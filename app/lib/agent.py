"""The AI STR, from the case page.

GENERATE_AI_STR (sql/procedures/09_generate_ai_str.sql) runs the whole agent
chain inside Snowflake -- evidence package, the agent's drafter and
cross-family reviewer, write-back, the deterministic Reviewer, the PDF -- so
the button works wherever the app runs: Streamlit Community Cloud, Streamlit
in Snowflake and locally, with nothing but USAGE on that one procedure.

An older config without the procedure falls back to the laptop bridge
(risk_copilot.agentic), which needs the package and the agent key.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from lib.db import PROC, call, reason


def in_snowflake() -> bool:
    return "generate_ai_str" in PROC


def available() -> tuple[bool, str]:
    if in_snowflake():
        return True, ""
    try:
        from risk_copilot.agentic.connections import agent_configured
    except Exception as exc:  # SiS / Community Cloud: the package is not uploaded
        return False, f"The agent bridge is not installed in this runtime ({type(exc).__name__})."
    if not agent_configured():
        return (
            False,
            "No agent-account key found (bridge/keys/rc_agent_svc.p8 or AGENT_PRIVATE_KEY_PATH).",
        )
    return True, ""


def generate(case_id: str, progress: Callable[[str, str], None], user: str) -> dict[str, Any]:
    if in_snowflake():
        progress(
            "snowflake", "GENERATE_AI_STR: evidence package, drafter, cross-review, Reviewer, PDF"
        )
        try:
            res = call(PROC["generate_ai_str"], case_id)
        except Exception as exc:  # the procedure's own message, without the envelope
            return {"case_id": case_id, "status": "ERROR", "error": reason(exc)}
        res = dict(res) if isinstance(res, dict) else {"status": "ERROR", "error": str(res)}
        if res.get("pdf_b64"):
            res["pdf_bytes"] = base64.b64decode(res.pop("pdf_b64"))
        return res

    from risk_copilot.agentic.str_pipeline import generate_ai_str

    return generate_ai_str(
        case_id, progress=progress, requested_by=f"app:{user}", channel="streamlit"
    )
