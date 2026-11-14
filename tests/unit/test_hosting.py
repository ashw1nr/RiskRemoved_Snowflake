"""Hosting the app on Streamlit Community Cloud, and the demo sign-in.

The hosted app runs from the repository as committed, so what it reads there
(app_config.json, requirements.txt, the theme) must not drift from conf/ and
from what Streamlit in Snowflake installs. Its service role must be able to
run every case tool and nothing on GROUND_TRUTH.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_copilot.config import load_settings
from risk_copilot.pipeline.app import AppError, app_config, secrets_toml
from risk_copilot.pipeline.deploy import Deployer, split_statements


def test_the_committed_app_config_matches_conf(repo_root: Path) -> None:
    # Community Cloud cannot run `riskcopilot app render`: it reads the file in git.
    committed = json.loads((repo_root / "app" / "app_config.json").read_text("utf-8"))
    assert committed == app_config(load_settings(repo_root=repo_root, profile="dev")), (
        "app/app_config.json is stale: run `riskcopilot -p dev app render` and commit it"
    )


def test_requirements_match_the_streamlit_in_snowflake_dependencies(repo_root: Path) -> None:
    project = tomllib.loads((repo_root / "app" / "pyproject.toml").read_text("utf-8"))
    lines = (repo_root / "app" / "requirements.txt").read_text("utf-8").splitlines()
    requirements = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    assert requirements == project["project"]["dependencies"]


def test_the_root_theme_is_the_app_theme(repo_root: Path) -> None:
    # Streamlit reads .streamlit/ from the working directory: the repository root on
    # Community Cloud, app/ for `riskcopilot app run` and in Snowflake.
    root = (repo_root / ".streamlit" / "config.toml").read_text("utf-8")
    app = (repo_root / "app" / ".streamlit" / "config.toml").read_text("utf-8")
    assert tomllib.loads(root) == tomllib.loads(app)


def test_secrets_are_never_committable(repo_root: Path) -> None:
    ignored = (repo_root / ".gitignore").read_text("utf-8").splitlines()
    assert {"secrets.toml", "*.streamlit-secrets.toml", "*.p8"} <= set(ignored)


def test_every_app_file_compiles(repo_root: Path) -> None:
    # The app is never imported by the suite (it needs a live session), so a
    # syntax error would otherwise first show up on the hosted URL.
    for path in (repo_root / "app").rglob("*.py"):
        compile(path.read_text("utf-8"), str(path), "exec")


# --------------------------------------------------------------------------- #
# The public app role
# --------------------------------------------------------------------------- #


def _signatures(sql: str) -> dict[str, tuple[str, ...]]:
    """Procedure name -> argument types, from CREATE PROCEDURE statements."""
    out: dict[str, tuple[str, ...]] = {}
    for m in re.finditer(
        r"CREATE OR REPLACE PROCEDURE\s+([\w.]+)\s*\((.*?)\)\s*(?:COPY GRANTS\s+)?RETURNS",
        sql,
        re.S,
    ):
        args = [a.split()[1].upper() for a in m.group(2).split(",") if a.strip()]
        out[m.group(1).upper()] = tuple(args)
    return out


def test_the_public_role_can_run_every_case_tool(repo_root: Path) -> None:
    deployer = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    procs: dict[str, tuple[str, ...]] = {}
    for path in deployer.stage_files("procedures"):
        procs |= _signatures(deployer.render(path))
    grants = {}
    for path in deployer.stage_files("app_access"):
        for m in re.finditer(
            r"GRANT USAGE ON PROCEDURE ([\w.]+)\(([^)]*)\)", deployer.render(path)
        ):
            grants[m.group(1).upper()] = tuple(t.strip().upper() for t in m.group(2).split(","))
    assert grants, "the app_access stage grants no procedure"
    # Each grant names a real signature; a mismatch fails only at deploy time otherwise.
    for name, types in grants.items():
        assert procs.get(name) == types, f"{name}{types} does not match {procs.get(name)}"
    # SUBMIT_AI_DRAFT is called by the agent bridge (src/risk_copilot/agentic), never by
    # the public app: the public role must not be able to write a narrative.
    bridge_only = {
        deployer.settings.fq("evidence", deployer.settings.case_tools["submit_ai_draft"]).upper()
    }
    tools = {
        deployer.settings.fq("evidence", t).upper() for t in deployer.settings.case_tools.values()
    } - bridge_only
    assert tools & set(procs) <= set(grants), "a case tool the app calls is not granted"
    assert not bridge_only & set(grants), "the public role must not be granted the write-back"


def test_the_public_role_gets_nothing_on_ground_truth(repo_root: Path) -> None:
    deployer = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    gt = deployer.settings.fq("ground_truth").upper()
    for path in deployer.stage_files("app_access"):
        for statement in split_statements(deployer.render(path)):
            if statement.lstrip().upper().startswith("GRANT"):
                assert gt not in statement.upper(), statement


def test_redeploying_a_procedure_keeps_the_public_grants(repo_root: Path) -> None:
    # CREATE OR REPLACE without COPY GRANTS drops USAGE, so the next `procedures`
    # deploy silently broke the hosted app ("Unknown user-defined function").
    deployer = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    for path in deployer.stage_files("procedures"):
        for statement in split_statements(deployer.render(path)):
            if re.match(r"\s*CREATE OR REPLACE PROCEDURE", statement, re.I):
                assert re.search(r"\)\s*COPY GRANTS\s+RETURNS", statement), path.name


def test_only_the_officer_role_can_sign_on_the_hosted_app(repo_root: Path) -> None:
    deployer = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    roles = deployer.settings.sql_context()["role"]
    pub, off, co = roles["public_app"], roles["public_officer"], roles["compliance_officer"]
    sql = "\n".join(deployer.render(p) for p in deployer.stage_files("app_access")).upper()
    assert f"GRANT ROLE {co} TO ROLE {pub};" not in sql
    assert f"REVOKE ROLE {co} FROM ROLE {pub};" in sql
    assert f"GRANT ROLE {co} TO ROLE {off};" in sql and f"GRANT ROLE {pub} TO ROLE {off};" in sql


def test_secrets_refuse_values_that_would_break_the_file() -> None:
    good = {"account": "A", "user": "U", "role": "R", "warehouse": "W", "database": "D"}
    text = secrets_toml(**good, pem=b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")
    parsed = tomllib.loads(text)["snowflake"]
    assert parsed["user"] == "U" and parsed["private_key_pem"].strip().startswith("-----BEGIN")
    with pytest.raises(AppError, match="bad role"):
        secrets_toml(**{**good, "role": 'R"\nx = "y'}, pem=b"k")


def test_the_officer_secrets_are_a_separate_section() -> None:
    good = {"account": "A", "user": "U", "role": "R", "warehouse": "W", "database": "D"}
    text = secrets_toml(
        **good,
        pem=b"k",
        section="snowflake_officer",
        extra='\n[demo_auth]\nofficer_password = "p"\n',
    )
    parsed = tomllib.loads(text)
    assert set(parsed) == {"snowflake_officer", "demo_auth"}  # pasted beside [snowflake]


# --------------------------------------------------------------------------- #
# Demo sign-in (app/lib/auth.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def auth(repo_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.syspath_prepend(str(repo_root / "app"))
    from lib import auth as module

    monkeypatch.setattr(module, "st", SimpleNamespace(session_state={}))
    return module


def test_maker_and_checker_are_different_people(auth) -> None:  # type: ignore[no-untyped-def]
    makers = {k for k, p in auth.PERSONAS.items() if p["can_investigate"]}
    checkers = {k for k, p in auth.PERSONAS.items() if p["can_attest"]}
    assert makers and checkers and not makers & checkers


def test_sign_in_checks_the_password(auth) -> None:  # type: ignore[no-untyped-def]
    assert not auth.sign_in("analyst", "wrong")
    assert auth.current() is None
    assert auth.sign_in("analyst", auth.DEMO_PASSWORDS["analyst"])
    assert auth.can("can_investigate") and not auth.can("can_attest")
    auth.sign_out(prefill="officer")
    assert auth.current() is None
    assert auth.st.session_state["login_user"] == "officer"
    assert auth.sign_in("officer", auth.st.session_state["login_password"])
    assert auth.can("can_attest")


def test_a_password_in_secrets_replaces_the_shown_one(auth) -> None:  # type: ignore[no-untyped-def]
    auth.st.secrets = {"demo_auth": {"officer_password": "kept-secret"}}
    assert auth.hidden("officer") and not auth.hidden("analyst")
    assert not auth.sign_in("officer", auth.DEMO_PASSWORDS["officer"])
    auth.fill("officer")
    assert auth.st.session_state["login_password"] == ""  # not filled in for anyone
    assert auth.sign_in("officer", "kept-secret")
    assert auth.sign_in("analyst", auth.DEMO_PASSWORDS["analyst"])


def test_an_officer_only_url_offers_only_the_officer(auth) -> None:  # type: ignore[no-untyped-def]
    auth.st.secrets = {
        "snowflake_officer": {"user": "O"},
        "demo_auth": {"officer_password": "kept-secret", "show_officer_password": True},
    }
    assert auth.available() == ["officer"]
    assert not auth.sign_in("analyst", auth.DEMO_PASSWORDS["analyst"])
    assert not auth.hidden("officer")  # shown on purpose on this URL
    auth.fill("officer")
    assert auth.st.session_state["login_password"] == "kept-secret"
    assert auth.sign_in("officer", auth.st.session_state["login_password"])


# --------------------------------------------------------------------------- #
# Visual helpers (app/lib/ui.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def ui(repo_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.syspath_prepend(str(repo_root / "app"))
    from lib import ui as module

    drawn: list[str] = []
    monkeypatch.setattr(
        module,
        "st",
        SimpleNamespace(
            metric=lambda *a, **k: drawn.append("metric"),
            altair_chart=lambda chart, **k: drawn.append(type(chart).__name__),
        ),
    )
    module.drawn = drawn
    return module


@pytest.mark.parametrize(
    ("column", "heading"),
    [
        ("case_id", "Case ID"),
        ("flagged_inr", "Flagged (\u20b9)"),
        ("CASE_BRANCH_REGION", "Case branch region"),
        ("pep_flag", "PEP flag"),
        ("latency_ms", "Latency (ms)"),
    ],
)
def test_headings_are_human(ui, column: str, heading: str) -> None:  # type: ignore[no-untyped-def]
    assert ui.label(column) == heading


def test_auto_chart_picks_by_shape(ui) -> None:  # type: ignore[no-untyped-def]
    import pandas as pd

    assert ui.auto_chart(pd.DataFrame({"N": [7]}))
    assert ui.auto_chart(pd.DataFrame({"BRANCH": ["A", "B"], "CASES": [5, 2]}))
    assert ui.auto_chart(pd.DataFrame({"DAY": ["2022-09-03", "2022-09-04"], "N": [1, 3]}))
    assert ui.drawn[0] == "metric" and len(ui.drawn) == 3
    # No obvious chart: two measures, text only, or a single row of categories.
    assert not ui.auto_chart(pd.DataFrame({"A": ["x", "y"], "B": [1, 2], "C": [3, 4]}))
    assert not ui.auto_chart(pd.DataFrame({"A": ["x", "y"], "B": ["p", "q"]}))
    assert not ui.auto_chart(pd.DataFrame({"A": ["x"], "B": [1]}))
    assert not ui.auto_chart(pd.DataFrame())


# --------------------------------------------------------------------------- #
# Session expiry (app/lib/db.py): reconnect once, never retry anything else
# --------------------------------------------------------------------------- #


class _Sessions:
    """Stands in for the cached ``session()``: a new object after each clear."""

    def __init__(self) -> None:
        self.made = 0
        self.cleared = 0

    def __call__(self) -> int:
        self.made += 1
        return self.cleared

    def clear(self) -> None:
        self.cleared += 1


@pytest.fixture
def db(repo_root: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.syspath_prepend(str(repo_root / "app"))
    from lib import db as module

    fake = _Sessions()
    monkeypatch.setattr(module, "session", fake)
    module.fake = fake
    return module


def test_an_expired_session_reconnects_once(db) -> None:  # type: ignore[no-untyped-def]
    def fn(generation: int) -> str:
        if generation == 0:
            raise RuntimeError("(1304): 390114 (08001): Authentication token has expired.")
        return "ok"

    assert db.run(fn) == "ok"
    assert db.fake.cleared == 1


def test_other_errors_are_not_retried(db) -> None:  # type: ignore[no-untyped-def]
    calls = []

    def fn(generation: int) -> None:
        calls.append(generation)
        raise RuntimeError("002003 (42S02): Object does not exist or not authorized.")

    with pytest.raises(RuntimeError, match="002003"):
        db.run(fn)
    assert calls == [0] and db.fake.cleared == 0


def test_a_second_expiry_is_raised(db) -> None:  # type: ignore[no-untyped-def]
    def fn(generation: int) -> None:
        raise RuntimeError("390112: Your session no longer exists.")

    with pytest.raises(RuntimeError, match="390112"):
        db.run(fn)
    assert db.fake.cleared == 1
