"""Principal Officer sign-off: the human gate. Nothing is filed without it."""

import html

import altair as alt
import pandas as pd
import streamlit as st
from lib import auth, data, filters, insights, ui
from lib.db import PROC, call, reason
from lib.str_view import review_result, str_document

F = filters.current()
queue = data.signoff_queue()
book = insights.sla_book()
if F.regions:
    queue = queue[queue["branch_region"].isin(F.regions)] if not queue.empty else queue
    book = book[book["branch_region"].isin(F.regions)] if not book.empty else book
reg = data.attestations()
bad = data.filing_integrity()
filed = int((reg["artifact_status"] == "FILED").sum()) if not reg.empty else 0
signed = int((reg["decision"] == "ATTESTED").sum()) if not reg.empty else 0
returned = int((reg["decision"] == "RETURNED").sum()) if not reg.empty else 0

ui.page_header(
    "Principal Officer sign-off",
    "PML (Maintenance of Records) Rules, Rule 7: the Principal Officer bears the filing "
    "obligation. ATTEST_STR refuses unless the caller holds the compliance officer role, the "
    "Reviewer approved, and this exact version is unchanged.",
    glyph="verified",
    eyebrow="The human gate",
    meta=[
        ui.pill(f"{len(queue)} awaiting signature", "violet", glyph="pending_actions"),
        ui.pill("Maker-checker enforced", "emerald", glyph="lock"),
    ],
)

hours = book["hours_left"].astype(float) if not book.empty else pd.Series(dtype=float)
ui.kpis(
    [
        ui.Kpi(
            "Awaiting signature",
            f"{len(queue):,}",
            note="approved by the Reviewer",
            glyph="pending_actions",
            tone="violet",
        ),
        ui.Kpi(
            "Next deadline",
            ui.ago(hours.min() * 3600) if not hours.empty else "-",
            note="statutory clock, earliest STR",
            delta="within 3 days" if not hours.empty and hours.min() < 72 else "",
            state="warn" if not hours.empty and hours.min() < 72 else None,
            glyph="timer",
            tone="amber",
        ),
        ui.Kpi(
            "STRs filed",
            f"{filed:,}",
            note=f"{signed} attested · {returned} returned",
            glyph="task_alt",
            tone="emerald",
        ),
        ui.Kpi(
            "Integrity breaches",
            f"{bad}",
            note="filed without a hash-bound attestation",
            delta="must always be 0",
            state="good" if bad == 0 else "bad",
            glyph="gpp_good" if bad == 0 else "gpp_bad",
            tone="emerald" if bad == 0 else "rose",
        ),
    ]
)
if bad:
    st.error("Integrity breach: an STR is FILED without a matching attestation.")

if not book.empty:
    with ui.card(
        "clock",
        "Statutory clock",
        "Time used against each STR's filing window, from suspicion to deadline. The window "
        "length comes from POLICY_CLAUSE.",
        glyph="avg_time",
        tone="amber",
    ):
        pal = ui.palette()
        clock = book.copy()
        clock["window_h"] = clock["window_hours"].astype(float).clip(lower=1)
        clock["used_h"] = (clock["window_h"] - clock["hours_left"].astype(float)).clip(lower=0)
        clock["used"] = (clock["used_h"] / clock["window_h"]).clip(upper=1.2)
        clock["left"] = clock["hours_left"].map(lambda h: f"{ui.ago(float(h) * 3600)} left")
        clock["label"] = (
            clock["full_name"].fillna(clock["case_id"])
            + " · v"
            + clock["version"].astype(str)
            + " · "
            + clock["left"]
        )
        clock["zone"] = clock["used"].map(
            lambda u: "Overdue" if u >= 1 else ("Final stretch" if u >= 0.7 else "On track")
        )
        zones = ["On track", "Final stretch", "Overdue"]
        base = alt.Chart(clock).encode(y=alt.Y("label:N", title=None, sort=None))
        track = (
            base.transform_calculate(one="1")
            .mark_bar(color=pal["muted"], opacity=0.22, cornerRadius=6, height=18)
            .encode(
                x=alt.X(
                    "one:Q",
                    title="Share of the filing window used",
                    axis=alt.Axis(format="%"),
                    scale=alt.Scale(domain=[0, 1]),
                )
            )
        )
        used = base.mark_bar(cornerRadius=6, height=10).encode(
            x="used:Q",
            color=alt.Color(
                "zone:N",
                title=None,
                scale=alt.Scale(domain=zones, range=[pal["emerald"], pal["amber"], pal["rose"]]),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="STR"),
                alt.Tooltip("artifact_id:N", title="Artifact"),
                alt.Tooltip("used:Q", title="Window used", format=".0%"),
                alt.Tooltip("sla_deadline:T", title="Deadline", format="%d %b %Y"),
            ],
        )
        ui.chart(
            alt.layer(track, used).properties(height=46 * len(clock) + 40), axis={"labelLimit": 320}
        )

if queue.empty:
    ui.empty("No STR is awaiting sign-off. Approved drafts appear here.", glyph="inbox")
