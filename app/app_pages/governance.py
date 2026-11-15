"""Governance & audit: the governed thresholds, the rule registry, and every action logged."""

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st
from lib import data, insights, ui
from lib.db import PROC, call, reason

clauses = data.policy_clauses()
rules = data.rules()
log = insights.audit_all()
runs = data.eval_runs()

ui.page_header(
    "Governance & audit",
    "Changing a threshold is a governed data change with an audit trail, not a code change. "
    "Editing a rule's SQL without bumping its version blocks the deploy (rule_sql_hash).",
    glyph="policy",
    eyebrow="Controls and evidence",
    meta=[
        ui.pill("Append-only audit log", "emerald", glyph="history"),
        ui.pill("Hash-locked rules", "violet", glyph="lock"),
    ],
)

actions = len(log)
ok = int((log["outcome"] == "SUCCESS").sum()) if actions else 0
refused = int((log["outcome"] == "REFUSED").sum()) if actions else 0
active = int(rules["is_active"].astype(bool).sum()) if not rules.empty else 0
passed = int(runs["passed"].astype(bool).sum()) if not runs.empty else 0
ui.kpis(
    [
        ui.Kpi(
            "Clauses in force",
            f"{len(clauses):,}",
            note="effective today",
            glyph="gavel",
            tone="navy",
        ),
        ui.Kpi(
            "Active rules",
            f"{active:,}",
            note=f"of {len(rules):,} registered",
            delta="SQL hash-locked",
            state="good",
            glyph="rule",
            tone="violet",
        ),
        ui.Kpi(
            "Actions logged",
            f"{actions:,}",
            note="every tool call, question and reset",
            delta=f"{ok / actions:.0%} succeeded" if actions else "",
            state="good" if actions and ok / actions >= 0.9 else "warn",
            glyph="history",
            tone="blue",
        ),
        ui.Kpi(
            "Refused by policy",
            f"{refused:,}",
            note="gates that held, logged too",
            glyph="block",
            tone="amber",
        ),
        ui.Kpi(
            "Evaluation runs",
            f"{len(runs):,}",
            note="detection scored in SQL",
            delta=f"{passed}/{len(runs)} passed" if len(runs) else "",
            state="good" if len(runs) and passed == len(runs) else "warn",
            glyph="science",
            tone="emerald",
        ),
    ]
)

t1, t2, t3, t4 = st.tabs(
    [
        ":material/gavel: Policy clauses in force",
        ":material/rule: Rule registry",
        ":material/history: Action log",
        ":material/science: Evaluation runs",
    ]
)

with t1:
    pal = ui.palette()
    with ui.card(
        "clauses",
        "Clause timeline",
        "When each governed threshold took effect. Open bars are still in force today.",
        glyph="timeline",
        tone="navy",
    ):
        if clauses.empty:
            ui.empty("No clauses in force.")
        else:
            show_visual = ui.visual("gov_clauses")
            if show_visual:
                tl = clauses.copy()
                tl["start"] = pd.to_datetime(tl["effective_from"])
                tl["end"] = pd.to_datetime(tl["effective_to"]).fillna(pd.Timestamp(date.today()))
                tl["family"] = tl["clause_id"].str.split("_").str[0].str.title()
                tl["rule"] = tl["operator"].astype(str) + " " + tl["threshold_lower"].astype(str)
                gantt = (
                    alt.Chart(tl)
                    .mark_bar(cornerRadius=5, height=14)
                    .encode(
                        x=alt.X("start:T", title=None, axis=alt.Axis(format="%Y")),
                        x2="end:T",
                        y=alt.Y("clause_id:N", sort=alt.EncodingSortField("start"), title=None),
                        color=alt.Color(
                            "family:N", title="Family", scale=alt.Scale(range=ui.series())
                        ),
                        tooltip=[
                            "clause_id:N",
                            "section_heading:N",
                            "metric:N",
                            alt.Tooltip("rule:N", title="Threshold"),
                            alt.Tooltip("start:T", title="In force from", format="%d %b %Y"),
                            "source_document:N",
                            "approved_by:N",
                        ],
                    )
                    .properties(height={"step": 24})
                )
                ui.chart(gantt, axis={"labelLimit": 300})
            else:
                st.dataframe(ui.pretty(clauses), hide_index=True, width="stretch")
            st.caption("GOVERNANCE.V_POLICY_CLAUSE_CURRENT: the clauses effective today.")

