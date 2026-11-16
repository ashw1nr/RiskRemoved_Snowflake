"""Case investigation: who, what fired, and the transactions behind it."""

import json

import altair as alt
import pandas as pd
import streamlit as st
from lib import agent, auth, data, filters, triage, ui
from lib.db import HERO, PROC, call, inr, lakh, reason
from lib.str_view import STATUS_HELP, review_result, str_document

F = filters.current()
everything = data.case_queue()
if everything.empty:
    ui.page_header("Case investigation", glyph="manage_search", eyebrow="Investigation")
    ui.empty("No cases yet. Signals are routed into cases by ROUTE_SIGNALS.")
    st.stop()

queue = filters.cases(everything, F)
names = everything.set_index("case_id")["full_name"].to_dict()
wanted = st.session_state.get("case_id") or (HERO["case_ids"][0] if HERO else None)
ids = queue["case_id"].tolist()
if wanted in names and wanted not in ids:  # opened from elsewhere: keep it selectable
    ids = [wanted, *ids]
if not ids:
    ui.page_header("Case investigation", glyph="manage_search", eyebrow="Investigation")
    ui.empty("No case matches the sidebar filters. Clear them to see every case.")
    st.stop()

head = st.container()  # the header names the selected case, so it is filled below
with st.container(key="case-picker"):
    case_id = st.selectbox(
        "Case",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=lambda c: (
            f"{c} -- {names.get(c) or 'unknown party'}"
            + ("  (demo hero)" if HERO and c in HERO["case_ids"] else "")
        ),
    )
st.session_state["case_id"] = case_id

case = data.case(case_id)
party = data.party(case["party_id"]) if case else None
if not case or not party:
    st.error("Case or party not found.")
    st.stop()

signals = data.case_signals(case_id)
txns = data.evidence_transactions(case_id)
chain = data.chain_counts(case_id)
pal = ui.palette()
status_tone = {"OPEN": "slate", "INVESTIGATING": "blue", "DRAFTED": "amber", "REVIEWED": "emerald"}

with head:
    ui.page_header(
        str(party["full_name"]),
        f"Case {case_id} for customer {case['party_id']}, opened "
        f"{str(case['opened_at'])[:16]}. Who they are, what fired, and the deposits behind it.",
        glyph="manage_search",
        eyebrow="Case investigation",
        meta=[
            ui.pill(
                str(case["status"]).title(), status_tone.get(case["status"], "navy"), glyph="flag"
            ),
            ui.pill(str(case["case_type"]), "violet", glyph="category"),
            ui.pill(str(case["branch_region"]).title(), "cyan", glyph="location_on"),
            *(
                [ui.pill("Demo case", "rose", live=True)]
                if HERO and case_id in HERO["case_ids"]
                else []
            ),
        ],
    )


def _detail(raw: object) -> dict:
    return json.loads(raw) if isinstance(raw, str) else (raw or {})  # type: ignore[return-value]


# ---- the case at a glance --------------------------------------------------- #
d0 = _detail(signals.iloc[0]["detail"]) if not signals.empty else {}
ctr = d0.get("ctr_threshold_inr")
amounts = txns["amount_inr"].astype(float) if not txns.empty else pd.Series(dtype=float)
total_value, total_unit = ui.rupees(amounts.sum() if not amounts.empty else 0)
closest = (amounts.max() / float(ctr)) if ctr and not amounts.empty else None
ui.kpis(
    [
        ui.Kpi(
            "Cash deposited",
            total_value,
            total_unit,
            note="across the evidence legs",
            glyph="payments",
            tone="navy",
        ),
        ui.Kpi(
            "Deposits in band",
            str(d0.get("deposit_count", len(txns))),
            note="each just under the line",
            delta=f"band {lakh(d0.get('band_lower_inr'))} to {lakh(d0.get('band_upper_inr'))}"
            if d0
            else "",
            state="warn" if d0 else None,
            glyph="price_check",
            tone="amber",
        ),
        ui.Kpi(
            "Accounts used",
            str(len(d0.get("accounts", []))) if d0 else str(txns["account_id"].nunique()),
            note="splitting is part of the pattern",
            glyph="account_tree",
            tone="violet",
        ),
        ui.Kpi(
            "Closest to the line",
            f"{closest:.1%}" if closest else "-",
            note=f"of the {lakh(ctr)} reporting line" if ctr else "",
            delta="never crossed" if closest and closest < 1 else "",
            state="bad" if closest and closest < 1 else None,
            glyph="speed",
            tone="rose",
        ),
        ui.Kpi(
            "Observation window",
            f"{d0.get('window_days', '-')}",
            "days",
            note=f"from {signals.iloc[0]['window_bucket']}" if not signals.empty else "",
            glyph="date_range",
            tone="cyan",
        ),
    ]
)

