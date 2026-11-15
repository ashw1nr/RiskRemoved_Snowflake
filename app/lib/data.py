"""Every read the pages make, in one place. Names from config, values bound."""

from __future__ import annotations

from typing import Any

import pandas as pd

from lib.db import DT, PROC, VIEW, S, cached, variant

EV = S["evidence"]
GOV = S["governance"]
AUD = S["audit"]
RAW = S["raw"]
CUR = S["curated"]


def kpis() -> dict[str, Any]:
    row = cached(
        f"""
        SELECT
          (SELECT COUNT(DISTINCT txn_pair_id) FROM {DT["transactions_enriched"]}) AS txns,
          (SELECT COUNT(*) FROM {DT["signals_fast"]}) AS live_signals,
          (SELECT COUNT(*) FROM {EV}.SIGNAL) AS signals,
          (SELECT COUNT(*) FROM {EV}.CASE_RECORD) AS cases,
          (SELECT COUNT(*) FROM {EV}.CASE_RECORD WHERE status <> 'CLOSED') AS open_cases,
          (SELECT COUNT(*) FROM {EV}.ARTIFACT WHERE status = 'FILED') AS filed,
          (SELECT MAX(txn_timestamp) FROM {DT["transactions_enriched"]}) AS data_through
        """
    ).iloc[0]
    return row.to_dict()


def funnel() -> dict[str, Any]:
    """How far the workflow has carried the data: transactions down to filed STRs."""
    row = cached(
        f"""
        SELECT
          (SELECT COUNT(DISTINCT txn_pair_id) FROM {DT["transactions_enriched"]}) AS txns,
          (SELECT COALESCE(SUM(cash_credit_count), 0) FROM {DT["txn_daily_agg"]}) AS cash,
          (SELECT COUNT(*) FROM {EV}.SIGNAL) AS signals,
          (SELECT COUNT(*) FROM {EV}.CASE_RECORD) AS cases,
          (SELECT COUNT(DISTINCT case_id) FROM {EV}.EVIDENCE) AS investigated,
          (SELECT COUNT(DISTINCT f.case_id) FROM {EV}.ARTIFACT a
             JOIN {EV}.FINDING f ON f.finding_id = a.finding_id) AS drafted,
          (SELECT COUNT(DISTINCT f.case_id) FROM {EV}.ARTIFACT a
             JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
            WHERE a.status IN ('REVIEWED', 'FILED')) AS approved,
          (SELECT COUNT(*) FROM {EV}.ARTIFACT WHERE status = 'FILED') AS filed
        """
    ).iloc[0]
    return {k.lower(): int(v or 0) for k, v in row.to_dict().items()}


def latest_detection_eval() -> dict[str, Any] | None:
    """Recall/precision of the newest DETECTION run. Aggregates only, never labels."""
    df = cached(
        f"""
        SELECT run_id, git_commit, passed, finished_at,
               d.value:rule_id::STRING AS rule_id,
               d.value:recall::FLOAT AS recall, d.value:precision::FLOAT AS precision,
               d.value:true_positive::INT AS tp, d.value:expected::INT AS expected,
               d.value:predicted::INT AS predicted
        FROM {VIEW["eval_scores"]}, LATERAL FLATTEN(input => detection) d
        WHERE suite_name = 'DETECTION'
        QUALIFY RANK() OVER (ORDER BY finished_at DESC) = 1
        """
    )
    return None if df.empty else df.iloc[0].to_dict()


def case_queue() -> pd.DataFrame:
    return cached(
        f"""
        SELECT c.case_id, p.full_name, c.party_id, c.branch_region, c.case_type, c.status,
               c.priority, c.opened_at,
               COUNT(cs.signal_id) AS signals,
               LISTAGG(DISTINCT s.rule_id, ', ') AS rules,
               MAX(s.severity) AS severity,
               MAX(s.detail:deposit_total_inr::NUMBER(20,2)) AS flagged_inr
        FROM {EV}.CASE_RECORD c
        LEFT JOIN {EV}.CASE_SIGNAL cs ON cs.case_id = c.case_id
        LEFT JOIN {EV}.SIGNAL s ON s.signal_id = cs.signal_id
        LEFT JOIN {DT["dim_party"]} p ON p.party_id = c.party_id
        GROUP BY ALL
        ORDER BY c.opened_at DESC, c.case_id
        """
    )


