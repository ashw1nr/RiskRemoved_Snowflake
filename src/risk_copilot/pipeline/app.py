"""``riskcopilot app`` -- render, run and deploy the Streamlit-in-Snowflake app.

The app under ``app/`` is a thin client: it reads tables and views and calls
procedures. It cannot import this package (Streamlit in Snowflake has only the
files we upload, and the trial account has no external access to install
anything), so every object name reaches it through ``app/app_config.json``,
rendered here from the same Settings the SQL stages use.

Deploy is plain SQL, no Snowflake CLI: ``PUT`` the files to a stage, then
``CREATE OR REPLACE STREAMLIT ... FROM`` that stage on the container runtime.

``reset-cases`` undoes the case workflow so the demo can be run again.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from risk_copilot.config import ConfigError, Schemas, Settings, validate_identifier
from risk_copilot.pipeline.verify import pin_path
from risk_copilot.session import execute

log = logging.getLogger(__name__)

APP_DIR = "app"
MAIN_FILE = "streamlit_app.py"
CONFIG_FILE = "app_config.json"
#: What gets uploaded. Anything else under app/ (caches, notes) stays local.
UPLOAD_SUFFIXES = frozenset({".py", ".toml", ".json", ".css", ".ttf", ".png", ".svg"})
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
#: Never uploaded, whatever the suffix: a local secrets file holds private keys,
#: and anything put on the app stage is readable by whoever can read the app.
_NEVER_UPLOAD = frozenset({"secrets.toml"})

CONFIRMED = "CONFIRMED_BY_DETECTION"
#: case_tools the app calls (the UDFs and data keys are not its business).
_APP_PROCEDURES = frozenset(
    {
        "gather_evidence",
        "draft_str",
        "review_str",
        "attest_str",
        "reset_cases",
        "sim_control",
        "generate_ai_str",
    }
)


class AppError(RuntimeError):
    """The app could not be rendered or deployed."""


def app_root(settings: Settings) -> Path:
    return settings.repo_root / APP_DIR


def _hero(settings: Settings) -> dict[str, Any] | None:
    """The verified hero, or ``None`` unless detection confirmed it.

    Read from the file ``verify`` writes, never from the generation pin: the
    app must only call a party the hero once Snowflake has proven it.
    """
    try:
        path = pin_path(settings, "demo_entity_verified_file")
    except KeyError:
        return None
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("status") != CONFIRMED or data.get("profile") != settings.profile:
        return None
    case_ids = sorted(
        {str(c) for rule in (data.get("rules") or {}).values() for c in rule.get("case_ids", [])}
    )
    return {"party_id": str(data["party_id"]), "case_ids": case_ids}


def app_config(settings: Settings) -> dict[str, Any]:
    """Every name the app needs, resolved for this profile.

    Tables are not listed: they are unsuffixed in every profile (as in the DDL),
    so the app qualifies them with ``schema``. Dynamic tables, views and
    procedures carry the profile suffix and are listed fully qualified.
    """
    return {
        "profile": settings.profile,
        "title": settings.app.title,
        "database": settings.db(),
        "schema": {key: settings.fq(key) for key in Schemas.model_fields},
        "dt": {
            key: settings.fq("curated", spec.name) for key, spec in settings.dynamic_tables.items()
        },
        "view": {key: settings.fq("curated", name) for key, name in settings.views.items()},
        "procedure": {
            "route_signals": settings.fq(
                "evidence", settings.streams_tasks.route_signals_procedure
            ),
            **{
                key: settings.fq("evidence", name)
                for key, name in settings.case_tools.items()
                if key in _APP_PROCEDURES
            },
        },
        "role": {"compliance_officer": settings.obj(settings.roles.compliance_officer)},
        "semantic_view": settings.fq("curated", settings.cortex.semantic_view),
        "hero": _hero(settings),
    }


def render_app_config(settings: Settings) -> Path:
    """Write ``app/app_config.json``; deterministic for the same configuration."""
    root = app_root(settings)
    if not (root / MAIN_FILE).is_file():
        raise AppError(f"No app at {root / MAIN_FILE}")
    out = root / CONFIG_FILE
    out.write_text(json.dumps(app_config(settings), indent=2, sort_keys=True) + "\n", "utf-8")
    return out


def app_files(root: Path) -> list[Path]:
    """Files to upload, relative to ``root``, in a stable order."""
    files = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or _SKIP_DIRS.intersection(rel.parts):
            continue
        if path.name.lower() in _NEVER_UPLOAD or path.name.lower().startswith("secrets.toml"):
            continue
        if path.suffix.lower() in UPLOAD_SUFFIXES:
            files.append(rel)
    if Path(MAIN_FILE) not in files:
        raise AppError(f"{MAIN_FILE} missing from {root}")
    return files


# --------------------------------------------------------------------------- #
# Deploy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AppDeployResult:
    fq_name: str
    stage: str
    compute_pool: str
    warehouse: str
    files: tuple[str, ...]
    url: str | None


def _first(rows: Sequence[Any], column: str) -> Any:
    return rows[0][column] if rows else None


class AppDeployer:
    """Upload ``app/`` and (re)create the STREAMLIT object, as the admin role."""

    def __init__(
        self, settings: Settings, session: Any, *, echo: Callable[[str], None] = log.info
    ) -> None:
        self.settings = settings
        self.session = session
        self.echo = echo
        spec = settings.app
        self.fq_name = settings.fq(spec.schema_key, spec.name)
        self.stage = settings.fq(spec.schema_key, spec.stage)
        self.warehouse = settings.obj(settings.warehouse(spec.warehouse).name)

    def sql(self, text: str, params: Sequence[Any] | None = None) -> list[Any]:
        return execute(self.session, text, params)

    def compute_pool(self) -> str:
        """The configured pool, else the account default; refused if not visible."""
        pool = self.settings.app.compute_pool
        if not pool:
            rows = self.sql("SHOW PARAMETERS LIKE 'DEFAULT_STREAMLIT_COMPUTE_POOL' IN ACCOUNT")
            pool = str(_first(rows, "value") or _first(rows, "default") or "")
        if not pool:
            raise AppError(
                "No compute pool: set conf/base.yml:app.compute_pool, or the account "
                "parameter DEFAULT_STREAMLIT_COMPUTE_POOL."
            )
        pool = validate_identifier(pool, what="Compute pool")
        visible = {str(r["name"]).upper() for r in self.sql("SHOW COMPUTE POOLS")}
        if pool not in visible:
            raise AppError(
                f"Compute pool {pool} is not visible to this role; visible: {sorted(visible)}"
            )
        return pool

    def upload(self, root: Path, files: Sequence[Path]) -> None:
        self.sql(
            f"CREATE STAGE IF NOT EXISTS {self.stage} "
            "COMMENT = 'Streamlit app source, replaced on every riskcopilot app deploy.'"
        )
        # A file deleted locally must not survive on the stage.
        self.sql(f"REMOVE @{self.stage}")
        for rel in files:
            target = f"@{self.stage}" + (f"/{rel.parent.as_posix()}" if rel.parent.parts else "")
            self.session.file.put(
                str((root / rel).resolve()), target, auto_compress=False, overwrite=True
            )
            self.echo(f"  put {rel.as_posix()}")

    def create(self, pool: str) -> None:
        spec = self.settings.app
        repo = (
            f"  ARTIFACT_REPOSITORIES = ({spec.artifact_repository})\n"
            if spec.artifact_repository
            else ""
        )
        self.sql(
            f"CREATE OR REPLACE STREAMLIT {self.fq_name}\n"
            f"  FROM '@{self.stage}'\n"
            f"  MAIN_FILE = '{MAIN_FILE}'\n"
            f"  QUERY_WAREHOUSE = {self.warehouse}\n"
            f"  RUNTIME_NAME = '{spec.runtime_name}'\n"
            f"  COMPUTE_POOL = {pool}\n"
            f"{repo}"
            f"  TITLE = '{spec.title}'\n"
            "  COMMENT = 'Risk & Compliance Copilot demo surface (riskcopilot app deploy).'"
        )
        # Without a live version only the owner, visiting in Snowsight, can open it.
        self.sql(f"ALTER STREAMLIT {self.fq_name} ADD LIVE VERSION FROM LAST")

    def confirm(self) -> str | None:
        """The object must exist after deploy; returns its Snowsight URL."""
        schema = self.settings.fq(self.settings.app.schema_key)
        name = self.settings.obj(self.settings.app.name)
        rows = self.sql(f"SHOW STREAMLITS LIKE '{name}' IN SCHEMA {schema}")
        if len(rows) != 1:
            raise AppError(f"Deploy reported success but SHOW STREAMLITS finds {len(rows)} rows")
        names = self.sql(
            "SELECT CURRENT_ORGANIZATION_NAME() AS org, CURRENT_ACCOUNT_NAME() AS acct"
        )
        if not names or not names[0]["ORG"]:
            return None
        org, acct = str(names[0]["ORG"]).lower(), str(names[0]["ACCT"]).lower()
        return f"https://app.snowflake.com/{org}/{acct}/#/streamlit-apps/{self.fq_name}"

    def run(self) -> AppDeployResult:
        root = app_root(self.settings)
        render_app_config(self.settings)
        files = app_files(root)
        pool = self.compute_pool()
        self.echo(f"Uploading {len(files)} files to @{self.stage}")
        self.upload(root, files)
        self.echo(f"Creating {self.fq_name} on {pool}, queries on {self.warehouse}")
        self.create(pool)
        url = self.confirm()
        return AppDeployResult(
            fq_name=self.fq_name,
            stage=self.stage,
            compute_pool=pool,
            warehouse=self.warehouse,
            files=tuple(f.as_posix() for f in files),
            url=url,
        )


def deploy_app(settings: Settings, session: Any, **kwargs: Any) -> AppDeployResult:
    try:
        return AppDeployer(settings, session, **kwargs).run()
    except ConfigError as exc:
        raise AppError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# Reset -- put every case back where the router left it, for a demo re-run
# --------------------------------------------------------------------------- #

#: What the case workflow writes, children first. Mirrors RESET_CASES.
WORKFLOW_TABLES = (
    "ATTESTATION",
    "REVIEW",
    "ARTIFACT",
    "FINDING",
    "EVIDENCE_QUERY_LOG",
    "EVIDENCE",
)


@dataclass(frozen=True)
class ResetResult:
    dry_run: bool
    rows: dict[str, int]
    case_ids: tuple[str, ...]

    @property
    def cases_reopened(self) -> int:
        return len(self.case_ids)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def reset_cases(settings: Settings, session: Any, *, dry_run: bool) -> ResetResult:
    """CALL the RESET_CASES procedure (sql/procedures/06_reset_cases.sql).

    The procedure is the one implementation: the app's Governance page, this
    CLI and a Snowsight worksheet all call it. With ``dry_run`` it only counts.
    """
    proc = settings.fq("evidence", settings.case_tools["reset_cases"])
    rows = execute(session, f"CALL {proc}(?, ?)", ["CLI", dry_run])
    raw = rows[0][0] if rows else None
    out = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(out, dict) or "rows" not in out:
        raise AppError(f"{proc} returned an unexpected result: {raw!r}")
    return ResetResult(
        dry_run=bool(out["dry_run"]),
        rows={t: int(out["rows"][t]) for t in WORKFLOW_TABLES},
        case_ids=tuple(str(c) for c in out.get("case_ids") or ()),
    )


# --------------------------------------------------------------------------- #
# Public access -- the service user Streamlit Community Cloud connects as
# --------------------------------------------------------------------------- #

#: Where the key pair and the secrets file go: outside the repository, always.
DEFAULT_KEY_DIR = Path.home() / ".snowflake" / "keys"
_B64 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


@dataclass(frozen=True)
class PublicAccess:
    user: str
    role: str
    warehouse: str
    key_file: Path
    secrets_file: Path
    key_created: bool
    checks: dict[str, Any]


def _key_pair(key_file: Path) -> tuple[bytes, str, bool]:
    """(PKCS8 PEM private key, base64 DER public key, created). Reuses an existing key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    created = not key_file.is_file()
    if created:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(pem)
    pem = key_file.read_bytes()
    private = serialization.load_pem_private_key(pem, password=None)
    der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64

    return pem, base64.b64encode(der).decode("ascii"), created


