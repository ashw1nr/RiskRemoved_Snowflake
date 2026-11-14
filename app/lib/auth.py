"""Demo sign-in: two personas, so maker-checker is visible in the UI.

This is a demonstration of the security model, not the security model itself.
The credentials are shown on the sign-in page on purpose, so a judge is never
blocked. What a real deployment does instead, and what holds regardless of
this page, is listed in SECURITY_NOTES.

The control that matters is in the database: ATTEST_STR refuses unless the
review approved this exact content hash, whoever is signed in here.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import streamlit as st

#: username -> persona. Passwords are compared as SHA-256 digests, in constant time.
PERSONAS: dict[str, dict[str, Any]] = {
    "analyst": {
        "name": "Priya Nair",
        "title": "AML Analyst",
        "duty": "Investigates, drafts and reviews STRs. Cannot sign.",
        "icon": ":material/manage_search:",
        "can_investigate": True,
        "can_attest": False,
        "sha256": "",
    },
    "officer": {
        "name": "Rajesh Menon",
        "title": "Principal Officer",
        "duty": "Signs or returns approved STRs. Does not draft them.",
        "icon": ":material/verified:",
        "can_investigate": False,
        "can_attest": True,
        "sha256": "",
    },
}

#: Shown on the sign-in page. Demo only -- see SECURITY_NOTES. Where the app's
#: secrets hold [demo_auth] officer_password (the hosted app), that replaces the
#: officer's here and is not shown: the signing user is then the one who knows it.
DEMO_PASSWORDS = {"analyst": "Analyst@2026", "officer": "Officer@2026"}

SECURITY_NOTES = [
    "**Maker-checker.** The analyst who drafts an STR cannot sign it; only the Principal "
    "Officer can (PML Rules, Rule 7). The app hides the other role's controls.",
    "**Enforced in the database too.** `ATTEST_STR` refuses unless the Reviewer approved "
    "this exact version and its SHA-256 still matches, and it runs with the caller's rights: "
    "only a session holding the compliance officer role can sign. Filing is impossible from "
    "any screen without a matching, hash-bound attestation.",
    "**What this demo does not prove.** On the public URL the analyst and the officer are two "
    "Snowflake service users: the officer's holds the compliance role and is used only after "
    "the officer signs in, with a password that is not on this page. A judge signed in as the "
    "analyst who reaches the signing call is refused by the database. Two demo logins are "
    "still not personal identities: in production each officer signs in through SSO.",
    "**Least privilege.** The hosted app connects as a dedicated service user that can read "
    "the demo data and run the case tools, and nothing else. It never sees the answer key.",
    "**Every action is logged** to `AUDIT.AGENT_ACTION_LOG`, including refusals and resets.",
    "**In production** this page is replaced by the bank's SSO (Snowflake SAML or OAuth), and "
    "each officer acts under their own Snowflake role (restricted caller's rights).",
]

_SESSION_KEY = "auth_user"


def _digest(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _secret_password(username: str) -> str | None:
    """The hosted app's password for a persona, from its secrets; None where there is none."""
    try:
        value = st.secrets["demo_auth"][f"{username}_password"]
    except Exception:  # no secrets file, no section, or no key: the demo default applies
        return None
    return str(value) if value else None


def _shown(username: str) -> bool:
    """[demo_auth] show_<user>_password = true: a URL kept for that persona's demo."""
    try:
        return st.secrets["demo_auth"].get(f"show_{username}_password") is True
    except Exception:
        return False


def _has(section: str) -> bool:
    try:
        return section in st.secrets
    except Exception:  # no secrets file at all
        return False


def available() -> list[str]:
    """Personas this deployment offers.

    A hosted URL whose secrets hold only [snowflake_officer] is the officer's
    URL: offering the analyst there would run the analyst as the signing user.
    """
    if _has("snowflake_officer") and not _has("snowflake"):
        return ["officer"]
    return list(PERSONAS)


def hidden(username: str) -> bool:
    """True where this persona's password comes from secrets and is not shown."""
    return _secret_password(username) is not None and not _shown(username)


def _password(username: str) -> str:
    return _secret_password(username) or DEMO_PASSWORDS[username]


def shown_password(username: str) -> str:
    """The password the sign-in page displays for a persona (only when not hidden)."""
    return _password(username)


# The digests are derived once from the demo passwords, so the two can never drift.
for _user, _password_text in DEMO_PASSWORDS.items():
    PERSONAS[_user]["sha256"] = _digest(_password_text)


def sign_in(username: str, password: str) -> bool:
    key = username.strip().lower()
    user = PERSONAS.get(key) if key in available() else None
    expected = _digest(_password(key)) if user else ""
    if not user or not hmac.compare_digest(_digest(password), expected):
        return False
    st.session_state[_SESSION_KEY] = key
    return True


def sign_out(prefill: str | None = None) -> None:
    """Sign out; optionally pre-fill the sign-in form for another persona."""
    st.session_state.pop(_SESSION_KEY, None)
    if prefill:
        fill(prefill)


def fill(username: str) -> None:
    """Put a persona's demo credentials into the sign-in form (an on_click callback).

    A password held in secrets is not filled in unless the secrets say to show it.
    """
    st.session_state["login_user"] = username
    st.session_state["login_password"] = "" if hidden(username) else _password(username)


def current() -> dict[str, Any] | None:
    key = st.session_state.get(_SESSION_KEY)
    return {"username": key, **PERSONAS[key]} if key in PERSONAS else None


def can(permission: str) -> bool:
    user = current()
    return bool(user and user.get(permission))