def daily_activity() -> pd.DataFrame:
    return cached(
        f"""
        SELECT txn_date,
               SUM(credit_count) AS credits,
               SUM(cash_credit_count) AS cash_credits,
               SUM(credit_amount_inr) AS credit_inr
        FROM {DT["txn_daily_agg"]}
        GROUP BY txn_date ORDER BY txn_date
        """
    )


def signals_feed(limit: int = 25) -> pd.DataFrame:
    return cached(
        f"""
        SELECT s.last_seen_at, s.rule_id, s.party_id, s.severity, s.window_bucket,
               s.detail:deposit_count::INT AS deposits,
               s.detail:deposit_total_inr::NUMBER(20,2) AS total_inr,
               cs.case_id
        FROM {EV}.SIGNAL s
        LEFT JOIN {EV}.CASE_SIGNAL cs ON cs.signal_id = s.signal_id
        ORDER BY s.last_seen_at DESC, s.signal_id
        LIMIT {int(limit)}
        """
    )


# ---- one case -------------------------------------------------------------- #


def case(case_id: str) -> dict[str, Any] | None:
    df = cached(f"SELECT * FROM {EV}.CASE_RECORD WHERE case_id = ?", (case_id,))
    return None if df.empty else df.iloc[0].to_dict()


def party(party_id: str) -> dict[str, Any] | None:
    df = cached(f"SELECT * FROM {DT['dim_party']} WHERE party_id = ?", (party_id,))
    return None if df.empty else df.iloc[0].to_dict()


def accounts(party_id: str) -> pd.DataFrame:
    return cached(
        f"""
        SELECT account_id, account_type, opened_date, status, branch_region, branch_code
        FROM {RAW}.RAW_ACCOUNTS WHERE party_id = ? ORDER BY account_id
        """,
        (party_id,),
    )


def case_signals(case_id: str) -> pd.DataFrame:
    return cached(
        f"""
        SELECT s.*, r.description AS rule_description, r.clause_ids, r.rule_sql_hash,
               r.typology_id
        FROM {EV}.CASE_SIGNAL cs
        JOIN {EV}.SIGNAL s ON s.signal_id = cs.signal_id
        LEFT JOIN {GOV}.RULE_DEFINITION r
          ON r.rule_id = s.rule_id AND r.rule_version = s.rule_version
        WHERE cs.case_id = ?
        ORDER BY s.first_seen_at
        """,
        (case_id,),
    )


def evidence_transactions(case_id: str) -> pd.DataFrame:
    """The legs the case's signals cite, as recorded in CURATED."""
    return cached(
        f"""
        WITH ids AS (
          SELECT DISTINCT f.value::STRING AS txn_id
          FROM {EV}.CASE_SIGNAL cs
          JOIN {EV}.SIGNAL s ON s.signal_id = cs.signal_id,
          LATERAL FLATTEN(input => s.evidence_txn_ids) f
          WHERE cs.case_id = ?
        )
        SELECT t.txn_id, t.txn_timestamp, t.account_id, t.amount_inr, t.channel, t.txn_type,
               t.branch_code, t.source_ref
        FROM ids JOIN {DT["transactions_enriched"]} t ON t.txn_id = ids.txn_id
        ORDER BY t.txn_timestamp, t.txn_id
        """,
        (case_id,),
    )