def secrets_toml(
    *,
    account: str,
    user: str,
    role: str,
    warehouse: str,
    database: str,
    pem: bytes,
    section: str = "snowflake",
    extra: str = "",
) -> str:
    """A secrets section lib/db.py reads on Streamlit Community Cloud ([snowflake] by default)."""
    for name, value in (
        ("account", account),
        ("user", user),
        ("role", role),
        ("warehouse", warehouse),
        ("database", database),
    ):
        if not value or '"' in value or "\n" in value:
            raise AppError(f"Refusing to write secrets: bad {name} {value!r}")
    return (
        "# Streamlit Community Cloud > your app > Settings > Secrets: paste this file.\n"
        "# It holds a private key. Never commit it; rotate with `riskcopilot app public-access`.\n"
        f"[{section}]\n"
        f'account = "{account}"\n'
        f'user = "{user}"\n'
        f'role = "{role}"\n'
        f'warehouse = "{warehouse}"\n'
        f'database = "{database}"\n'
        f'private_key_pem = """\n{pem.decode("ascii").strip()}\n"""\n'
        f"{extra}"
    )


def provision_public_access(
    settings: Settings,
    session: Any,
    *,
    key_dir: Path = DEFAULT_KEY_DIR,
    account: str,
    rotate: bool = False,
    officer: bool = False,
) -> PublicAccess:
    """Create (or update) the hosted app's service user and write its secrets file.

    The user is TYPE = SERVICE with only a public key: it cannot sign in with a
    password or interactively. Its role is granted by the app_access stage.

    ``officer`` does the same for the Principal Officer's user (its own key and
    secrets file, section ``[snowflake_officer]``), and adds the officer's
    sign-in password, generated once, so it is no longer shown on the page.
    """
    if officer:
        if not settings.app.public_officer_user or not settings.roles.public_officer:
            raise AppError(
                "Set conf/base.yml app.public_officer_user and roles.public_officer first."
            )
        user = settings.obj(settings.app.public_officer_user)
        role = settings.obj(settings.roles.public_officer)
    else:
        if not settings.app.public_user or not settings.roles.public_app:
            raise AppError("Set conf/base.yml app.public_user and roles.public_app first.")
        user = settings.obj(settings.app.public_user)
        role = settings.obj(settings.roles.public_app)
    warehouse = settings.obj(settings.warehouse(settings.app.warehouse).name)
    key_file = key_dir / f"{user.lower()}.p8"
    if rotate and key_file.is_file():
        key_file.unlink()
    pem, public_b64, created = _key_pair(key_file)
    if not set(public_b64) <= _B64:  # it is interpolated into ALTER USER below
        raise AppError("Public key is not base64; refusing to continue.")

    execute(session, "USE ROLE ACCOUNTADMIN")
    try:
        execute(
            session,
            f"CREATE USER IF NOT EXISTS {user} TYPE = SERVICE "
            "COMMENT = 'Streamlit Community Cloud demo app. Key pair only.'",
        )
        execute(
            session,
            f"ALTER USER {user} SET RSA_PUBLIC_KEY = '{public_b64}' "
            f"DEFAULT_ROLE = {role} DEFAULT_WAREHOUSE = {warehouse}",
        )
        execute(session, f"GRANT ROLE {role} TO USER {user}")
    finally:
        execute(session, f"USE ROLE {settings.obj(settings.roles.admin)}")

    secrets_file = key_dir / f"{user.lower()}.streamlit-secrets.toml"
    extra = ""
    if officer:
        password = _officer_password(secrets_file)
        extra = f'\n[demo_auth]\nofficer_password = "{password}"\n'
    secrets_file.write_text(
        secrets_toml(
            account=account,
            user=user,
            role=role,
            warehouse=warehouse,
            database=settings.db(),
            pem=pem,
            section="snowflake_officer" if officer else "snowflake",
            extra=extra,
        ),
        encoding="utf-8",
    )
    return PublicAccess(user, role, warehouse, key_file, secrets_file, created, checks={})


