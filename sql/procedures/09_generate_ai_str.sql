-- ===========================================================================
-- procedures / 09_generate_ai_str.sql
-- ===========================================================================
-- GENERATE_AI_STR(case_id): the AI STR, end to end, inside Snowflake.
--
-- What the laptop bridge (risk_copilot.agentic.str_pipeline) did, as one
-- owner's-rights procedure a hosted app can call with nothing but USAGE:
--
--   1. spend cap      one run per case per cooldown, a daily cap (public URL)
--   2. baseline       the open DRAFT/REVIEWED artifact, else DRAFT_STR
--   3. package        logged evidence + semantic-view context, PAN masked,
--                     handed to the agent's PUT_EVIDENCE_PACKAGE (hashed there)
--   4. agent tools    DRAFT_STR_NARRATIVE -> REVIEW_STR_DRAFT, one redraft on
--                     REJECT (the agent's own order; a cross-family reviewer)
--   5. write-back     on APPROVE: SUBMIT_AI_DRAFT, then REVIEW_STR re-proves
--                     every figure and the narrative trace (no model)
--   6. PDF            RENDER_STR_PDF; its bytes come back base64
--
-- No number comes from a model: SUBMIT_AI_DRAFT copies the computed values and
-- REVIEW_STR rejects any figure in the prose that is not one of them.
-- The agent side holds no grant on this database (decision 3); this role calls
-- its tools through the USAGE granted in 11_app_access/02_agent_tools.sql.
-- ===========================================================================
{% set EV = fq.evidence %}
{% set T = tool %}
{% set AG = cortex.agent_schema_fq %}
{% set CFG = {
    "tool": T.generate_ai_str,
    "draft_str": EV ~ "." ~ T.draft_str,
    "submit": EV ~ "." ~ T.submit_ai_draft,
    "review": EV ~ "." ~ T.review_str,
    "ev": EV,
    "audit": fq.audit,
    "agent": AG,
    "semantic_view": fq.curated ~ "." ~ cortex.semantic_view,
    "database": db,
    "cooldown_minutes": cortex.ai_str.cooldown_minutes,
    "daily_runs": cortex.ai_str.daily_runs,
    "reporting_bank": cortex.ai_str.reporting_bank,
    "principal_officer": cortex.ai_str.principal_officer,
} %}
{% if AG %}
CREATE OR REPLACE PROCEDURE {{ EV }}.{{ T.generate_ai_str }}(CASE_ID VARCHAR)
    COPY GRANTS
    RETURNS VARIANT
    LANGUAGE PYTHON
    RUNTIME_VERSION = '3.11'
    PACKAGES = ('snowflake-snowpark-python')
    HANDLER = 'run'
    COMMENT = 'AI STR end to end in Snowflake: evidence package -> agent tools (draft, cross-family review) -> SUBMIT_AI_DRAFT -> REVIEW_STR -> PDF. Spend-capped.'
    EXECUTE AS OWNER
AS
$$
import hashlib
import json
import time

CFG = {{ CFG | tojson }}


def _v(x):
    return json.loads(x) if isinstance(x, str) else x


def _rows(s, sql, params=None):
    return [r.as_dict() for r in s.sql(sql, params=params or []).collect()]


def _call(s, proc, *args, json_at=None):
    """CALL proc(args); the argument at index json_at is a VARIANT, sent as JSON text."""
    marks = ", ".join("PARSE_JSON(?)" if i == json_at else "?" for i in range(len(args)))
    row = s.sql(f"CALL {proc}({marks})", params=list(args)).collect()
    return _v(row[0][0]) if row else None


def _audit(s, case_id, outcome, started, args, reason=None, error=None):
    s.sql(
        f"""INSERT INTO {CFG['audit']}.AGENT_ACTION_LOG (
                log_id, session_id, turn_number, agent_mode, invoked_by, snowflake_user, user_role,
                tools_invoked, tool_arguments, case_ids_touched, model_version, prompt_version,
                latency_ms, outcome, refusal_reason, error_text)
            SELECT 'LOG-' || UUID_STRING(), CURRENT_SESSION()::VARCHAR, 1, 'AGENTIC', 'USER',
                   CURRENT_USER(), CURRENT_ROLE(), ARRAY_CONSTRUCT(?), PARSE_JSON(?),
                   ARRAY_CONSTRUCT(?), 'agent-tools', 'agent-str-v1', ?, ?, ?, ?""",
        params=[CFG["tool"], json.dumps(args, default=str), case_id,
                int((time.time() - started) * 1000), outcome, reason, error],
    ).collect()


def _cap(s, case_id):
    """A refusal message, or None. Started runs are logged as PARTIAL."""
    rows = _rows(
        s,
        f"""SELECT COUNT_IF(logged_at >= DATEADD('minute', -1 * ?, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ)
                            AND ARRAY_CONTAINS(?::VARIANT, case_ids_touched)) AS recent,
                   COUNT(*) AS today
            FROM {CFG['audit']}.AGENT_ACTION_LOG
            WHERE ARRAY_CONTAINS(?::VARIANT, tools_invoked) AND outcome = 'PARTIAL'
              AND logged_at >= DATEADD('day', -1, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ)""",
        [CFG["cooldown_minutes"], case_id, CFG["tool"]],
    )[0]
    if int(rows["RECENT"] or 0) > 0:
        wait = CFG["cooldown_minutes"]
        return f"An AI STR for {case_id} was started in the last {wait} minutes. Open it below, or try again shortly."
    if int(rows["TODAY"] or 0) >= CFG["daily_runs"]:
        return f"The demo's daily limit of {CFG['daily_runs']} AI STR runs is reached. Try again tomorrow."
    return None


def _baseline(s, case_id):
    cur = _rows(
        s,
        f"""SELECT a.artifact_id, a.status FROM {CFG['ev']}.ARTIFACT a
            JOIN {CFG['ev']}.FINDING f ON f.finding_id = a.finding_id
            WHERE f.case_id = ? ORDER BY f.version DESC LIMIT 1""",
        [case_id],
    )
    if cur and cur[0]["STATUS"] == "FILED":
        raise ValueError(f"{case_id} already has a filed STR ({cur[0]['ARTIFACT_ID']})")
    if cur and cur[0]["STATUS"] in ("DRAFT", "REVIEWED"):
        return cur[0]["ARTIFACT_ID"]
    return _call(s, CFG["draft_str"], case_id, False)["artifact_id"]


def _mask_pan(pan):
    return (pan[:4] + "****" + pan[-2:]) if pan and len(pan) >= 6 else pan


def _context(s, party_id):
    """Background for the drafter from the semantic view (never quoted as a figure)."""
    sv = CFG["semantic_view"]
    out = []
    for question, sql in (
        ("Transactions for this customer by channel and type",
         f"""SELECT * FROM SEMANTIC_VIEW({sv}
                 METRICS transactions.total_amount_inr, transactions.transaction_count
                 DIMENSIONS transactions.channel, transactions.txn_type, customers.party_id)
             WHERE party_id = ? ORDER BY total_amount_inr DESC LIMIT 20"""),
        ("Cases and signals for this customer by status and rule",
         f"""SELECT * FROM SEMANTIC_VIEW({sv}
                 METRICS cases.case_count, signals.signal_count
                 DIMENSIONS cases.case_status, signals.rule_id, customers.party_id)
             WHERE party_id = ? LIMIT 20"""),
    ):
        try:
            rows = _rows(s, sql, [party_id])
            out.append({"question": question, "rows": [{k.lower(): v for k, v in r.items()} for r in rows],
                        "source": "semantic_view"})
        except Exception as exc:  # context only: a failed query must not stop the STR
            out.append({"question": question, "error": str(exc)[:200], "source": "semantic_view"})
    return out


def _package(s, case_id, artifact_id):
    ev = CFG["ev"]
    art = _rows(s, f"SELECT content, content_hash, finding_id FROM {ev}.ARTIFACT WHERE artifact_id = ?",
                [artifact_id])[0]
    content = _v(art["CONTENT"])
    logs = _rows(
        s,
        f"""SELECT q.query_log_id, q.bind_values:name::VARCHAR AS name, q.result_hash,
                   q.result_row_count AS row_count, q.executed_at, q.result_snapshot
            FROM {ev}.EVIDENCE_QUERY_LOG q JOIN {ev}.EVIDENCE e ON e.evidence_id = q.evidence_id
            WHERE e.case_id = ? ORDER BY q.query_log_id""",
        [case_id],
    )
    clauses = next((_v(lg["RESULT_SNAPSHOT"]) for lg in logs if lg["NAME"] == "clauses_in_force"), [])
    signals = _rows(
        s,
        f"""SELECT s.signal_id, s.rule_id, s.rule_version, s.severity, s.window_bucket, s.fired_at,
                   s.metric_value, s.threshold_value, s.detail
            FROM {ev}.SIGNAL s JOIN {ev}.CASE_SIGNAL cs ON cs.signal_id = s.signal_id
            WHERE cs.case_id = ? ORDER BY s.fired_at""",
        [case_id],
    )
    signals = [{k.lower(): (_v(v) if k == "DETAIL" else v) for k, v in r.items()} for r in signals]
    case = _rows(
        s,
        f"""SELECT case_id, party_id, case_type, status, branch_region, opened_at
            FROM {ev}.CASE_RECORD WHERE case_id = ?""",
        [case_id],
    )[0]
    case = {k.lower(): v for k, v in case.items()}
    subject = dict(content.get("subject") or {})
    subject["PAN_NUMBER"] = _mask_pan(subject.get("PAN_NUMBER"))
    account = s.sql("SELECT CURRENT_ACCOUNT()").collect()[0][0]
    return account, {
        "case": {**case, "suspicion_established_at": content.get("suspicion_established_at"),
                 "filing_deadline": content.get("filing_deadline"), "typology_id": content.get("typology_id")},
        "subject": subject,
        "computed_values": content.get("computed_values", {}),
        "transactions": content.get("transactions", []),
        "signals": signals,
        "clauses": clauses,
        "cited_clause_ids": content.get("cited_clause_ids", []),
        "query_logs": [{"query_log_id": lg["QUERY_LOG_ID"], "name": lg["NAME"], "result_hash": lg["RESULT_HASH"],
                        "row_count": lg["ROW_COUNT"], "executed_at": lg["EXECUTED_AT"]} for lg in logs],
        "baseline": {"artifact_id": artifact_id, "finding_id": art["FINDING_ID"],
                     "content_hash": art["CONTENT_HASH"], "narrative_author": content.get("narrative_author"),
                     "grounds_of_suspicion": content.get("grounds_of_suspicion")},
        "source": {"account": account, "database": CFG["database"]},
        "reporting_entity": {"bank_name": CFG["reporting_bank"], "principal_officer": CFG["principal_officer"]},
        "analyst_context": _context(s, case["party_id"]),
    }


def _latest(s, package_id):
    ag = CFG["agent"]
    rows = _rows(
        s,
        f"""SELECT d.draft_id, d.attempt, d.status, d.model, d.prompt_version, d.sections, d.error,
                   r.decision, r.defects, r.numeric_check, r.reviewer_model
            FROM {ag}.AI_DRAFT d LEFT JOIN {ag}.AI_REVIEW r ON r.draft_id = d.draft_id
            WHERE d.package_id = ? ORDER BY d.attempt DESC, r.created_at DESC NULLS LAST LIMIT 1""",
        [package_id],
    )
    if not rows:
        return None
    row = {k.lower(): v for k, v in rows[0].items()}
    for k in ("sections", "defects", "numeric_check"):
        row[k] = _v(row[k])
    return row


def _agent_tools(s, package_id):
    """The agent's tools in the agent's order: draft, review, one redraft on REJECT."""
    ag = CFG["agent"]
    d = _call(s, f"{ag}.DRAFT_STR_NARRATIVE", package_id, None)
    if (d or {}).get("status") != "DRAFTED":
        return
    r = _call(s, f"{ag}.REVIEW_STR_DRAFT", d["draft_id"])
    if (r or {}).get("decision") == "REJECT":
        d2 = _call(s, f"{ag}.DRAFT_STR_NARRATIVE", package_id, "\n".join(r.get("defects", [])))
        if (d2 or {}).get("status") == "DRAFTED":
            _call(s, f"{ag}.REVIEW_STR_DRAFT", d2["draft_id"])


def _run_log(s, run_id, case_id, out, steps, started_iso):
    keep = {k: v for k, v in out.items() if k not in ("pdf_b64", "sections")}
    try:
        s.sql(
            f"""INSERT INTO {CFG['agent']}.AGENT_RUN_LOG
                (run_id, case_id, channel, requested_by, status, steps, result, error, started_at, finished_at)
                SELECT ?, ?, 'snowflake', CURRENT_USER(), ?, PARSE_JSON(?), PARSE_JSON(?), ?,
                       ?::TIMESTAMP_NTZ, SYSDATE()""",
            params=[run_id, case_id, out["status"], json.dumps(steps), json.dumps(keep, default=str),
                    out.get("error"), started_iso],
        ).collect()
    except Exception:  # the run stands on its own; the audit row below still records it
        pass


def run(session, case_id):
    s = session
    started = time.time()
    started_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(started))
    run_id = "RUN-" + hashlib.sha256(f"{case_id}|{started}".encode()).hexdigest()[:16]
    out = {"run_id": run_id, "case_id": case_id, "status": "ERROR", "orchestrator": "snowflake"}
    steps = []

    def step(name):
        steps.append({"step": name, "at": round(time.time() - started, 1)})

    refused = _cap(s, case_id)
    if refused:
        _audit(s, case_id, "REFUSED", started, {"case_id": case_id}, reason=refused)
        return {**out, "status": "REFUSED", "error": refused}
    _audit(s, case_id, "PARTIAL", started, {"case_id": case_id, "run_id": run_id, "phase": "started"})

    try:
        step("baseline")
        base_aid = _baseline(s, case_id)
        out["base_artifact_id"] = base_aid

        step("package")
        account, payload = _package(s, case_id, base_aid)
        pkg = _call(s, f"{CFG['agent']}.PUT_EVIDENCE_PACKAGE", case_id, account, CFG["database"], base_aid,
                    json.dumps(payload, default=str), json_at=4)
        package_id = pkg["package_id"]
        out.update(package_id=package_id, payload_hash=pkg["payload_hash"])

        step("agent_tools")
        _agent_tools(s, package_id)
        draft = _latest(s, package_id)
        if draft is None or draft.get("status") != "DRAFTED":
            raise ValueError(f"No usable draft: {(draft or {}).get('error') or 'the drafter returned nothing'}")
        sections = draft["sections"] or {}
        ai_review = {
            "decision": draft["decision"], "reviewer_model": draft["reviewer_model"],
            "defects": draft.get("defects") or [], "numeric_check": draft.get("numeric_check") or {},
            "draft_id": draft["draft_id"], "attempt": draft["attempt"], "drafter_model": draft["model"],
            "package_id": package_id, "payload_hash": pkg["payload_hash"], "agent_account": account,
            "agent": CFG["agent"], "orchestrator": "snowflake",
            "reason_codes": [r.get("code") for r in sections.get("reason_codes", [])],
        }
        out.update(draft_id=draft["draft_id"], attempt=draft["attempt"], sections=sections, ai_review=ai_review)

        target_aid = target_hash = data_review = None
        if draft["decision"] == "APPROVE":
            step("write_back")
            sub = _call(s, CFG["submit"], base_aid, sections["grounds_of_suspicion"], draft["model"],
                        json.dumps(ai_review, sort_keys=True, default=str), draft["prompt_version"], json_at=3)
            target_aid, target_hash = sub["artifact_id"], sub["content_hash"]
            step("data_review")
            data_review = _call(s, CFG["review"], target_aid)
            out.update(artifact_id=target_aid, content_hash=target_hash, data_review=data_review)
            out["status"] = ("READY_FOR_ATTESTATION" if data_review.get("decision") == "APPROVE"
                             else "REJECTED_BY_DATA_REVIEW")
        else:
            out["status"] = "REJECTED_BY_AGENT_REVIEW"

        step("pdf")
        rpt = _call(s, f"{CFG['agent']}.RENDER_STR_PDF", draft["draft_id"], target_aid, target_hash,
                    json.dumps(data_review, default=str) if data_review else None)
        out.update(stage_path=rpt["stage_path"], pdf_sha256=rpt["pdf_sha256"], pdf_b64=rpt.get("pdf_b64"),
                   pdf_name=f"STR_{case_id}_{target_aid or draft['draft_id']}.pdf")
        step("done")
        _audit(s, case_id, "SUCCESS", started,
               {"case_id": case_id, "run_id": run_id, "status": out["status"], "artifact_id": target_aid})
    except Exception as exc:
        out["error"] = str(exc)[:1500]
        try:
            _audit(s, case_id, "ERROR", started, {"case_id": case_id, "run_id": run_id}, error=out["error"])
        except Exception:
            pass
    _run_log(s, run_id, case_id, out, steps, started_iso)
    return out
$$;
{% endif %}
