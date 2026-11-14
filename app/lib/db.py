"""Data access for the app: one session, parameterised queries, config-driven names.

Runs in three places with one code path; only the session differs:

* Streamlit Community Cloud -- a ``[snowflake]`` section in the app's secrets
  (written by ``riskcopilot app public-access``): a key-pair service user with a
  least-privilege role. No visitor needs a Snowflake account.
* Locally (``riskcopilot app run``) -- the package is installed, so the session
  comes from the same key-pair connection every other command uses.
* Streamlit in Snowflake -- ``st.connection("snowflake")`` gives the app owner's
  session. The ``risk_copilot`` package is not uploaded, so its import fails.

Identifiers come from ``app_config.json`` (rendered from conf/, validated
there); values are always bound, never interpolated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import streamlit as st

T = TypeVar("T")

CFG: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "app_config.json").read_text("utf-8")
)
S: dict[str, str] = CFG["schema"]
DT: dict[str, str] = CFG["dt"]
VIEW: dict[str, str] = CFG["view"]
PROC: dict[str, str] = CFG["procedure"]
HERO: dict[str, Any] | None = CFG.get("hero")


def _secrets(section: str = "snowflake") -> dict[str, Any] | None:
    """One of the hosted app's secrets sections, or None (no secrets file is normal)."""
    try:
        return dict(st.secrets[section]) if section in st.secrets else None
    except Exception:  # no secrets.toml at all: local or Streamlit in Snowflake
        return None


def _secrets_session(cfg: dict[str, Any]) -> Any:
    from cryptography.hazmat.primitives import serialization
    from snowflake.snowpark import Session

    key = serialization.load_pem_private_key(cfg.pop("private_key_pem").encode(), password=None)
    cfg["private_key"] = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cfg["authenticator"] = "SNOWFLAKE_JWT"
    cfg.setdefault("application", "risk_copilot_app")
    return Session.builder.configs(cfg).create()


@st.cache_resource(show_spinner=False)
def _session(section: str) -> Any:
    if (cfg := _secrets(section)) is not None:  # Streamlit Community Cloud
        return _secrets_session(cfg)
    try:
        from risk_copilot.config import load_settings
        from risk_copilot.session import create_session
    except ImportError:  # Streamlit in Snowflake
        return st.connection("snowflake").session()
    return create_session(load_settings(), unattended=True)


def _section() -> str:
    """Which secrets section this visitor's queries run under.

    The hosted app has two service users: [snowflake] for everyone, and
    [snowflake_officer] (the compliance role, so ATTEST_STR accepts it) only
    while the Principal Officer persona is signed in. Without that section, the
    officer runs as everyone else and the database refuses to sign. A URL
    whose secrets hold only [snowflake_officer] is the officer's own: it runs
    as that user throughout (lib/auth.py offers no other persona there).
    """
    officer_cfg = _secrets("snowflake_officer") is not None
    if officer_cfg and _secrets("snowflake") is None:
        return "snowflake_officer"
    officer = st.session_state.get("auth_user") == "officer"
    return "snowflake_officer" if officer and officer_cfg else "snowflake"


class _Current:
    """``session()`` is this visitor's session; ``session.clear()`` drops every cached one."""

    def __call__(self) -> Any:
        return _session(_section())

    def clear(self) -> None:
        _session.clear()


session = _Current()


# 390114 token expired, 390112 session gone, 390111 session expired; Snowpark's own
# message when it finds the connection closed. All are refused before the statement
# runs, so sending it again on a new session cannot run anything twice.
_EXPIRED = ("390114", "390112", "390111", "session has been closed")


def expired(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in _EXPIRED)


def run(fn: Callable[[Any], T]) -> T:
    """``fn(session)``, reconnecting once if the cached session has expired.

    A hosted app runs for days, and a laptop sleeps: either outlives the session's
    token, and without this every page fails until the server restarts.
    """
    try:
        return fn(session())
    except Exception as exc:
        if not expired(exc):
            raise
        session.clear()
        return fn(session())


def query(sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    """Run one statement; columns lower-cased."""
    df = run(lambda s: s.sql(sql, params=params or []).to_pandas())
    df.columns = [c.lower() for c in df.columns]
    return df


@st.cache_data(ttl=10, show_spinner=False)
def cached(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    """``query`` with a short TTL: pages re-run on every click."""
    return query(sql, list(params))


def call(proc: str, *args: Any) -> Any:
    """CALL a procedure; returns its result (parsed if JSON) and drops cached reads."""
    marks = ", ".join("?" for _ in args)
    rows = run(lambda s: s.sql(f"CALL {proc}({marks})", params=list(args)).collect())
    st.cache_data.clear()
    value = rows[0][0] if rows else None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def reason(exc: Exception) -> str:
    """The procedure's own message from a Snowflake error, without the envelope."""
    text = str(exc)
    marker = text.rfind(" : ")
    return text[marker + 3 :].strip() if marker != -1 else text.strip()


def variant(value: Any) -> Any:
    """A VARIANT/OBJECT/ARRAY column as Python (pandas gives JSON text)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def inr(value: Any) -> str:
    """Indian digit grouping: 9402691.42 -> '₹94,02,691'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    n = round(float(value))
    sign, digits = ("-" if n < 0 else ""), str(abs(n))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}₹{digits}"


def lakh(value: Any) -> str:
    """9402691.42 -> '₹94.03 lakh'."""
    if value is None:
        return "-"
    return f"₹{float(value) / 100000:,.2f} lakh"