def _officer_password(secrets_file: Path) -> str:
    """The officer's sign-in password: kept from an earlier secrets file, else new."""
    import secrets
    import tomllib

    if secrets_file.is_file():
        old = tomllib.loads(secrets_file.read_text("utf-8")).get("demo_auth", {})
        if isinstance(old.get("officer_password"), str) and old["officer_password"]:
            return str(old["officer_password"])
    return secrets.token_urlsafe(12)  # URL-safe: no quote or backslash to escape in TOML


def check_public_access(
    settings: Settings, secrets_file: Path, section: str = "snowflake"
) -> dict[str, Any]:
    """Sign in exactly as the hosted app will, and run what its pages run."""
    import tomllib

    from cryptography.hazmat.primitives import serialization
    from snowflake.snowpark import Session

    cfg = dict(tomllib.loads(secrets_file.read_text("utf-8"))[section])
    key = serialization.load_pem_private_key(cfg.pop("private_key_pem").encode(), password=None)
    cfg["private_key"] = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cfg["authenticator"] = "SNOWFLAKE_JWT"
    s = Session.builder.configs(cfg).create()
    try:
        ev = settings.fq("evidence")
        who = s.sql("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()").collect()[0]
        cases = s.sql(f"SELECT COUNT(*) FROM {ev}.CASE_RECORD").collect()[0][0]
        scores = s.sql(
            f"SELECT COUNT(*) FROM {settings.fq('curated', settings.views['eval_scores'])}"
        ).collect()[0][0]
        proc = settings.fq("evidence", settings.case_tools["reset_cases"])
        dry = s.sql(f"CALL {proc}('CHECK', TRUE)").collect()[0][0]
        try:
            s.sql(f"SELECT COUNT(*) FROM {settings.fq('ground_truth')}.TXN_LABELS").collect()
            labels = "READABLE"
        except Exception:
            labels = "refused"
        return {
            "user": who[0],
            "role": who[1],
            "warehouse": who[2],
            "cases": int(cases),
            "eval_runs": int(scores),
            "reset_dry_run": "ok" if dry else "empty",
            "ground_truth_labels": labels,
        }
    finally:
        s.close()
