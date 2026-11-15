"""The case tools (sql/procedures): offline rendering and the invariants REVIEW_STR relies on."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from risk_copilot.config import load_settings
from risk_copilot.pipeline.deploy import Deployer, split_statements


def _rendered(repo_root: Path, profile: str) -> dict[str, str]:
    d = Deployer(load_settings(repo_root=repo_root, profile=profile))
    return {f.name: d.render(f) for f in d.stage_files("procedures")}


@pytest.mark.parametrize("profile", ["dev", "demo"])
def test_procedures_stage_renders_one_object_per_statement(repo_root: Path, profile: str) -> None:
    files = _rendered(repo_root, profile)
    assert sorted(files) == [
        "01_case_tools_common.sql",
        "02_gather_evidence.sql",
        "03_draft_str.sql",
        "04_review_str.sql",
        "05_attest_str.sql",
        "06_reset_cases.sql",
        "07_submit_ai_draft.sql",
        "08_simulator.sql",
        "09_generate_ai_str.sql",
    ]
    # 08: the procedure, then SUSPEND and CREATE OR ALTER of its task.
    assert [len(split_statements(s)) for s in files.values()] == [3, 1, 1, 1, 1, 1, 1, 3, 1]


def test_ai_str_runs_the_agent_tools_inside_snowflake_with_a_spend_cap(repo_root: Path) -> None:
    sql = _rendered(repo_root, "dev")["09_generate_ai_str.sql"]
    assert "PROCEDURE RISK_COPILOT_DEV.EVIDENCE_CHAIN.GENERATE_AI_STR_DEV(CASE_ID VARCHAR)" in sql
    assert "COPY GRANTS" in sql and "EXECUTE AS OWNER" in sql
    # Names and the cap come from conf, rendered once into the handler's CFG.
    assert '"agent": "RISK_COPILOT_AGENT.STR"' in sql
    assert '"submit": "RISK_COPILOT_DEV.EVIDENCE_CHAIN.SUBMIT_AI_DRAFT_DEV"' in sql
    assert '"cooldown_minutes": 5' in sql and '"daily_runs": 30' in sql
    # The agent's tools, in the agent's order, and the deterministic Reviewer last.
    for tool in (
        "PUT_EVIDENCE_PACKAGE",
        "DRAFT_STR_NARRATIVE",
        "REVIEW_STR_DRAFT",
        "RENDER_STR_PDF",
    ):
        assert tool in sql
    assert 'CFG["review"]' in sql and "GROUND_TRUTH" not in sql.upper()


def test_simulator_holds_back_by_booking_time_and_never_reads_labels(repo_root: Path) -> None:
    sql = _rendered(repo_root, "dev")["08_simulator.sql"]
    assert "PROCEDURE RISK_COPILOT_DEV.EVIDENCE_CHAIN.SIM_CONTROL_DEV(" in sql
    assert "TASK RISK_COPILOT_DEV.EVIDENCE_CHAIN.SIM_TICK_TASK_DEV" in sql
    assert "SET held_back = TRUE WHERE txn_timestamp >= :cut" in sql
    # The cut comes from replay.hold_back_from (dev.yml), not a literal in the file.
    assert "cut := '2022-09-03 00:00:00'::TIMESTAMP_NTZ;" in sql
    assert "GROUND_TRUTH" not in sql and "TXN_LABELS" not in sql
    # Every change is refreshed and routed in the same call, so the app sees it.
    assert "CALL RISK_COPILOT_DEV.EVIDENCE_CHAIN.ROUTE_SIGNALS_DEV(:full_scan)" in sql


def test_detection_reads_only_arrived_legs(repo_root: Path) -> None:
    d = Deployer(load_settings(repo_root=repo_root, profile="dev"))
    enriched = next(f for f in d.stage_files("dynamic_tables") if "transactions_enriched" in f.name)
    sql = d.render(enriched)
    assert sql.rstrip().endswith("WHERE NOT t.held_back;")
    assert "COALESCE(t.released_at, t.ingested_at)      AS ingested_at" in sql


def test_tool_names_carry_the_profile_suffix(repo_root: Path) -> None:
    dev = _rendered(repo_root, "dev")
    assert (
        "PROCEDURE RISK_COPILOT_DEV.EVIDENCE_CHAIN.GATHER_EVIDENCE_DEV("
        in dev["02_gather_evidence.sql"]
    )
    assert "PROCEDURE RISK_COPILOT_DEV.EVIDENCE_CHAIN.DRAFT_STR_DEV(" in dev["03_draft_str.sql"]
    # DRAFT_STR calls the same profile's gatherer, not another profile's.
    assert "CALL RISK_COPILOT_DEV.EVIDENCE_CHAIN.GATHER_EVIDENCE_DEV(" in dev["03_draft_str.sql"]
    demo = _rendered(repo_root, "demo")
    assert (
        "PROCEDURE RISK_COPILOT.EVIDENCE_CHAIN.GATHER_EVIDENCE(" in demo["02_gather_evidence.sql"]
    )


def _logged_queries(sql: str) -> dict[str, str]:
    """The query texts GATHER_EVIDENCE stores, unescaped from their DEFAULT literals."""
    found = re.findall(r"(q_\w+) VARCHAR DEFAULT '(.*?)';\n", sql, flags=re.S)
    return {name: text.replace("''", "'") for name, text in found}


def test_every_logged_query_takes_exactly_one_bind(repo_root: Path) -> None:
    # REVIEW_STR re-runs a log as EXECUTE IMMEDIATE <sql_text> USING (<params[0]>).
    queries = _logged_queries(_rendered(repo_root, "dev")["02_gather_evidence.sql"])
    assert sorted(queries) == ["q_clauses", "q_deposits", "q_party", "q_summary"]
    for name, text in queries.items():
        assert text.count("?") == 1, name
        assert ";" not in text, name


def test_logged_queries_are_fully_qualified(repo_root: Path) -> None:
    # A log is re-run long after deploy, from any schema: no relative names.
    queries = _logged_queries(_rendered(repo_root, "dev")["02_gather_evidence.sql"])
    for name, text in queries.items():
        for table in re.findall(r"\b(?:FROM|JOIN)\s+([A-Z_][\w.]*)", text):
            if table in {"p", "ids", "d", "r", "span"}:
                continue
            assert table.startswith("RISK_COPILOT_DEV."), (name, table)


def test_the_clause_list_includes_the_filing_duties(repo_root: Path) -> None:
    q = _logged_queries(_rendered(repo_root, "dev")["02_gather_evidence.sql"])["q_clauses"]
    assert "'STR_FILING_SLA_DAYS'" in q and "'PRINCIPAL_OFFICER_ATTESTATION_REQUIRED'" in q


def test_the_narrative_template_holds_no_figure(repo_root: Path) -> None:
    common = _rendered(repo_root, "dev")["01_case_tools_common.sql"]
    (template,) = re.findall(r"'narrative_template', '(.*?)'\n", common)
    assert re.findall(r"\{([a-z_]+)\}", template)
    assert not re.search(r"\d", template), "a number in the template would bypass computed_values"


def test_draft_records_the_configured_narrative_author(repo_root: Path) -> None:
    draft = _rendered(repo_root, "dev")["03_draft_str.sql"]
    assert "'TEMPLATE_V1'" in draft
    assert "FIU_IND_STR" in draft


def test_gather_and_review_share_one_canonical_form(repo_root: Path) -> None:
    # Recompute-and-compare is only meaningful if both sides hash the same way.
    files = _rendered(repo_root, "dev")

    def wrapper(sql: str) -> str:
        # The variable spliced in (q_summary, q_text) differs; the form must not.
        found = re.findall(r"wrapped := '(.*?)';\n", sql)
        (w,) = {re.sub(r"' \|\| \w+ \|\| '", "' || <q> || '", x) for x in found}
        return w

    assert wrapper(files["02_gather_evidence.sql"]) == wrapper(files["04_review_str.sql"])
    assert "'SQL-1'" in files["02_gather_evidence.sql"]


def test_the_reviewer_runs_every_check_and_uses_no_model(repo_root: Path) -> None:
    review = _rendered(repo_root, "dev")["04_review_str.sql"]
    for check in (
        "numeric_recompute",
        "citation_resolution",
        "clause_in_effect",
        "schema",
        "narrative_trace",
    ):
        assert f"'{check}'" in review, check
    assert "AI_COMPLETE" not in review and "CORTEX" not in review
    # Only a DRAFT can be reviewed; APPROVE is the only path to REVIEWED.
    assert "IF (v_status <> 'DRAFT') THEN" in review
    assert "IFF(:decision = 'APPROVE', 'REVIEWED', 'REJECTED')" in review


def _trace_patterns(review: str) -> tuple[str, str, str]:
    """The narrative_trace regexes as REVIEW_STR renders them: amount|date, count, rounding."""
    found = re.findall(r"REGEXP_SUBSTR_ALL\(:narr, '([^']+)'", review)
    assert len(found) == 3, found
    return found[0], found[1], found[2]


def _trace(review: str, narrative: str, displays: set[str]) -> list[str]:
    """What narrative_trace would flag, using the very patterns the procedure renders."""
    figure, count, rounding = _trace_patterns(review)
    flags = [m.group(0) for m in re.finditer(figure, narrative) if m.group(0) not in displays]
    flags += [
        m.group(0)
        for m in re.finditer(count, narrative)
        if re.match(r"[0-9]+", m.group(0)).group(0) not in displays  # type: ignore[union-attr]
    ]
    flags += [m.group(0) for m in re.finditer(rounding, narrative, re.IGNORECASE)]
    return flags


# The hero's computed_values displays and the template's own narrative (live, dev).
_DISPLAYS = {
    "2",
    "10",
    "₹94,02,691",
    "₹9,03,642",
    "₹9,67,299",
    "₹10,00,000",
    "03 Sep 2022",
    "08 Sep 2022",
    "₹38,50,000",
    "MUMBAI",
    "Kamya Dhar",
    "P-754971478708",
}
_TEMPLATE = (
    "Between 03 Sep 2022 and 08 Sep 2022, Kamya Dhar (P-754971478708, Scientist, marine) "
    "received 10 cash deposits totalling ₹94,02,691 into 2 different accounts at the MUMBAI "
    "branch. Every deposit was between ₹9,03,642 and ₹9,67,299, just below the cash "
    "transaction reporting threshold of ₹10,00,000."
)


def test_narrative_trace_passes_the_template_narrative(repo_root: Path) -> None:
    review = _rendered(repo_root, "dev")["04_review_str.sql"]
    # "₹9,67,299, just below": the sentence comma is not part of the amount.
    assert _trace(review, _TEMPLATE, _DISPLAYS) == []


def test_narrative_trace_rejects_an_invented_or_rounded_figure(repo_root: Path) -> None:
    review = _rendered(repo_root, "dev")["04_review_str.sql"]
    fake = (
        "Between 03 Sep 2022 and 09 Sep 2022 the customer made 12 cash deposits totalling "
        "₹1,12,40,000 (about ₹94 lakh) into 2 accounts."
    )
    assert _trace(review, fake, _DISPLAYS) == [
        "09 Sep 2022",
        "₹1,12,40,000",
        "₹94",
        "12 cash deposits",
        "94 lakh",
    ]


@pytest.mark.parametrize(
    ("profile", "role"), [("dev", "COMPLIANCE_OFFICER_DEV"), ("demo", "COMPLIANCE_OFFICER")]
)
def test_attestation_is_gated_on_the_callers_compliance_role(
    repo_root: Path, profile: str, role: str
) -> None:
    attest = _rendered(repo_root, profile)["05_attest_str.sql"]
    # Caller's rights: the owner's role must not stand in for the person signing.
    assert "EXECUTE AS CALLER" in attest
    assert f"IF (NOT IS_ROLE_IN_SESSION('{role}')) THEN" in attest


def test_attestation_binds_the_reviewed_hash_and_is_the_only_way_to_file(repo_root: Path) -> None:
    files = _rendered(repo_root, "dev")
    attest = files["05_attest_str.sql"]
    assert "reviewed_hash IS DISTINCT FROM actual_hash" in attest
    assert "attested_content_hash" in attest
    assert "IFF(decision = 'ATTESTED', 'FILED', 'RETURNED')" in attest
    others = [s for name, s in files.items() if name != "05_attest_str.sql"]
    # No other tool can set FILED (DRAFT_STR only reads it, to refuse a redraft).
    assert not any(re.search(r"SET\s+status\s*=[^;]*'FILED'", s) for s in others)
    # A refusal is written to the audit log before it is re-raised.
    assert "'REFUSED'" in attest


WORKFLOW = ("ATTESTATION", "REVIEW", "ARTIFACT", "FINDING", "EVIDENCE_QUERY_LOG", "EVIDENCE")


def test_reset_deletes_the_workflow_children_first_and_keeps_detection(repo_root: Path) -> None:
    reset = _rendered(repo_root, "dev")["06_reset_cases.sql"]
    assert "PROCEDURE RISK_COPILOT_DEV.EVIDENCE_CHAIN.RESET_CASES_DEV(" in reset
    deletes = re.findall(r"DELETE FROM RISK_COPILOT_DEV\.EVIDENCE_CHAIN\.(\w+);", reset)
    # An attestation, review or artifact is never left pointing at a deleted parent.
    assert deletes == list(WORKFLOW)
    # Detection's output and the audit trail are never deleted.
    for kept in ("SIGNAL", "CASE_SIGNAL", "CASE_RECORD", "AGENT_ACTION_LOG"):
        assert not re.search(rf"DELETE FROM \S*\.{kept}\b", reset), kept


def test_reset_reopens_every_case_field_the_tools_set(repo_root: Path) -> None:
    reset = _rendered(repo_root, "dev")["06_reset_cases.sql"]
    (update,) = re.findall(
        r"UPDATE RISK_COPILOT_DEV\.EVIDENCE_CHAIN\.CASE_RECORD(.*?);", reset, re.S
    )
    assert "status = 'OPEN'" in update
    # Everything DRAFT_STR and ATTEST_STR write on a case.
    for column in ("suspicion_established_at", "sla_deadline", "closed_at", "closure_reason"):
        assert f"{column} = NULL" in update


def test_reset_is_one_transaction_logged_and_a_dry_run_changes_nothing(repo_root: Path) -> None:
    reset = _rendered(repo_root, "dev")["06_reset_cases.sql"]
    body = reset[reset.index("BEGIN TRANSACTION") :]
    dry = body[body.index("IF (dry_run)") : body.index("END IF;")]
    # The dry run counts, then leaves before any write.
    assert "ROLLBACK" in dry and "RETURN" in dry
    assert body.index("END IF;") < body.index("DELETE FROM")
    insert = body.index("INSERT INTO RISK_COPILOT_DEV.AUDIT.AGENT_ACTION_LOG")
    assert body.rindex("DELETE FROM") < insert < body.index("COMMIT")
    assert "'OPERATOR'" in body and "'via', :via" in body
    assert "WHEN OTHER THEN" in body and "ROLLBACK;" in body[body.index("WHEN OTHER") :]