else:
    labels = {
        r.artifact_id: f"{r.artifact_id} -- {r.full_name} ({r.branch_region}), file by "
        f"{str(r.sla_deadline)[:10]}"
        for r in queue.itertuples()
    }
    with ui.card("pick", "STR awaiting attestation", glyph="assignment", tone="violet"):
        aid = st.selectbox(
            "STR awaiting attestation",
            list(labels),
            format_func=labels.get,
            label_visibility="collapsed",
        )
        due = pd.to_datetime(queue.set_index("artifact_id").loc[aid, "sla_deadline"])
        days = (due.normalize() - pd.Timestamp.now(tz=due.tz).normalize()).days
        tone = "rose" if days <= 2 else "amber" if days <= 5 else "emerald"
        ui.callout(
            ui.pill(
                f"{days} day(s) left" if days >= 0 else f"{-days} day(s) overdue",
                tone,
                glyph="timer",
            )
            + f" Filing deadline <b>{html.escape(str(due)[:10])}</b>: the governed number of "
            "working days after suspicion was established, read from POLICY_CLAUSE when the "
            "STR was drafted."
        )
    a = data.artifact(aid)
    if a:
        left, right = st.columns([3, 2], gap="medium")
        with left, ui.card("doc", glyph="description"):
            str_document(a)
        with right:
            with ui.card(
                "verdict",
                "Reviewer's result",
                "Every figure re-run, every citation resolved.",
                glyph="fact_check",
                tone="emerald",
            ):
                arts = data.case_artifacts(a["case_id"])
                row = arts[arts["artifact_id"] == aid]
                if not row.empty:
                    review_result(row.iloc[0].to_dict())
            with ui.card(
                "decide",
                "Your decision",
                "Your signature binds this exact content hash.",
                glyph="draw",
                tone="navy",
            ):
                if auth.can("can_attest") and not auth.officer_connected():
                    st.error(
                        "Signing is not connected here: this deployment's secrets have no "
                        "[snowflake_officer] section, so the signature would run as the shared "
                        "service user and the database would refuse it. Add the officer's "
                        "secrets (`riskcopilot app public-access --officer`) and restart.",
                        icon=":material/link_off:",
                    )
                elif auth.can("can_attest"):
                    officer = auth.current() or {}
                    with st.form("attest", clear_on_submit=False, border=False):
                        attestor = st.text_input(
                            "Your name",
                            value=f"{officer.get('name', '')}, {officer.get('title', '')}",
                            placeholder="Principal Officer",
                        )
                        decision = st.radio(
                            "Decision",
                            ["ATTESTED", "RETURNED"],
                            format_func={
                                "ATTESTED": "Attest and file",
                                "RETURNED": "Return for redraft",
                            }.get,
                            horizontal=True,
                        )
                        comment = st.text_area("Comment (optional)")
                        confirm = st.checkbox(
                            f"I have read this exact version (hash {a['content_hash'][:12]}…) "
                            "and take responsibility for this decision."
                        )
                        submitted = st.form_submit_button(
                            "Sign", type="primary", icon=":material/draw:"
                        )
                    if submitted:
                        if not confirm:
                            st.error("Tick the confirmation to sign.")
                        else:
                            try:
                                result = call(
                                    PROC["attest_str"], aid, decision, attestor, comment or None
                                )
                                st.session_state["signed"] = result
                                st.rerun()
                            except Exception as exc:  # the gate's reason, shown verbatim
                                st.error(f"Refused: {reason(exc)}")
                else:
                    st.warning(
                        "Only the Principal Officer can sign. The analyst who prepared this STR "
                        "cannot approve their own work.",
                        icon=":material/lock:",
                    )
                    st.button(
                        "Switch to the Principal Officer",
                        on_click=auth.sign_out,
                        args=("officer",),
                        type="primary",
                        icon=":material/swap_horiz:",
                    )

signed_now = st.session_state.pop("signed", None)
if signed_now:
    st.success(
        f"{signed_now.get('decision')}: {signed_now.get('artifact_id')} is now "
        f"{signed_now.get('artifact_status')}. Attestation {signed_now.get('attestation_id')} "
        f"records hash {str(signed_now.get('attested_content_hash'))[:12]}…",
        icon=":material/task_alt:",
    )

with ui.card(
    "register",
    "Attestation register",
    "Every signature and return, bound to the content hash it approved.",
    glyph="history_edu",
    tone="slate",
):
    if reg.empty:
        ui.empty("No attestations yet.", glyph="history_edu")
    else:
        show_visual = ui.visual("so_register")
        if show_visual:
            pal = ui.palette()
            timeline = reg.assign(when=pd.to_datetime(reg["attested_at"]))
            marks = (
                alt.Chart(timeline)
                .mark_point(filled=True, size=220, opacity=0.95)
                .encode(
                    x=alt.X("when:T", title=None),
                    y=alt.Y("decision:N", title=None),
                    color=alt.Color(
                        "decision:N",
                        title=None,
                        scale=alt.Scale(
                            domain=["ATTESTED", "RETURNED"], range=[pal["emerald"], pal["amber"]]
                        ),
                    ),
                    shape=alt.Shape("hash_matches:N", title="Hash matches"),
                    tooltip=[
                        alt.Tooltip("when:T", title="Signed", format="%d %b %H:%M"),
                        "case_id:N",
                        "artifact_id:N",
                        "attestor:N",
                        "decision:N",
                        "hash_matches:N",
                    ],
                )
                .properties(height=150)
            )
            ui.chart(marks)
        else:
            st.dataframe(ui.pretty(reg), hide_index=True, width="stretch")
