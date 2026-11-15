"""``riskcopilot app``: config rendering, file selection, the SQL deploy loop and reset.

No connection: deploy and reset run against recording fake sessions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from risk_copilot.config import ConfigError, Settings, load_settings
from risk_copilot.pipeline.app import (
    CONFIG_FILE,
    MAIN_FILE,
    WORKFLOW_TABLES,
    AppDeployer,
    AppError,
    app_config,
    app_files,
    render_app_config,
    reset_cases,
)

VERIFIED = "conf/generated/demo_entity.{profile}.verified.yml"


def _settings_at(repo_root: Path, tmp_path: Path, profile: str = "dev") -> Settings:
    """Real configuration, but every file the app code touches lives under tmp_path."""
    settings = load_settings(repo_root=repo_root, profile=profile)
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "app" / MAIN_FILE).write_text("import streamlit\n", "utf-8")
    return settings.model_copy(update={"repo_root": tmp_path})


def _write_verified(tmp_path: Path, profile: str, status: str) -> None:
    path = tmp_path / VERIFIED.format(profile=profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"party_id: P-1\nprofile: {profile}\nstatus: {status}\n"
        "rules:\n  R1:\n    case_ids: [CASE-b, CASE-a]\n  R2:\n    case_ids: [CASE-a]\n",
        "utf-8",
    )


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_dev_names_carry_the_suffix_and_tables_are_qualified_by_schema(
    repo_root: Path, tmp_path: Path
) -> None:
    cfg = app_config(_settings_at(repo_root, tmp_path))
    assert cfg["database"] == "RISK_COPILOT_DEV"
    assert cfg["schema"]["evidence"] == "RISK_COPILOT_DEV.EVIDENCE_CHAIN"
    assert cfg["dt"]["signals_fast"] == "RISK_COPILOT_DEV.CURATED.DT_SIGNALS_FAST_DEV"
    assert cfg["view"]["transaction_pairs"] == "RISK_COPILOT_DEV.CURATED.V_TRANSACTION_PAIRS_DEV"
    assert cfg["procedure"]["route_signals"] == "RISK_COPILOT_DEV.EVIDENCE_CHAIN.ROUTE_SIGNALS_DEV"
    assert cfg["procedure"]["attest_str"] == "RISK_COPILOT_DEV.EVIDENCE_CHAIN.ATTEST_STR_DEV"
    assert set(cfg["procedure"]) == {
        "route_signals",
        "gather_evidence",
        "draft_str",
        "review_str",
        "attest_str",
        "reset_cases",
        "sim_control",
        "generate_ai_str",
    }
    assert cfg["role"]["compliance_officer"] == "COMPLIANCE_OFFICER_DEV"
    assert cfg["semantic_view"] == "RISK_COPILOT_DEV.CURATED.SV_RISK_COPILOT_DEV"


def test_demo_names_are_unsuffixed(repo_root: Path, tmp_path: Path) -> None:
    cfg = app_config(_settings_at(repo_root, tmp_path, "demo"))
    assert cfg["database"] == "RISK_COPILOT"
    assert cfg["dt"]["signals_fast"] == "RISK_COPILOT.CURATED.DT_SIGNALS_FAST"


def test_hero_only_once_detection_confirmed_it(repo_root: Path, tmp_path: Path) -> None:
    settings = _settings_at(repo_root, tmp_path)
    assert app_config(settings)["hero"] is None
    _write_verified(tmp_path, "dev", "REJECTED_NOT_DETECTED")
    assert app_config(settings)["hero"] is None
    _write_verified(tmp_path, "dev", "CONFIRMED_BY_DETECTION")
    assert app_config(settings)["hero"] == {"party_id": "P-1", "case_ids": ["CASE-a", "CASE-b"]}


def test_render_is_deterministic(repo_root: Path, tmp_path: Path) -> None:
    settings = _settings_at(repo_root, tmp_path)
    first = render_app_config(settings).read_bytes()
    assert render_app_config(settings).read_bytes() == first
    assert json.loads(first)["profile"] == "dev"


def test_render_refuses_without_an_app(repo_root: Path, tmp_path: Path) -> None:
    settings = load_settings(repo_root=repo_root, profile="dev").model_copy(
        update={"repo_root": tmp_path}
    )
    with pytest.raises(AppError, match=MAIN_FILE):
        render_app_config(settings)


def test_app_warehouse_must_be_a_configured_key(repo_root: Path) -> None:
    with pytest.raises(ConfigError, match=r"app\.warehouse"):
        Settings.load(repo_root=repo_root, profile="dev", overrides={"app": {"warehouse": "nope"}})


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"title": "It's here"}, "app.title"),
        ({"schema": "nowhere"}, "app.schema"),
        ({"runtime_name": "python3"}, "runtime_name"),
        ({"compute_pool": "bad pool"}, "Compute pool"),
    ],
)
def test_unsafe_app_values_are_rejected(
    repo_root: Path, override: dict[str, str], message: str
) -> None:
    with pytest.raises((ConfigError, ValueError), match=message):
        Settings.load(repo_root=repo_root, profile="dev", overrides={"app": override})


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #


def test_app_files_selects_sources_and_skips_caches(tmp_path: Path) -> None:
    for rel in [
        MAIN_FILE,
        "lib/db.py",
        ".streamlit/config.toml",
        ".streamlit/secrets.toml",  # private keys: never on the app stage
        CONFIG_FILE,
        "lib/__pycache__/db.cpython-312.pyc",
        "notes.txt",
    ]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", "utf-8")
    assert [p.as_posix() for p in app_files(tmp_path)] == [
        ".streamlit/config.toml",
        CONFIG_FILE,
        "lib/db.py",
        MAIN_FILE,
    ]


def test_the_repository_app_has_its_entry_point_and_pages(repo_root: Path) -> None:
    files = {p.as_posix() for p in app_files(repo_root / "app")}
    assert {
        MAIN_FILE,
        "lib/db.py",
        "lib/data.py",
        "app_pages/overview.py",
        "app_pages/case.py",
        "app_pages/signoff.py",
        "app_pages/governance.py",
        "lib/str_view.py",
        "lib/analyst.py",
        "app_pages/ask.py",
        "app_pages/liquidity.py",
    } <= files


# --------------------------------------------------------------------------- #
# Deploy
# --------------------------------------------------------------------------- #


class _Session:
    def __init__(self, *, default_pool: str = "POOL_A", pools: tuple[str, ...] = ("POOL_A",)):
        self.statements: list[str] = []
        self.puts: list[tuple[str, str]] = []
        self.default_pool, self.pools = default_pool, pools
        self.file = SimpleNamespace(put=self._put)

    def _put(self, local: str, target: str, **kwargs: Any) -> list[Any]:
        assert kwargs == {"auto_compress": False, "overwrite": True}
        self.puts.append((Path(local).name, target))
        return [SimpleNamespace(target=Path(local).name)]

    def sql(self, text: str, params: Any = None) -> Any:
        self.statements.append(" ".join(text.split()))
        rows: list[dict[str, Any]] = []
        if text.startswith("SHOW PARAMETERS"):
            rows = [{"value": self.default_pool, "default": ""}]
        elif text.startswith("SHOW COMPUTE POOLS"):
            rows = [{"name": p} for p in self.pools]
        elif text.startswith("SHOW STREAMLITS"):
            rows = [{"name": "RISK_COPILOT_APP_DEV"}]
        elif text.startswith("SELECT CURRENT_ORGANIZATION_NAME"):
            rows = [{"ORG": "MYORG", "ACCT": "MYACCT"}]
        return SimpleNamespace(collect=lambda: rows)


def test_deploy_uploads_then_creates_on_the_default_pool(repo_root: Path, tmp_path: Path) -> None:
    settings = _settings_at(repo_root, tmp_path)
    (tmp_path / "app" / "lib").mkdir()
    (tmp_path / "app" / "lib" / "db.py").write_text("x", "utf-8")
    session = _Session()
    result = AppDeployer(settings, session, echo=lambda _: None).run()

    stage = "RISK_COPILOT_DEV.EVIDENCE_CHAIN.APP_STAGE_DEV"
    app = "RISK_COPILOT_DEV.EVIDENCE_CHAIN.RISK_COPILOT_APP_DEV"
    s = session.statements
    create_stage = next(
        i for i, x in enumerate(s) if x.startswith(f"CREATE STAGE IF NOT EXISTS {stage}")
    )
    assert s[create_stage + 1] == f"REMOVE @{stage}"
    create = next(x for x in s if x.startswith("CREATE OR REPLACE STREAMLIT"))
    assert create.startswith(
        f"CREATE OR REPLACE STREAMLIT {app} FROM '@{stage}' MAIN_FILE = '{MAIN_FILE}'"
    )
    assert "QUERY_WAREHOUSE = RC_DEMO_WH_DEV" in create
    assert "RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11' COMPUTE_POOL = POOL_A" in create
    # Packages come from Snowflake's PyPI mirror: the trial has no external access.
    assert "ARTIFACT_REPOSITORIES = (SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY)" in create
    assert s.index(create) < s.index(f"ALTER STREAMLIT {app} ADD LIVE VERSION FROM LAST")
    assert sorted(session.puts) == sorted(
        [(CONFIG_FILE, f"@{stage}"), ("db.py", f"@{stage}/lib"), (MAIN_FILE, f"@{stage}")]
    )
    assert result.url == f"https://app.snowflake.com/myorg/myacct/#/streamlit-apps/{app}"
    assert result.compute_pool == "POOL_A"


def test_deploy_refuses_a_pool_the_role_cannot_see(repo_root: Path, tmp_path: Path) -> None:
    session = _Session(default_pool="HIDDEN", pools=("POOL_A",))
    with pytest.raises(AppError, match="HIDDEN"):
        AppDeployer(_settings_at(repo_root, tmp_path), session, echo=lambda _: None).run()
    assert not any(x.startswith("CREATE OR REPLACE STREAMLIT") for x in session.statements)


def test_configured_pool_wins_over_the_account_default(repo_root: Path, tmp_path: Path) -> None:
    settings = _settings_at(repo_root, tmp_path)
    settings = settings.model_copy(
        update={"app": settings.app.model_copy(update={"compute_pool": "POOL_B"})}
    )
    session = _Session(default_pool="POOL_A", pools=("POOL_A", "POOL_B"))
    assert AppDeployer(settings, session, echo=lambda _: None).compute_pool() == "POOL_B"
    assert not any(x.startswith("SHOW PARAMETERS") for x in session.statements)


def test_the_app_declares_streamlit_and_its_imports(repo_root: Path) -> None:
    # The container runtime will not start without a dependency file ("Installing
    # dependencies failed because the pyproject.toml file does not exist"), and it
    # installs exactly what is listed: an empty list failed with "Failed to get the
    # version of the Streamlit library ... >=1.48.0".
    import tomllib

    files = {p.as_posix() for p in app_files(repo_root / "app")}
    assert "pyproject.toml" in files
    project = tomllib.loads((repo_root / "app" / "pyproject.toml").read_text("utf-8"))["project"]
    names = {re.split(r"[\[<>=]", d)[0] for d in project["dependencies"]}
    assert names == {"streamlit", "pandas", "altair"}
    assert any(d.startswith("streamlit[snowflake]") for d in project["dependencies"])


def test_artifact_repository_must_be_fully_qualified(repo_root: Path) -> None:
    with pytest.raises((ConfigError, ValueError), match="artifact_repository"):
        Settings.load(
            repo_root=repo_root, profile="dev", overrides={"app": {"artifact_repository": "PYPI"}}
        )


# --------------------------------------------------------------------------- #
# Reset
# --------------------------------------------------------------------------- #


class _CallSession:
    """Answers one CALL with a canned RESET_CASES result, recording the binds."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, Any]] = []

    def sql(self, text: str, params: Any = None) -> Any:
        self.calls.append((" ".join(text.split()), params))
        return SimpleNamespace(collect=lambda: [(self.result,)])