with (
    t2,
    ui.card(
        "rules",
        "Rule registry",
        "Each detection rule, its typology, the clauses it reads and the SHA-256 of its SQL.",
        glyph="rule",
        tone="violet",
    ),
):
    r = rules.copy()
    r["rule_sql_hash"] = r["rule_sql_hash"].str[:16] + "…"
    st.dataframe(ui.pretty(r), hide_index=True, width="stretch")

with t3:
    if log.empty:
        ui.empty(
            "No actions logged yet. Every workflow step writes a row to AUDIT.AGENT_ACTION_LOG."
        )
    else:
        pal = ui.palette()
        f1, f2 = st.columns(2, gap="medium")
        actors = sorted(log["agent_mode"].dropna().unique())
        outcomes_all = sorted(log["outcome"].dropna().unique())
        pick_actor = f1.pills(
            "Actor", actors, selection_mode="multi", key="gov_actor", format_func=str.title
        )
        pick_outcome = f2.pills(
            "Outcome",
            outcomes_all,
            selection_mode="multi",
            key="gov_outcome",
            format_func=str.title,
        )
        tl = log.copy()
        if pick_actor:
            tl = tl[tl["agent_mode"].isin(pick_actor)]
        if pick_outcome:
            tl = tl[tl["outcome"].isin(pick_outcome)]
        outcome_scale = alt.Scale(
            domain=["SUCCESS", "REFUSED", "ERROR"],
            range=[pal["emerald"], pal["rose"], pal["amber"]],
        )
        tl["day"] = tl["logged_at"].dt.normalize()
        tl["seconds"] = tl["latency_ms"].astype(float) / 1000
        left, right = st.columns([3, 2], gap="medium")
        with (
            left,
            ui.card(
                "per-day",
                "Actions per day",
                "Stacked by the actor that ran them.",
                glyph="stacked_bar_chart",
                tone="blue",
            ),
        ):
            per_day = (
                alt.Chart(tl)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=34)
                .encode(
                    x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b", tickCount="day")),
                    y=alt.Y("count():Q", title="Actions"),
                    color=alt.Color(
                        "agent_mode:N", title="Actor", scale=alt.Scale(range=ui.series())
                    ),
                    tooltip=[
                        alt.Tooltip("day:T", format="%d %b"),
                        "agent_mode:N",
                        alt.Tooltip("count():Q", title="Actions"),
                    ],
                )
                .properties(height=260)
            )
            ui.chart(per_day)
        with (
            right,
            ui.card(
                "outcomes",
                "Outcomes",
                "Refusals are the controls doing their job.",
                glyph="donut_small",
                tone="emerald",
            ),
        ):
            mix = tl.groupby("outcome").size().reset_index(name="actions")
            ring = (
                alt.Chart(mix)
                .mark_arc(innerRadius=62, outerRadius=98, cornerRadius=4, padAngle=0.02)
                .encode(
                    theta=alt.Theta("actions:Q", stack=True),
                    color=alt.Color("outcome:N", title=None, scale=outcome_scale),
                    tooltip=["outcome:N", "actions:Q"],
                )
            )
            centre = alt.Chart(pd.DataFrame({"t": [f"{len(tl):,}"], "s": ["actions"]}))
            ui.chart(
                (
                    ring
                    + centre.mark_text(fontSize=20, fontWeight=700, color=pal["ink"], dy=-6).encode(
                        text="t:N"
                    )
                    + centre.mark_text(fontSize=11, color=pal["slate"], dy=13).encode(text="s:N")
                ).properties(height=260)
            )
        with ui.card(
            "latency",
            "Latency by tool",
            "Seconds per call: each dot is one action, the bar spans the middle half.",
            glyph="speed",
            tone="cyan",
        ):
            timed = tl.dropna(subset=["seconds"])
            if timed.empty:
                ui.empty("No timed actions in view.")
            else:
                base = alt.Chart(timed).encode(y=alt.Y("tool:N", title=None, sort="-x"))
                spread = base.mark_boxplot(
                    extent="min-max", size=16, median={"color": pal["ink"]}, outliers=False
                ).encode(
                    x=alt.X("seconds:Q", title="Seconds", scale=alt.Scale(zero=True)),
                    color=alt.value(pal["blue"]),
                )
                dots = base.mark_circle(size=46, opacity=0.55).encode(
                    x="seconds:Q",
                    color=alt.Color("outcome:N", title="Outcome", scale=outcome_scale),
                    tooltip=[
                        alt.Tooltip("logged_at:T", format="%d %b %H:%M:%S", title="Logged"),
                        "tool:N",
                        "agent_mode:N",
                        "outcome:N",
                        alt.Tooltip("seconds:Q", format=".2f"),
                    ],
                )
                ui.chart((spread + dots).properties(height={"step": 40}), axis={"labelLimit": 260})
        with ui.card(
            "timeline",
            "Every action",
            "Who ran what and how it ended, newest on the right. Switch to Table for the raw rows.",
            glyph="schedule",
            tone="slate",
        ):
            if ui.visual("gov_log"):
                chart = (
                    alt.Chart(tl)
                    .mark_point(filled=True, size=140, opacity=0.88)
                    .encode(
                        x=alt.X("logged_at:T", title=None),
                        y=alt.Y("tool:N", title=None, sort="-x"),
                        color=alt.Color("outcome:N", title="Outcome", scale=outcome_scale),
                        shape=alt.Shape("agent_mode:N", title="Actor"),
                        tooltip=[
                            alt.Tooltip("logged_at:T", format="%d %b %H:%M:%S"),
                            "tool:N",
                            alt.Tooltip("snowflake_user:N", title="Snowflake user"),
                            "agent_mode:N",
                            "outcome:N",
                            "refusal_reason:N",
                            "latency_ms:Q",
                        ],
                    )
                    .properties(height={"step": 34})
                )
                ui.chart(chart, axis={"labelLimit": 260})
            else:
                st.dataframe(ui.pretty(data.audit_log()), hide_index=True, width="stretch")