steps = [
    ("Signal", chain["signals"], "a rule fired"),
    ("Evidence", chain["evidence"], "pointers + logged queries"),
    ("Finding", chain["findings"], "facts the STR may state"),
    ("STR draft", chain["artifacts"], "governed template"),
    ("Review", chain["reviews"], "every figure re-run"),
    ("Attested", chain["attestations"], "officer's signature"),
]
reached = max((i for i, (_, n, _) in enumerate(steps) if n), default=-1)
ui.section("Evidence chain", "each step points at the one before; the outlined tile is next")
ui.strip(
    [(name, f"{int(n):,}" if n else "--", note) for name, n, note in steps],
    active=reached + 1 if reached + 1 < len(steps) else None,
    done=reached,
)

# ---- party profile and what fired ------------------------------------------- #
left, right = st.columns([2, 3], gap="medium")
with (
    left,
    ui.card(
        "kyc",
        "Customer profile (KYC)",
        "The record the bank holds on this customer.",
        glyph="badge",
        tone="navy",
    ),
):
    profile = {
        "Party type": party["party_type"],
        "Occupation": party["occupation"],
        "Declared annual income": inr(party["declared_annual_income"]),
        "Source of funds": party["source_of_funds"],
        "Risk rating": party["risk_rating"],
        "PEP": "Yes" if party["pep_flag"] else "No",
        "KYC status": f"{party['kyc_status']} (reviewed {party['kyc_last_reviewed']})",
        "Customer since": str(party["customer_since"]),
        "Branch": f"{party['branch_region']} / {party['branch_code']}",
        "Aadhaar": f"XXXX-XXXX-{party['aadhaar_last4']}",
    }
    flags = {}
    if str(party["risk_rating"]).upper() == "HIGH":
        flags["Risk rating"] = "high risk"
    if party["pep_flag"]:
        flags["PEP"] = "enhanced due diligence"
    ui.facts(profile, flags=flags)
    if party.get("kyc_note"):
        st.caption("KYC note (free text, untrusted input)")
        st.text(party["kyc_note"])
    st.markdown("##### Accounts")
    st.dataframe(ui.pretty(data.accounts(case["party_id"])), hide_index=True, width="stretch")