def _proc_result(*, dry_run: bool) -> str:
    rows = dict.fromkeys(WORKFLOW_TABLES, 2)
    return json.dumps(
        {"dry_run": dry_run, "rows": rows, "cases_reopened": 2, "case_ids": ["CASE-a", "CASE-b"]}
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_reset_calls_the_one_procedure(repo_root: Path, dry_run: bool) -> None:
    settings = load_settings(repo_root=repo_root, profile="dev")
    session = _CallSession(_proc_result(dry_run=dry_run))
    result = reset_cases(settings, session, dry_run=dry_run)

    # The CLI never writes SQL of its own: it CALLs what the app's button calls.
    assert session.calls == [
        ("CALL RISK_COPILOT_DEV.EVIDENCE_CHAIN.RESET_CASES_DEV(?, ?)", ["CLI", dry_run])
    ]
    assert result.dry_run is dry_run
    assert result.rows == dict.fromkeys(WORKFLOW_TABLES, 2)
    assert result.case_ids == ("CASE-a", "CASE-b")
    assert result.total_rows == 12 and result.cases_reopened == 2


def test_an_unexpected_procedure_result_is_refused(repo_root: Path) -> None:
    settings = load_settings(repo_root=repo_root, profile="dev")
    with pytest.raises(AppError, match="unexpected result"):
        reset_cases(settings, _CallSession("null"), dry_run=True)


def test_the_app_is_given_the_reset_procedure(repo_root: Path, tmp_path: Path) -> None:
    cfg = app_config(_settings_at(repo_root, tmp_path))
    assert cfg["procedure"]["reset_cases"] == "RISK_COPILOT_DEV.EVIDENCE_CHAIN.RESET_CASES_DEV"
