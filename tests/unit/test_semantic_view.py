"""SV_RISK_COPILOT (sql/07_semantic): what Cortex Analyst may and may not see."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from risk_copilot.config import load_settings
from risk_copilot.pipeline.deploy import Deployer, split_statements


def _view(repo_root: Path, profile: str = "dev") -> str:
    """The CREATE statement without its comment lines (the header names what is excluded)."""
    d = Deployer(load_settings(repo_root=repo_root, profile=profile))
    (path,) = [p for p in d.stage_files("semantic") if p.name == "01_semantic_view.sql"]
    (statement,) = split_statements(d.render(path))
    return "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--"))


def test_regulatory_search_is_built_from_the_governed_clauses(repo_root: Path) -> None:
    d = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    (path,) = [p for p in d.stage_files("semantic") if p.name == "02_regulatory_search.sql"]
    sql = d.render(path)
    assert "CORTEX SEARCH SERVICE RISK_COPILOT_DEV.GOVERNANCE.REGULATORY_SEARCH_SVC_DEV" in sql
    assert "FROM RISK_COPILOT_DEV.GOVERNANCE.V_POLICY_CLAUSE_CURRENT" in sql
    assert "GROUND_TRUTH" not in sql.upper()


@pytest.mark.parametrize("profile", ["dev", "demo"])
def test_the_view_is_named_from_config(repo_root: Path, profile: str) -> None:
    suffix = "_DEV" if profile == "dev" else ""
    db = f"RISK_COPILOT{suffix}"
    assert f"SEMANTIC VIEW {db}.CURATED.SV_RISK_COPILOT{suffix}" in _view(repo_root, profile)


def test_the_answer_key_is_unreachable(repo_root: Path) -> None:
    # The label-leakage rule: nothing an analyst or a model can query reaches GROUND_TRUTH.
    view = _view(repo_root)
    assert "GROUND_TRUTH" not in view
    assert not re.search(r"is_laundering|TXN_LABELS|EVAL_", view, re.IGNORECASE)


def test_identity_numbers_and_free_text_are_not_exposed(repo_root: Path) -> None:
    # No masking policies on Standard edition, so these simply are not in the view.
    view = _view(repo_root).lower()
    for column in ("pan_number", "aadhaar", "phone_number", "kyc_note", "source_ref"):
        assert column not in view, column