with right:
    with ui.card(
        "fired",
        "Why it was flagged",
        "The rule that fired and the governed thresholds it read.",
        glyph="policy_alert",
        tone="rose",
    ):
        for _, s in signals.iterrows():
            detail = _detail(s["detail"])
            st.markdown(
                f"**{s['rule_id']}** v{s['rule_version']} &nbsp;"
                + ui.pill(f"severity {s['severity']}", "rose", glyph="priority_high"),
                unsafe_allow_html=True,
            )
            if s.get("rule_description"):
                st.caption(s["rule_description"])
            band = (detail.get("band_lower_inr"), detail.get("band_upper_inr"))
            st.caption(
                f"Each deposit between {inr(band[0])} and {inr(band[1])}, just under the "
                f"{inr(detail.get('ctr_threshold_inr'))} cash reporting threshold, spread over "
                f"{len(detail.get('accounts', []))} accounts within {detail.get('window_days')} "
                f"days (window from {s['window_bucket']}). Thresholds read from POLICY_CLAUSE."
            )

    if not txns.empty:
        with ui.card(
            "deposits",
            "The deposits",
            "Every deposit sits in the shaded band just under the dashed reporting line.",
            glyph="scatter_plot",
            tone="amber",
        ):
            ctr, lo, hi = (
                d0.get(k) for k in ("ctr_threshold_inr", "band_lower_inr", "band_upper_inr")
            )
            t = txns.sort_values("txn_timestamp").copy()
            t["lakh"] = t["amount_inr"].astype(float) / 100000
            t["amount"] = t["amount_inr"].map(inr)
            t["running_lakh"] = t["lakh"].cumsum()
            x = alt.X("txn_timestamp:T", title=None, axis=alt.Axis(format="%d %b", tickCount="day"))
            layers = []
            if lo and hi:
                band_df = pd.DataFrame({"lo": [float(lo) / 100000], "hi": [float(hi) / 100000]})
                layers.append(
                    alt.Chart(band_df)
                    .mark_rect(color=pal["band"], opacity=0.16)
                    .encode(y="lo:Q", y2="hi:Q")
                )
            if ctr:
                line = alt.Chart(pd.DataFrame({"y": [float(ctr) / 100000]}))
                layers.append(
                    line.mark_rule(strokeDash=[6, 4], color=pal["rose"], strokeWidth=2).encode(
                        y="y:Q"
                    )
                )
                layers.append(
                    line.mark_text(
                        align="left", dx=4, dy=-8, color=pal["rose"], fontSize=11, fontWeight=600
                    ).encode(y="y:Q", text=alt.value(f"Reporting line {lakh(ctr)}"), x=alt.value(0))
                )
            layers.append(
                alt.Chart(t)
                .mark_circle(size=190, opacity=0.95, stroke=pal["ink"], strokeWidth=0.8)
                .encode(
                    x=x,
                    y=alt.Y("lakh:Q", title="Deposit (₹ lakh)", scale=alt.Scale(zero=False)),
                    color=alt.Color(
                        "account_id:N", title="Account", scale=alt.Scale(range=ui.series())
                    ),
                    tooltip=[
                        "txn_id:N",
                        alt.Tooltip("txn_timestamp:T", format="%d %b %H:%M"),
                        "account_id:N",
                        "amount:N",
                        "channel:N",
                    ],
                )
            )
            ui.chart(alt.layer(*layers).properties(height=230))
            cumulative = (
                alt.Chart(t)
                .mark_area(
                    interpolate="step-after",
                    color=pal["blue"],
                    opacity=0.2,
                    line={"color": pal["navy"], "strokeWidth": 2},
                )
                .encode(
                    x=x,
                    y=alt.Y("running_lakh:Q", title="Running total (₹ lakh)"),
                    tooltip=[
                        alt.Tooltip("txn_timestamp:T", format="%d %b %H:%M"),
                        alt.Tooltip("running_lakh:Q", title="Total (₹ lakh)", format=",.2f"),
                    ],
                )
                .properties(height=150)
            )
            ui.chart(cumulative)
            st.caption(
                f"Together they add up to {lakh(t['amount_inr'].sum())}, none of it reported."
            )

if not txns.empty:
    with ui.card(
        "txns",
        "Evidence transactions",
        "How the cash was spread across accounts: splitting is part of the pattern.",
        glyph="receipt_long",
        tone="blue",
    ):
        show_visual = ui.visual("case_txns")
        shown = txns.assign(amount=txns["amount_inr"].map(inr))
        if show_visual:
            per_account = (
                alt.Chart(shown.assign(lakh=shown["amount_inr"].astype(float) / 100000))
                .mark_bar(cornerRadiusEnd=6, height={"band": 0.62})
                .encode(
                    y=alt.Y("account_id:N", title=None),
                    x=alt.X("sum(lakh):Q", title="Deposited (₹ lakh)"),
                    color=alt.Color(
                        "channel:N", title="Channel", scale=alt.Scale(range=ui.series())
                    ),
                    tooltip=[
                        "account_id:N",
                        "channel:N",
                        alt.Tooltip("count():Q", title="Deposits"),
                        alt.Tooltip("sum(lakh):Q", title="₹ lakh", format=",.2f"),
                    ],
                )
                .properties(height={"step": 40})
            )
            ui.chart(per_account)
        else:
            cols = [
                "txn_id",
                "txn_timestamp",
                "account_id",
                "amount",
                "channel",
                "txn_type",
                "branch_code",
                "source_ref",
            ]
            st.dataframe(ui.pretty(shown[cols]), hide_index=True, width="stretch")

    # A model reads the same legs and describes the pattern. Beside the evidence, never in it.
    with ui.card(
        "triage",
        "Triage summary",
        "A Cortex model reads the same legs and describes the pattern. Nothing it writes "
        "reaches the filing.",
        glyph="auto_awesome",
        tone="violet",
        aside=ui.pill("Cortex AI", "violet", glyph="auto_awesome"),
    ):
        asked = st.button(
            "Summarise the evidence",
            key=f"triage_{case_id}",
            icon=":material/auto_awesome:",
        )
        if asked:
            st.session_state.setdefault("triage", {})[case_id] = True
        if st.session_state.get("triage", {}).get(case_id):
            with st.spinner("Cortex AI is reading the evidence legs..."):
                summary = triage.summarise(case_id)
            if summary.get("error"):
                st.warning(f"The model is unavailable here: {summary['error'][:200]}")
            else:
                st.markdown(summary["text"])
                st.caption(
                    f"{triage.MODEL} over the {summary['legs']} evidence legs "
                    f"({triage.PROMPT_VERSION}, {summary['latency_ms']:,} ms), logged to the audit "
                    "trail. An analyst aid, not evidence: it is never written into the STR, "
                    "whose every figure comes from SQL."
                )


