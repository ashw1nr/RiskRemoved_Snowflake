"""Rendering an STR and its review, shared by the case and sign-off pages."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from lib.db import variant

CHECKS = [
    ("numeric_recompute", "Every figure recomputed from its logged query"),
    ("citation_resolution", "Every cited clause and evidence id resolves"),
    ("clause_in_effect", "Every cited clause was in force on the deposit dates"),
    ("schema", "Required STR fields present; content matches its hash"),
]

STATUS_HELP = {
    "DRAFT": "Drafted, waiting for the Reviewer.",
    "REJECTED": "The Reviewer found defects. Fix by redrafting; it is never edited.",
    "REVIEWED": "The Reviewer approved it. Waiting for the Principal Officer.",
    "FILED": "Attested by the Principal Officer and filed.",
    "RETURNED": "Returned by the Principal Officer for a redraft.",
    "SUPERSEDED": "Replaced by a newer version.",
}


def str_document(a: dict[str, Any]) -> None:
    """The filing as an officer would read it."""
    content = variant(a["content"]) or {}
    subject = content.get("subject") or {}
    st.markdown(
        f"**Suspicious Transaction Report** · `{a['artifact_id']}` · version {a['version']}"
    )
    status = a["status"]
    st.markdown(
        "| Status | Subject | Typology | File by |\n|---|---|---|---|\n"
        f"| **{status}** | {subject.get('FULL_NAME', '-')} "
        f"| {str(content.get('typology_id', '-')).replace('TYP_', '')} "
        f"| {content.get('filing_deadline', '-')} |",
        help=STATUS_HELP.get(status),
    )
    st.markdown("##### Grounds of suspicion")
    st.info(content.get("grounds_of_suspicion", "-"))

    figures = variant(content.get("computed_values")) or {}
    rows = [
        {
            "placeholder": k,
            "stated as": v.get("display"),
            "source query": v.get("query_log_id"),
            "column": v.get("column"),
        }
        for k, v in sorted(figures.items())
    ]
    with st.expander(f"Where every figure comes from ({len(rows)} figures)"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        author = content.get("narrative_author") or a.get("author_model")
        if content.get("agent_review"):
            st.caption(
                f"Narrative written by `{author}` (Cortex Agent, separate account) and "
                "cross-reviewed before write-back. The figures are unchanged from the logged, "
                "hashed queries; the Reviewer re-checks them here."
            )
        else:
            st.caption(
                f"Narrative written by `{a.get('author_model')}` from template "
                f"`{a.get('prompt_version')}`; it contains placeholders only. Each figure is "
                "read from a logged, hashed query."
            )
    review = variant(content.get("agent_review")) or {}
    if review:
        check = review.get("numeric_check") or {}
        with st.expander("Agent review (agent account)"):
            st.markdown(
                f"**{review.get('decision')}** by `{review.get('reviewer_model')}`, "
                f"cross-family check of a `{review.get('drafter_model')}` draft "
                f"(attempt {review.get('attempt')}).  \n"
                f"Figure trace: **{'PASS' if check.get('passed') else 'FAIL'}** "
                f"({check.get('tokens_checked', 0)} amounts/counts/dates).  \n"
                f"FIU-IND reason codes: {', '.join(review.get('reason_codes') or []) or '-'}"
            )
            st.caption(
                f"`{review.get('agent')}` in `{review.get('agent_account')}` · package "
                f"`{review.get('package_id')}` · payload SHA-256 "
                f"`{(review.get('payload_hash') or '')[:16]}...`"
            )
    with st.expander("Cited clauses and transactions"):
        st.write(", ".join(variant(content.get("cited_clause_ids")) or []))
        tx = variant(content.get("transactions")) or []
        if tx:
            st.dataframe(pd.DataFrame(tx), hide_index=True, width="stretch")
    st.caption(f"content_hash `{a['content_hash']}`")


def review_result(row: dict[str, Any]) -> None:
    """The Reviewer's checklist and defects for one artifact."""
    if not row.get("review_decision") or pd.isna(row.get("review_decision")):
        st.caption("Not reviewed yet.")
        return
    defects = variant(row.get("defects_found")) or []
    failed = {d.get("check") for d in defects}
    approve = row["review_decision"] == "APPROVE"
    (st.success if approve else st.error)(
        f"Reviewer: **{row['review_decision']}**"
        + ("" if approve else f" -- {len(defects)} defect(s) found")
    )
    for key, label in CHECKS:
        bad = [d for d in defects if d.get("check") == key]
        with st.container(border=True):
            if key in failed:
                st.markdown(f":red[:material/cancel: **FAIL**]  \n{label}")
                for d in bad:
                    st.caption(d.get("message") or "")
            else:
                st.markdown(f":green[:material/check_circle: **PASS**]  \n{label}")
    known = {k for k, _ in CHECKS}
    for d in defects:
        if d.get("check") not in known:
            st.markdown(f"- **{d.get('check')}**: {d.get('message')}")
    checks = variant(row.get("checks_run")) or {}
    if checks:
        st.caption(
            f"{checks.get('queries_recomputed')} queries re-run, "
            f"{checks.get('figures_checked')} figures, {checks.get('clauses_checked')} clauses, "
            f"{checks.get('evidence_checked')} evidence ids checked. No model involved."
        )
