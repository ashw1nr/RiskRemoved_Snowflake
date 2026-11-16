"""Cortex Analyst client -- live mode (calls Snowflake) and demo mode (canned responses).

Extracted from app/lib/analyst.py so the Slack bot can use it without Streamlit.
"""

from __future__ import annotations

import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# Demo responses -- used when DEMO_MODE=true (no Snowflake access)
# ---------------------------------------------------------------------------
DEMO_RESPONSES: dict[str, dict[str, Any]] = {
    "default": {
        "text": (
            "Based on the data in SV_RISK_COPILOT, here are the results. "
            "Note: this is a demo response. Connect to Snowflake for live data."
        ),
        "sql": "SELECT 'demo_mode' AS status;",
        "rows": [{"status": "demo_mode"}],
    },
    "branch": {
        "text": "MUMBAI received the most cash deposits by value, totalling Rs 1,42,50,000 across 47 transactions.",
        "sql": (
            "SELECT branch_region, SUM(amount_inr) AS total_cash_deposits, COUNT(*) AS txn_count\n"
            "FROM RISK_COPILOT.CURATED.DT_TRANSACTIONS_ENRICHED\n"
            "WHERE is_cash = TRUE AND leg_side = 'C'\n"
            "GROUP BY branch_region\n"
            "ORDER BY total_cash_deposits DESC\n"
            "LIMIT 5;"
        ),
        "rows": [
            {"branch_region": "MUMBAI", "total_cash_deposits": 14250000, "txn_count": 47},
            {"branch_region": "DELHI", "total_cash_deposits": 8730000, "txn_count": 31},
            {"branch_region": "CHENNAI", "total_cash_deposits": 5420000, "txn_count": 19},
        ],
    },
    "cases": {
        "text": "There are 3 cases total: 2 OPEN and 1 DRAFTED.",
        "sql": (
            "SELECT status, COUNT(*) AS case_count\n"
            "FROM RISK_COPILOT.EVIDENCE_CHAIN.CASE_RECORD\n"
            "GROUP BY status\n"
            "ORDER BY case_count DESC;"
        ),
        "rows": [
            {"status": "OPEN", "case_count": 2},
            {"status": "DRAFTED", "case_count": 1},
        ],
    },
    "customers": {
        "text": "The top 5 customers by total cash deposits:",
        "sql": (
            "SELECT p.full_name, p.party_id, SUM(e.amount_inr) AS total_cash\n"
            "FROM RISK_COPILOT.CURATED.DT_TRANSACTIONS_ENRICHED e\n"
            "JOIN RISK_COPILOT.CURATED.DT_DIM_PARTY p ON p.party_id = e.party_id\n"
            "WHERE e.is_cash = TRUE AND e.leg_side = 'C'\n"
            "GROUP BY p.full_name, p.party_id\n"
            "ORDER BY total_cash DESC\n"
            "LIMIT 5;"
        ),
        "rows": [
            {"full_name": "Rajesh Kumar", "party_id": "PTY-abc123", "total_cash": 2850000},
            {"full_name": "Meena Sharma", "party_id": "PTY-def456", "total_cash": 1920000},
            {"full_name": "Amit Patel", "party_id": "PTY-ghi789", "total_cash": 1540000},
            {"full_name": "Sunita Reddy", "party_id": "PTY-jkl012", "total_cash": 1230000},
            {"full_name": "Vikram Singh", "party_id": "PTY-mno345", "total_cash": 980000},
        ],
    },
    "signals": {
        "text": "There are 2 active signals, both STRUCTURING_CROSS_ACCOUNT with severity HIGH.",
        "sql": (
            "SELECT rule_id, severity, COUNT(*) AS signal_count\n"
            "FROM RISK_COPILOT.EVIDENCE_CHAIN.SIGNAL\n"
            "GROUP BY rule_id, severity\n"
            "ORDER BY signal_count DESC;"
        ),
        "rows": [
            {"rule_id": "STRUCTURING_CROSS_ACCOUNT", "severity": "HIGH", "signal_count": 2},
        ],
    },
    "str": {
        "text": (
            "For case CASE-4399c4c86db5c6ea, the STR was drafted on 2026-09-29. "
            "Subject: Rajesh Kumar (PTY-abc123). 3 cash deposits totalling Rs 28,50,000 "
            "across 2 accounts (ACC-001, ACC-002) within a 30-day structuring window. "
            "Status: DRAFT, pending MLRO review."
        ),
        "sql": (
            "SELECT str_output_id, status, model_used, generated_at\n"
            "FROM RISK_COPILOT.EVIDENCE_CHAIN.STR_OUTPUT\n"
            "ORDER BY generated_at DESC LIMIT 1;"
        ),
        "rows": [
            {
                "str_output_id": "out-uuid-456",
                "status": "DRAFT",
                "model_used": "claude-sonnet-4",
                "generated_at": "2026-09-29 10:07:30",
            },
        ],
    },
}