# ---- the STR workflow: gather -> draft -> review ---------------------------- #
arts = data.case_artifacts(case_id)
latest = arts.iloc[0].to_dict() if not arts.empty else None
filed = bool((arts["status"] == "FILED").any()) if not arts.empty else False


def run(label: str, fn) -> None:
    try:
        with st.spinner(label):
            result = fn()
        st.session_state["last_action"] = (True, label, result)
    except Exception as exc:  # shown to the user, verbatim
        st.session_state["last_action"] = (False, label, reason(exc))
    st.rerun()


maker = auth.can("can_investigate")
locked = filed or not maker
gathered = chain["evidence"] > 0
can_review = maker and latest is not None and latest["status"] == "DRAFT"
# The next step is the primary button, so the page always says what to do next.
# An approved STR's next step is the officer's sign-off, so no button is primary.
redraft = latest is None or latest["status"] == "REJECTED"
next_step = (
    None
    if locked
    else "review"
    if can_review
    else ("draft" if gathered else "gather")
    if redraft
    else None
)
with ui.card(
    "workflow",
    "Investigate, draft and review the STR",
    "Each step calls one governed stored procedure, and every step is written to the audit log.",
    glyph="account_tree",
    tone="emerald",
    aside=ui.pill("Maker" if maker else "Checker", "violet" if maker else "emerald"),
):
    b = st.columns([1, 1, 1, 2], vertical_alignment="center")
    plant = b[3].toggle(
        "Plant a defect in the next draft",
        help="Demo: inflates the deposit total by 10% and cites a clause that does not exist. "
        "Watch the Reviewer catch both.",
        disabled=locked,
    )
    if b[0].button(
        "1 · Gather evidence",
        type="primary" if next_step == "gather" else "secondary",
        width="stretch",
        disabled=locked,
        icon=":material/travel_explore:",
    ):
        run("Gathering evidence", lambda: call(PROC["gather_evidence"], case_id))
    if b[1].button(
        "2 · Draft STR",
        type="primary" if next_step == "draft" else "secondary",
        width="stretch",
        disabled=locked,
        icon=":material/edit_document:",
    ):
        run("Drafting the STR", lambda: call(PROC["draft_str"], case_id, bool(plant)))
    if b[2].button(
        "3 · Run Reviewer",
        type="primary" if next_step == "review" else "secondary",
        width="stretch",
        disabled=not can_review,
        icon=":material/rule:",
    ):
        run("Reviewing", lambda: call(PROC["review_str"], latest["artifact_id"]))
    if not maker and not filed:
        st.info(
            "Maker-checker: the Principal Officer signs STRs but does not draft them. "
            "Sign in as the AML Analyst to investigate.",
            icon=":material/lock:",
        )

last = st.session_state.pop("last_action", None)
if last:
    ok, label, result = last
    if ok:
        st.toast(f"{label}: done", icon=":material/check_circle:")
    else:
        st.error(f"{label} refused: {result}")