with (
    t4,
    ui.card(
        "eval",
        "Detection quality over time",
        "Detection scored against the hidden answer key, in SQL, by riskcopilot verify. The app "
        "reads only these aggregate results, never a label.",
        glyph="science",
        tone="emerald",
    ),
):
    if not runs.empty and ui.visual("gov_eval"):
        pal = ui.palette()
        long = runs.melt(
            id_vars=["finished_at"], value_vars=["recall", "precision"], var_name="metric"
        )
        trend = (
            alt.Chart(long)
            .mark_line(point={"size": 110, "filled": True}, strokeWidth=2.5)
            .encode(
                x=alt.X("finished_at:T", title="Verify run"),
                y=alt.Y(
                    "value:Q",
                    title=None,
                    axis=alt.Axis(format="%"),
                    scale=alt.Scale(domain=[0, 1.05]),
                ),
                color=alt.Color(
                    "metric:N", title=None, scale=alt.Scale(range=[pal["navy"], pal["emerald"]])
                ),
                tooltip=[
                    alt.Tooltip("finished_at:T", format="%d %b %H:%M"),
                    "metric:N",
                    alt.Tooltip("value:Q", format=".0%"),
                ],
            )
            .properties(height=240)
        )
        ui.chart(trend)
    else:
        st.dataframe(ui.pretty(runs), hide_index=True, width="stretch")

# Demo reset: one CALL to RESET_CASES, the same procedure the CLI uses.
done = st.session_state.pop("reset_done", None)
with st.expander(
    "Demo controls: reset every case", expanded=bool(done), icon=":material/restart_alt:"
):
    st.caption(
        "Puts all cases back to OPEN and deletes their evidence, drafts, reviews and sign-offs, "
        "so the demo can be run again. Signals are kept, and so is the action log: the reset "
        "adds its own row to it. A demo control only: a production evidence chain is never deleted."
    )
    if done:
        n = done["cases_reopened"]
        st.success(
            f"Reset: {sum(done['rows'].values()):,} workflow rows deleted, "
            f"{n} case{'' if n == 1 else 's'} reopened."
        )
    preview = data.reset_preview()
    to_delete = preview["rows"]
    if not sum(to_delete.values()) and not preview["cases_reopened"]:
        st.info("Nothing to reset: every case is OPEN, with no evidence, drafts or sign-offs.")
    else:
        left, right = st.columns([3, 2])
        with left:
            st.dataframe(
                pd.DataFrame(
                    {"table": list(to_delete), "rows to delete": list(to_delete.values())}
                ),
                hide_index=True,
                width="stretch",
            )
        with right:
            st.metric("Cases to reopen", preview["cases_reopened"])
            with st.form("reset_cases"):
                typed = st.text_input("Type RESET to confirm")
                go = st.form_submit_button(
                    "Reset all cases", type="primary", icon=":material/restart_alt:"
                )
        if go:
            if typed.strip() != "RESET":
                st.error("Type RESET to confirm.")
            else:
                try:
                    st.session_state["reset_done"] = call(PROC["reset_cases"], "APP", False)
                    st.session_state.pop("case_id", None)
                    st.rerun()
                except Exception as exc:  # the procedure's own message, verbatim
                    st.error(f"Reset refused: {reason(exc)}")
