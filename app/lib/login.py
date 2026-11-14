"""The sign-in screen. The demo credentials are on it on purpose: see lib/auth.py.

Rendered in place of whichever page was requested, so a deep link (or the
officer switching in from sign-off) lands back on that page after signing in.
"""

from __future__ import annotations

import html

import streamlit as st

from lib import auth, ui
from lib.db import CFG

_POINTS = [
    "Structuring and mule patterns detected in SQL, against governed policy thresholds",
    "Every figure in a Suspicious Transaction Report re-proven by a deterministic Reviewer",
    "Maker-checker: nothing is filed without the Principal Officer's hash-bound signature",
]


def _stats() -> str:
    """A few live figures for the hero panel; '' if the data cannot be read."""
    from lib import data  # imported late: a sign-in screen must render without a session

    try:
        k = data.kpis()
        ev = data.latest_detection_eval()
        clauses = len(data.policy_clauses())
    except Exception:
        return ""
    cells = [
        (f"{int(k['txns']):,}", "Transactions monitored"),
        (f"{int(k['signals']):,}", "Signals raised"),
        (f"{ev['recall']:.0%}" if ev else "-", "Detection recall"),
        (f"{clauses}", "Governed clauses"),
    ]
    return (
        '<div class="rc-login-stats">'
        + "".join(
            f'<div class="rc-login-stat"><b>{html.escape(v)}</b><span>{html.escape(t)}</span></div>'
            for v, t in cells
        )
        + "</div>"
    )


def render() -> None:
    left, right = st.columns([7, 5], gap="large")
    with left:
        points = "".join(
            f"<li>{ui.icon('verified')}<span>{html.escape(p)}</span></li>" for p in _POINTS
        )
        st.markdown(
            '<div class="rc-login-hero">'
            '<div class="rc-login-brand">'
            f'<div class="rc-head-icon">{ui.icon("shield")}</div>'
            f"<div><small>Financial crime &amp; compliance</small>{html.escape(CFG['title'])}</div>"
            "</div>"
            '<div class="rc-login-title">From a suspicious pattern to a '
            "<span>regulator-ready STR</span>.</div>"
            '<div class="rc-login-lead">One governed evidence chain on Snowflake: every figure '
            "proven in SQL, every action logged, a human signature on every filing.</div>"
            f'<ul class="rc-login-points">{points}</ul>'
            f"{_stats()}"
            "</div>",
            unsafe_allow_html=True,
        )

    with right:
        with ui.card("signin", "Sign in", "Use a demo account below, or type it in.", glyph="lock"):
            with st.form("sign_in", border=False):
                username = st.text_input("Username", key="login_user")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button(
                    "Sign in", type="primary", width="stretch", icon=":material/login:"
                )
            if submitted:
                if auth.sign_in(username, password):
                    st.rerun()
                st.error(
                    "Those credentials do not match a demo user. Use one of the accounts shown."
                )

        for key in auth.available():
            persona = auth.PERSONAS[key]
            maker = persona["can_investigate"]
            with ui.card(
                f"persona-{key}",
                f"{persona['title']} · {persona['name']}",
                persona["duty"],
                glyph="manage_search" if maker else "verified",
                tone="violet" if maker else "emerald",
                aside=ui.pill("Maker" if maker else "Checker", "violet" if maker else "emerald"),
            ):
                u, p, use = st.columns([5, 5, 3], vertical_alignment="center")
                with u:
                    st.code(key, language=None)
                with p:
                    if auth.hidden(key):
                        st.caption("Password held by the Principal Officer")
                    else:
                        st.code(auth.shown_password(key), language=None)
                use.button(
                    "Use",
                    key=f"use_{key}",
                    on_click=auth.fill,
                    args=(key,),
                    width="stretch",
                    icon=":material/arrow_forward:",
                )

    with st.expander(
        "Security model: what this page shows, and what a bank would use",
        icon=":material/security:",
    ):
        for note in auth.SECURITY_NOTES:
            st.markdown(f"- {note}")