def chain_counts(case_id: str) -> dict[str, int]:
    """How far the case has travelled along the evidence chain."""
    row = cached(
        f"""
        SELECT
          (SELECT COUNT(*) FROM {EV}.CASE_SIGNAL WHERE case_id = ?) AS signals,
          (SELECT COUNT(*) FROM {EV}.EVIDENCE WHERE case_id = ?) AS evidence,
          (SELECT COUNT(*) FROM {EV}.FINDING WHERE case_id = ?) AS findings,
          (SELECT COUNT(*) FROM {EV}.ARTIFACT a JOIN {EV}.FINDING f USING (finding_id)
             WHERE f.case_id = ?) AS artifacts,
          (SELECT COUNT(*) FROM {EV}.REVIEW r JOIN {EV}.FINDING f USING (finding_id)
             WHERE f.case_id = ?) AS reviews,
          (SELECT COUNT(*) FROM {EV}.ATTESTATION t JOIN {EV}.ARTIFACT a USING (artifact_id)
             JOIN {EV}.FINDING f USING (finding_id) WHERE f.case_id = ?) AS attestations
        """,
        (case_id,) * 6,
    ).iloc[0]
    return {k: int(v) for k, v in row.items()}


# ---- the STR workflow ------------------------------------------------------ #


def case_artifacts(case_id: str) -> pd.DataFrame:
    """Every STR version for a case, newest first, with its latest review."""
    return cached(
        f"""
        SELECT a.artifact_id, a.version, a.status, a.validation_status, a.content_hash,
               a.created_at, f.finding_id, f.author_model, f.prompt_version,
               r.decision AS review_decision, r.review_id, r.defects_found, r.checks_run,
               r.numeric_recompute_passed, r.citation_resolution_passed,
               r.clause_in_effect_passed, r.reviewed_at
        FROM {EV}.ARTIFACT a
        JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
        LEFT JOIN {EV}.REVIEW r ON r.finding_id = a.finding_id
        WHERE f.case_id = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY a.artifact_id
                                   ORDER BY r.reviewed_at DESC NULLS LAST) = 1
        ORDER BY a.version DESC
        """,
        (case_id,),
    )


def artifact(artifact_id: str) -> dict[str, Any] | None:
    df = cached(
        f"""
        SELECT a.*, f.case_id, f.computed_values, f.cited_clause_ids, f.author_model,
               f.prompt_version, f.version AS finding_version
        FROM {EV}.ARTIFACT a JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
        WHERE a.artifact_id = ?
        """,
        (artifact_id,),
    )
    return None if df.empty else df.iloc[0].to_dict()


def evidence_rows(case_id: str) -> pd.DataFrame:
    return cached(
        f"""
        SELECT ref_type, ref_id, materiality, ref_table, as_of_ts, evidence_id
        FROM {EV}.EVIDENCE WHERE case_id = ? ORDER BY ref_type, ref_id
        """,
        (case_id,),
    )


def query_logs(case_id: str) -> pd.DataFrame:
    return cached(
        f"""
        SELECT q.bind_values:name::STRING AS query, q.query_log_id, q.result_row_count AS row_count,
               q.result_hash, q.canonical_form_version AS form, q.executed_at, q.execution_ms,
               q.sql_text
        FROM {EV}.EVIDENCE_QUERY_LOG q JOIN {EV}.EVIDENCE e ON e.evidence_id = q.evidence_id
        WHERE e.case_id = ? ORDER BY q.executed_at, q.query_log_id
        """,
        (case_id,),
    )


def signoff_queue() -> pd.DataFrame:
    """STRs the Reviewer approved and no officer has signed yet."""
    return cached(
        f"""
        SELECT a.artifact_id, f.case_id, p.full_name, c.branch_region, a.version,
               c.sla_deadline, a.created_at
        FROM {EV}.ARTIFACT a
        JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
        JOIN {EV}.CASE_RECORD c ON c.case_id = f.case_id
        LEFT JOIN {DT["dim_party"]} p ON p.party_id = c.party_id
        WHERE a.status = 'REVIEWED'
        ORDER BY c.sla_deadline, a.created_at
        """
    )


