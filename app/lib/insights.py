"""Reads behind the executive views: the ledger, telemetry, KYC, the party graph and SLAs.

Kept apart from lib/data.py, which serves the case workflow. The same rules
apply: object names come from app_config.json, values are bound, and nothing
here estimates a figure. Ages are computed in SQL against the session clock,
the same clock that stamped the rows.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from lib.db import DT, VIEW, S, cached, query, variant

EV = S["evidence"]
AUD = S["audit"]
TXN = DT["transactions_enriched"]
NOW = "CURRENT_TIMESTAMP()::TIMESTAMP_NTZ"


@st.cache_data(ttl=30, show_spinner=False)
def _ledger_rows() -> pd.DataFrame:
    return query(
        f"""
        SELECT txn_pair_id, txn_timestamp, txn_date, leg_side, ABS(amount_inr) AS amount_inr,
               channel, COALESCE(is_cash, FALSE) AS is_cash, txn_type, account_type,
               party_type, branch_region, party_id
        FROM {TXN}
        WHERE NOT is_internal_account
        """
    )


@st.cache_data(ttl=300, show_spinner=False)
def _reference(sql: str) -> pd.DataFrame:
    """Slow-changing reference reads (customers, the party graph), cached for minutes."""
    return query(sql)


def ledger() -> pd.DataFrame:
    """Every customer leg (the bank's own cash desk excluded), amounts unsigned."""
    df = _ledger_rows().copy()
    if df.empty:
        return df
    df["txn_timestamp"] = pd.to_datetime(df["txn_timestamp"])
    df["txn_date"] = pd.to_datetime(df["txn_date"]).dt.date
    df["amount_inr"] = df["amount_inr"].astype(float)
    df["is_cash"] = df["is_cash"].astype(bool)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def filter_options() -> dict[str, Any]:
    """What the sidebar filters can choose from, read once every few minutes."""
    df = query(
        f"""
        SELECT
          (SELECT ARRAY_AGG(DISTINCT branch_region) WITHIN GROUP (ORDER BY branch_region)
             FROM {DT["dim_party"]} WHERE branch_region IS NOT NULL) AS regions,
          (SELECT ARRAY_AGG(DISTINCT channel) WITHIN GROUP (ORDER BY channel)
             FROM {TXN} WHERE NOT is_internal_account AND channel IS NOT NULL) AS channels,
          (SELECT MIN(txn_date) FROM {TXN}) AS first_day,
          (SELECT MAX(txn_date) FROM {TXN}) AS last_day
        """
    )
    row = df.iloc[0]
    first, last = row["first_day"], row["last_day"]
    return {
        "regions": [str(r) for r in variant(row["regions"]) or []],
        "channels": [str(c) for c in variant(row["channels"]) or []],
        "first_day": pd.to_datetime(first).date() if pd.notna(first) else None,
        "last_day": pd.to_datetime(last).date() if pd.notna(last) else None,
    }


def parties() -> pd.DataFrame:
    """Customer reference rows for joins in pandas: name, branch, KYC posture."""
    return _reference(
        f"""
        SELECT party_id, full_name, party_type, branch_region, risk_rating, kyc_status, pep_flag,
               YEAR(customer_since) AS since_year
        FROM {DT["dim_party"]}
        """
    )


def telemetry() -> dict[str, Any]:
    """One row of system readings for the telemetry console."""
    row = cached(
        f"""
        SELECT
          (SELECT COUNT(*) FROM {TXN}) AS legs,
          (SELECT DATEDIFF('second', MAX(ingested_at), {NOW}) FROM {TXN}) AS ingest_age_s,
          (SELECT COUNT(DISTINCT DATE_TRUNC('minute', ingested_at)) FROM {TXN}) AS ingest_batches,
          (SELECT MAX(txn_timestamp) FROM {TXN}) AS data_through,
          (SELECT COUNT(*) FROM {DT["signals_fast"]}) AS live_signals,
          (SELECT COUNT(*) FROM {EV}.SIGNAL) AS routed_signals,
          (SELECT DATEDIFF('second', MAX(last_seen_at), {NOW}) FROM {EV}.SIGNAL) AS route_age_s,
          (SELECT COUNT(*) FROM {AUD}.AGENT_ACTION_LOG) AS actions,
          (SELECT COUNT_IF(outcome = 'SUCCESS') FROM {AUD}.AGENT_ACTION_LOG) AS actions_ok,
          (SELECT COUNT_IF(outcome = 'REFUSED') FROM {AUD}.AGENT_ACTION_LOG) AS refused,
          (SELECT MEDIAN(latency_ms) FROM {AUD}.AGENT_ACTION_LOG) AS p50_ms,
          (SELECT APPROX_PERCENTILE(latency_ms, 0.95) FROM {AUD}.AGENT_ACTION_LOG) AS p95_ms,
          (SELECT DATEDIFF('second', MAX(logged_at), {NOW}) FROM {AUD}.AGENT_ACTION_LOG)
            AS action_age_s,
          (SELECT COUNT(*) FROM {EV}.EVIDENCE) AS evidence,
          (SELECT COUNT(*) FROM {EV}.EVIDENCE_QUERY_LOG) AS logged_queries,
          (SELECT COUNT(*) FROM {EV}.ATTESTATION) AS attestations
        """
    ).iloc[0]
    return row.to_dict()


def ingest_series() -> pd.DataFrame:
    """Legs landed per minute: the bursts of the initial load and each replay tick."""
    return cached(
        f"""
        SELECT DATE_TRUNC('minute', ingested_at) AS minute, COUNT(*) AS legs
        FROM {TXN} GROUP BY 1 ORDER BY 1
        """
    )


def audit_all(limit: int = 2000) -> pd.DataFrame:
    """The action log with the first tool named, newest first."""
    df = cached(
        f"""
        SELECT logged_at, agent_mode, outcome, latency_ms, model_version, refusal_reason,
               snowflake_user,
               tools_invoked[0]::STRING AS tool,
               DATEDIFF('second', logged_at, {NOW}) AS age_s
        FROM {AUD}.AGENT_ACTION_LOG
        ORDER BY logged_at DESC
        LIMIT {int(limit)}
        """
    )
    if not df.empty:
        df["logged_at"] = pd.to_datetime(df["logged_at"])
        df["tool"] = df["tool"].fillna("-")
    return df


def kyc_profile() -> pd.DataFrame:
    """Customers by branch, type, risk rating and KYC status."""
    return _reference(
        f"""
        SELECT branch_region, party_type, risk_rating, kyc_status, pep_flag, COUNT(*) AS parties
        FROM {DT["dim_party"]}
        GROUP BY ALL
        """
    )


def network_edges() -> pd.DataFrame:
    """The party graph by edge type: how many links, how many parties, how much money."""
    return _reference(
        f"""
        SELECT relationship_type, COUNT(*) AS edges, COUNT(DISTINCT party_a_id) AS parties,
               SUM(total_amount_inr) AS amount_inr
        FROM {DT["party_relationship"]}
        GROUP BY 1
        ORDER BY edges DESC
        """
    )


def top_flows(limit: int = 12) -> pd.DataFrame:
    """The largest customer-to-customer fund flows (cash desk excluded upstream)."""
    return _reference(
        f"""
        SELECT f.party_a_id, a.full_name AS payer, f.party_b_id, b.full_name AS payee,
               f.txn_count, f.total_amount_inr
        FROM {DT["party_relationship"]} f
        LEFT JOIN {DT["dim_party"]} a ON a.party_id = f.party_a_id
        LEFT JOIN {DT["dim_party"]} b ON b.party_id = f.party_b_id
        WHERE f.relationship_type = 'FUND_FLOW'
        ORDER BY f.total_amount_inr DESC
        LIMIT {int(limit)}
        """
    )


def signal_book() -> pd.DataFrame:
    """Every routed signal at ledger time: who, how much, across how many accounts."""
    df = cached(
        f"""
        SELECT s.signal_id, s.rule_id, s.party_id, p.full_name, p.branch_region, s.severity,
               s.fired_at, s.last_seen_at,
               s.detail:deposit_count::INT AS deposits,
               s.detail:deposit_total_inr::NUMBER(20,2) AS total_inr,
               ARRAY_SIZE(s.detail:accounts) AS accounts,
               cs.case_id
        FROM {EV}.SIGNAL s
        LEFT JOIN {EV}.CASE_SIGNAL cs ON cs.signal_id = s.signal_id
        LEFT JOIN {DT["dim_party"]} p ON p.party_id = s.party_id
        ORDER BY s.fired_at, s.signal_id
        """
    )
    if not df.empty:
        df["fired_at"] = pd.to_datetime(df["fired_at"])
    return df


def sla_book() -> pd.DataFrame:
    """Approved STRs awaiting sign-off, with the statutory clock in hours."""
    return cached(
        f"""
        SELECT a.artifact_id, f.case_id, p.full_name, c.branch_region, a.version,
               c.suspicion_established_at, c.sla_deadline,
               DATEDIFF('hour', {NOW}, c.sla_deadline) AS hours_left,
               DATEDIFF('hour', c.suspicion_established_at, c.sla_deadline) AS window_hours
        FROM {EV}.ARTIFACT a
        JOIN {EV}.FINDING f ON f.finding_id = a.finding_id
        JOIN {EV}.CASE_RECORD c ON c.case_id = f.case_id
        LEFT JOIN {DT["dim_party"]} p ON p.party_id = c.party_id
        WHERE a.status = 'REVIEWED'
        ORDER BY c.sla_deadline, a.created_at
        """
    )


def artifact_mix() -> pd.DataFrame:
    """STR versions by status: the drafting funnel's outcome."""
    return cached(f"SELECT status, COUNT(*) AS versions FROM {EV}.ARTIFACT GROUP BY 1")


def cash_thresholds() -> dict[str, float | None]:
    """The cash reporting line and the structuring band in rupees, from the clauses in force."""
    row = cached(
        f"""
        SELECT MAX(IFF(metric = 'cash_txn_amount_inr', threshold_lower, NULL)) AS ctr_inr,
               MAX(IFF(metric = 'cash_txn_amount_ratio_to_ctr', threshold_lower, NULL)) AS band_lo,
               MAX(IFF(metric = 'cash_txn_amount_ratio_to_ctr', threshold_upper, NULL)) AS band_hi
        FROM {S["governance"]}.V_POLICY_CLAUSE_CURRENT
        """
    ).iloc[0]
    ctr = float(row["ctr_inr"]) if pd.notna(row["ctr_inr"]) else None
    lo = float(row["band_lo"]) if pd.notna(row["band_lo"]) else None
    hi = float(row["band_hi"]) if pd.notna(row["band_hi"]) else None
    return {
        "ctr": ctr,
        "band_lo": ctr * lo if ctr and lo else None,
        "band_hi": ctr * hi if ctr and hi else None,
    }


def eval_latest() -> dict[str, Any] | None:
    """The newest detection run's control figures (aggregates only, never a label)."""
    df = cached(
        f"""
        SELECT finished_at, passed,
               d.value:controls::INT AS controls, d.value:controls_fired::INT AS controls_fired,
               d.value:false_positive::INT AS false_positive, d.value:missed::INT AS missed
        FROM {VIEW["eval_scores"]}, LATERAL FLATTEN(input => detection) d
        WHERE suite_name = 'DETECTION'
        QUALIFY RANK() OVER (ORDER BY finished_at DESC) = 1
        """
    )
    return None if df.empty else df.iloc[0].to_dict()