# Keywords that route to specific demo responses
_KEYWORD_MAP = {
    "branch": "branch",
    "region": "branch",
    "case": "cases",
    "status": "cases",
    "customer": "customers",
    "top": "customers",
    "highest": "customers",
    "signal": "signals",
    "alert": "signals",
    "structuring": "signals",
    "str": "str",
    "report": "str",
    "filing": "str",
    "draft": "str",
}


def _match_demo(question: str) -> dict[str, Any]:
    q = question.lower()
    for keyword, key in _KEYWORD_MAP.items():
        if keyword in q:
            return DEMO_RESPONSES[key]
    return DEMO_RESPONSES["default"]


# ---------------------------------------------------------------------------
# Live Snowflake client
# ---------------------------------------------------------------------------
class AnalystClient:
    """Calls Cortex Analyst and runs the returned SQL. Two modes: live and demo."""

    def __init__(self) -> None:
        self.demo_mode = os.getenv("DEMO_MODE", "true").lower() == "true"
        self.semantic_view = os.getenv(
            "SNOWFLAKE_SEMANTIC_VIEW", "RISK_COPILOT.CURATED.SV_RISK_COPILOT"
        )
        self._conn = None

    def _get_connection(self):
        """Lazy Snowflake connection."""
        if self._conn is not None:
            return self._conn
        import snowflake.connector

        params = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "user": os.environ["SNOWFLAKE_USER"],
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "RC_DEMO_WH"),
            "role": os.getenv("SNOWFLAKE_ROLE", "RC_SERVICE_ROLE"),
        }
        key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
        if key_path:
            from cryptography.hazmat.primitives import serialization

            passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
            with open(key_path, "rb") as f:
                key = serialization.load_pem_private_key(
                    f.read(),
                    password=passphrase.encode() if passphrase else None,
                )
            params["private_key"] = key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        else:
            params["password"] = os.environ.get("SNOWFLAKE_PASSWORD", "")
        self._conn = snowflake.connector.connect(**params)
        return self._conn

    def ask(self, question: str) -> dict[str, Any]:
        """Ask a question. Returns {text, sql, rows, latency_ms, demo}."""
        if self.demo_mode:
            resp = _match_demo(question)
            return {**resp, "latency_ms": 42, "demo": True}

        started = time.time()
        conn = self._get_connection()
        # Call the Cortex Analyst REST API (connector 5.x no longer vendors requests)
        import requests

        host = f"{conn.account}.snowflakecomputing.com"
        if "." not in conn.account:
            host = f"{conn.account}.snowflakecomputing.com"
        resp = requests.post(
            f"https://{conn.host}/api/v2/cortex/analyst/message",
            json={
                "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
                "semantic_view": self.semantic_view,
            },
            headers={
                "Authorization": f'Snowflake Token="{conn.rest.token}"',
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=120,
        )
        latency = int((time.time() - started) * 1000)
        if not resp.ok:
            return {
                "text": f"Cortex Analyst error ({resp.status_code}): {resp.text[:300]}",
                "sql": None,
                "rows": [],
                "latency_ms": latency,
                "demo": False,
            }

        body = resp.json()
        text = ""
        sql = None
        for part in (body.get("message") or {}).get("content", []):
            if part.get("type") == "text":
                text += part.get("text", "")
            elif part.get("type") == "sql":
                sql = part.get("statement")

        rows = []
        if sql:
            try:
                cur = conn.cursor()
                wh = os.getenv("SNOWFLAKE_WAREHOUSE", "RC_DEV_WH")
                cur.execute(f"USE WAREHOUSE {wh}")
                cur.execute(sql)
                cols = [d[0].lower() for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchmany(50)]
            except Exception as exc:
                text += f"\n\n_SQL execution failed: {exc}_"

        return {
            "text": text,
            "sql": sql,
            "rows": rows,
            "latency_ms": latency,
            "demo": False,
        }