def attestations() -> pd.DataFrame:
    return cached(
        f"""
        SELECT t.attested_at, t.decision, t.attestor, t.attestor_role, f.case_id, t.artifact_id,
               a.status AS artifact_status,
               t.attested_content_hash = a.content_hash AS hash_matches,
               t.comment
        FROM {EV}.ATTESTATION t
        JOIN {EV}.ARTIFACT a ON a.artifact_id = t.artifact_id
        JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
        ORDER BY t.attested_at DESC
        """
    )


def filing_integrity() -> int:
    """FILED artifacts without a matching, hash-bound attestation. Must be 0."""
    df = cached(
        f"""
        SELECT COUNT(*) AS n FROM {EV}.ARTIFACT a
        WHERE a.status = 'FILED' AND NOT EXISTS (
            SELECT 1 FROM {EV}.ATTESTATION t
            WHERE t.artifact_id = a.artifact_id AND t.decision = 'ATTESTED'
              AND t.attested_content_hash = a.content_hash)
        """
    )
    return int(df.iloc[0]["n"])


# ---- governance ------------------------------------------------------------ #


def policy_clauses() -> pd.DataFrame:
    return cached(
        f"""
        SELECT clause_id, clause_version, section_heading, metric, operator,
               threshold_lower, threshold_upper, action_required, effective_from,
               effective_to, source_document, approved_by
        FROM {GOV}.V_POLICY_CLAUSE_CURRENT ORDER BY clause_id
        """
    )


def rules() -> pd.DataFrame:
    return cached(
        f"""
        SELECT rule_id, rule_version, typology_id, severity, is_active,
               admissible_as_primary_evidence, clause_ids, rule_sql_hash, description
        FROM {GOV}.RULE_DEFINITION ORDER BY rule_id, rule_version
        """
    )


def audit_log(limit: int = 100) -> pd.DataFrame:
    return cached(
        f"""
        SELECT logged_at, snowflake_user, agent_mode, invoked_by, user_role, tools_invoked,
               case_ids_touched, model_version, outcome, refusal_reason, latency_ms
        FROM {AUD}.AGENT_ACTION_LOG ORDER BY logged_at DESC LIMIT {int(limit)}
        """
    )


def eval_runs() -> pd.DataFrame:
    return cached(
        f"""
        SELECT run_id, suite_name, profile, git_commit, generation_seed, passed,
               detection[0]:recall::FLOAT AS recall,
               detection[0]:precision::FLOAT AS precision,
               hero_status, finished_at
        FROM {VIEW["eval_scores"]} ORDER BY finished_at DESC
        """
    )


def reset_preview() -> dict[str, Any]:
    """What a reset would delete and reopen. RESET_CASES with dry_run: changes nothing."""
    df = cached(f"CALL {PROC['reset_cases']}(?, ?)", ("APP", True))
    return variant(df.iloc[0, 0])


def sim_status() -> dict[str, Any] | None:
    """The live simulator's state: held / arrived legs, next booking, task. Writes nothing.

    None where the simulator is not deployed (an older config without sim_control).
    """
    if "sim_control" not in PROC:
        return None
    df = cached(f"CALL {PROC['sim_control']}(?, ?, ?)", ("STATUS", None, "APP"))
    return variant(df.iloc[0, 0])


# ---- liquidity ------------------------------------------------------------- #


def structural_liquidity() -> pd.DataFrame:
    """The Statement of Structural Liquidity, one row per maturity bucket."""
    return cached(f"SELECT * FROM {VIEW['structural_liquidity']} ORDER BY as_of_date, bucket_id")


def alm_lines() -> pd.DataFrame:
    """The ALM book's line items behind the statement."""
    return cached(
        f"""
        SELECT as_of_date, bucket_id, bucket_label, side, line_item, amount_inr
        FROM {CUR}.ALM_CASHFLOW ORDER BY bucket_id, side DESC, amount_inr DESC
        """
    )