# ---- the agentic path: the STR agent's tools, run inside Snowflake ---------- #
with st.container(border=True):
    a1, a2 = st.columns([3, 1], vertical_alignment="center")
    a1.markdown(
        "**AI STR (agent tools in Snowflake)**: the STR agent's drafter (an Anthropic model) "
        "writes the narrative, a non-Anthropic model cross-reviews it, and every amount, count and "
        "date is traced to this case's logged evidence. An approved draft is written back here as "
        "a new version and re-checked by the Reviewer. One procedure runs it all, about a minute."
    )
    ok_agent, why = agent.available()
    if a2.button(
        "4 · AI STR + PDF",
        type="secondary",
        width="stretch",
        disabled=locked or not ok_agent,
        help=why or "Runs GENERATE_AI_STR inside Snowflake (about a minute).",
    ):
        with st.status("Running the STR agent...", expanded=True) as box:
            res = agent.generate(
                case_id,
                progress=lambda step, msg: box.write(f"`{step}` {msg}"),
                user=(auth.current() or {}).get("username", "unknown"),
            )
            failed = res.get("error") and res.get("status") != "REFUSED"
            box.update(
                label=f"STR agent: {res.get('status')}",
                state="error" if failed else "complete",
            )
        st.session_state.setdefault("ai_str", {})[case_id] = res
        st.cache_data.clear()
    elif not ok_agent:
        a2.caption(why)
    res = st.session_state.get("ai_str", {}).get(case_id)
    if res:
        air, dr = res.get("ai_review") or {}, res.get("data_review") or {}
        if res.get("status") == "REFUSED":
            st.info(res.get("error") or "Refused by the demo's spend cap.")
        elif res.get("error") and not res.get("sections"):
            st.error(f"STR agent failed: {res['error']}")
        else:
            (st.success if res.get("status") == "READY_FOR_ATTESTATION" else st.warning)(
                f"**{res.get('status')}** · drafted by `{air.get('drafter_model')}` (attempt "
                f"{res.get('attempt')}) · agent reviewer `{air.get('reviewer_model')}`: "
                f"**{air.get('decision')}** · data-account Reviewer: "
                f"**{dr.get('decision', 'not run')}**"
            )
            for d in air.get("defects") or []:
                st.caption(f"- {d}")
            if res.get("pdf_bytes"):
                st.download_button(
                    "Download STR PDF",
                    data=res["pdf_bytes"],
                    file_name=res.get("pdf_name", f"STR_{case_id}.pdf"),
                    mime="application/pdf",
                    icon=":material/picture_as_pdf:",
                )
            st.caption(
                f"package `{res.get('package_id')}` · run `{res.get('run_id')}` · "
                f"PDF SHA-256 `{(res.get('pdf_sha256') or '')[:16]}...`"
            )
        if st.button("Refresh the STR below", key="ai_refresh"):
            st.rerun()


def evidence_chain() -> None:
    st.markdown("##### Evidence pointers")
    st.dataframe(ui.pretty(data.evidence_rows(case_id)), hide_index=True, width="stretch")
    st.markdown("##### Logged queries (the source of every figure)")
    st.dataframe(
        ui.pretty(data.query_logs(case_id).drop(columns=["sql_text"])),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Each query's exact text, bind value and canonical result hash are stored. "
        "The Reviewer re-runs them and compares."
    )


if latest is None and not gathered:
    ui.empty("No evidence gathered yet. Start with 1 · Gather evidence.", glyph="travel_explore")
elif latest is None:
    queries = len(data.query_logs(case_id))
    st.success(
        f"Evidence gathered: {chain['evidence']:,} pointer(s) and {queries:,} logged "
        "query(ies). No STR drafted yet. Next: **2 · Draft STR**."
        if maker
        else f"Evidence gathered: {chain['evidence']:,} pointer(s) and {queries:,} logged "
        "query(ies). The AML Analyst drafts the STR next.",
        icon=":material/inventory:",
    )
    with st.expander("What was gathered", expanded=True, icon=":material/inventory_2:"):
        evidence_chain()
else:
    if latest["status"] == "REVIEWED":
        s1, s2 = st.columns([3, 1], vertical_alignment="center")
        s1.success(
            "The Reviewer approved this STR. It now needs the Principal Officer's signature.",
            icon=":material/verified:",
        )
        if s2.button("Go to sign-off", width="stretch", icon=":material/arrow_forward:"):
            st.switch_page("app_pages/signoff.py")
    elif filed:
        st.success(
            "STR attested by the Principal Officer and filed. The case is closed.",
            icon=":material/task_alt:",
        )
    t1, t2, t3 = st.tabs(
        [
            ":material/description: Latest STR",
            ":material/history: Version history",
            ":material/link: Evidence chain",
        ]
    )
    with t1:
        left, right = st.columns([3, 2], gap="medium")
        with left, ui.card("str", glyph="description"):
            a = data.artifact(latest["artifact_id"])
            if a:
                str_document(a)
        with (
            right,
            ui.card(
                "review",
                "Reviewer",
                "Deterministic checks, no model involved.",
                glyph="fact_check",
                tone="emerald",
            ),
        ):
            review_result(latest)
    with t2:
        hist = arts[["version", "artifact_id", "status", "review_decision", "created_at"]].copy()
        hist["meaning"] = hist["status"].map(STATUS_HELP)
        st.dataframe(ui.pretty(hist), hide_index=True, width="stretch")
        for _, row in arts.iterrows():
            if row["review_decision"] == "REJECT":
                with st.expander(
                    f"v{row['version']} -- why the Reviewer rejected it", icon=":material/cancel:"
                ):
                    review_result(row.to_dict())
    with t3:
        evidence_chain()
