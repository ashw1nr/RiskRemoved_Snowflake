"""Risk Copilot Slack Bot -- STR agent + 'Ask the Data'.

Connects to Slack in Socket Mode (no public URL needed).

    @RiskCopilot str CASE-...        generate an AI STR end to end: evidence from
                                     the data account -> RC_STR_AGENT (draft +
                                     cross-family review) in the agent account ->
                                     write-back + REVIEW_STR -> PDF in the thread
    @RiskCopilot agent <question>    ask the STR agent directly
    (reply in an STR thread)         follow-up questions go to the agent, with the
                                     case's evidence package as context
    @RiskCopilot <question>          Cortex Analyst over the semantic view (data account)

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # fill in your tokens
    python slack_bot.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from tabulate import tabulate

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")  # reads .env file
sys.path.insert(0, str(HERE.parent / "src"))  # the shared risk_copilot.agentic package

from analyst_client import AnalystClient  # noqa: E402
from risk_copilot.agentic.agent_client import create_thread  # noqa: E402
from risk_copilot.agentic.chat_log import record_turn  # noqa: E402
from risk_copilot.agentic.str_pipeline import ask_agent, generate_ai_str, latest_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("risk_copilot_bot")

# ---------------------------------------------------------------------------
# Slack app (Socket Mode -- connects outbound, no ngrok needed)
# ---------------------------------------------------------------------------
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

analyst = AnalystClient()

# STR runs take minutes; Bolt must stay free to ack other events meanwhile.
_workers = ThreadPoolExecutor(max_workers=int(os.getenv("BOT_WORKERS", "4")))

# Slack thread ts -> STR context, so replies in the thread reach the agent.
_threads: dict[str, dict] = {}
_threads_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Strip the @mention tag from the message to get the actual question
# ---------------------------------------------------------------------------
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*")
_CASE_RE = re.compile(r"\bCASE-[0-9a-f]{6,}\b", re.I)
# Intent words may appear anywhere: "Give pdf report for CASE-...", "can you draft the STR for CASE-...".
_PDF_RE = re.compile(r"\b(pdf|download|report|document|send|share)\b", re.I)
_STR_RE = re.compile(r"\b(str|sar|generate|regenerate|draft|redraft|prepare|create|write|new)\b", re.I)
_ASK_RE = re.compile(
    r"\b(why|explain|reason|reasons|suspicious|suspicion|codes?|reviewer|review|defects?|clauses?|grounds|"
    r"typology|evidence|summari[sz]e|summary)\b", re.I)
_AGENT_RE = re.compile(r"^\s*agent\b[:,]?\s*", re.I)


def _extract_question(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _route(question: str) -> tuple[str, str | None]:
    """('pdf'|'str'|'agent'|'analyst', case_id or None).

    pdf    send the current STR PDF for the case, generating one only if none is current
    str    always run a fresh AI STR
    agent  reasoning about a case (or anything prefixed 'agent')
    """
    m = _CASE_RE.search(question)
    case_id = "CASE-" + m.group(0)[5:].lower() if m else None  # ids are lowercase hex
    if _AGENT_RE.match(question):
        return "agent", case_id
    if case_id:
        wants_new = re.search(r"\b(generate|regenerate|draft|redraft|prepare|create|write|new)\b", question, re.I)
        if _PDF_RE.search(question) and not wants_new:
            return "pdf", case_id
        if _STR_RE.search(question):
            return "str", case_id
        if _ASK_RE.search(question):
            return "agent", case_id
    return "analyst", case_id


# ---------------------------------------------------------------------------
# Format the response for Slack (using Block Kit for rich formatting)
# ---------------------------------------------------------------------------
def _format_blocks(question: str, result: dict) -> list[dict]:
    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": "Risk Copilot", "emoji": True},
    })

    # The answer text
    answer = result.get("text", "No answer available.")
    if result.get("demo"):
        answer = f":test_tube: *Demo Mode* -- connect Snowflake for live data\n\n{answer}"
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": answer[:2900]},
    })

    # Data table (if rows exist)
    rows = result.get("rows", [])
    if rows:
        table_text = tabulate(rows, headers="keys", tablefmt="simple", maxcolwidths=30)
        # Slack code blocks have a 3000 char limit per block
        if len(table_text) > 2900:
            table_text = table_text[:2900] + "\n... (truncated)"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{table_text}\n```"},
        })

    # SQL (collapsible -- shown as a context block with the SQL)
    sql = result.get("sql")
    if sql:
        # Slack doesn't have native collapsible sections, so we use a smaller font
        sql_display = sql if len(sql) < 2500 else sql[:2500] + "\n-- truncated"
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":mag: *SQL generated by Cortex Analyst:*"},
            ],
        })
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{sql_display}\n```"},
        })

    # Footer
    latency = result.get("latency_ms", 0)
    mode = "demo" if result.get("demo") else "live"
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_{latency}ms | {mode} mode | Cortex Analyst_"},
        ],
    })

    return blocks


_STEP_LABELS = {
    "baseline": "Current STR draft located (data account)",
    "evidence": "Logged evidence collected",
    "analyst": "Cortex Analyst context added",
    "package": "Hashed evidence package sent to the agent account",
    "agent": "RC_STR_AGENT drafting + cross-reviewing",
    "agent_fallback": "Agent run incomplete -- tools called directly",
    "write_back": "Approved narrative written back as a new STR version",
    "data_review": "Data account REVIEW_STR re-checking",
    "pdf": "STR PDF rendered",
}


def _progress_blocks(case_id: str, done: list[str], current: str | None) -> list[dict]:
    lines = [f":white_check_mark: {_STEP_LABELS.get(s, s)}" for s in done]
    if current:
        lines.append(f":hourglass_flowing_sand: {_STEP_LABELS.get(current, current)}...")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Generating AI STR for `{case_id}`*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines) or "Starting..."}},
    ]


def _str_blocks(res: dict) -> list[dict]:
    s = res.get("sections") or {}
    air = res.get("ai_review") or {}
    dr = res.get("data_review") or {}
    status = {
        "READY_FOR_ATTESTATION": ":large_green_circle: *Ready for Principal Officer attestation*",
        "REJECTED_BY_AGENT_REVIEW": ":red_circle: *Rejected by the agent's reviewer -- not written back*",
        "REJECTED_BY_DATA_REVIEW": ":red_circle: *Rejected by the data account's REVIEW_STR*",
    }.get(res.get("status"), f"*{res.get('status')}*")
    codes = ", ".join(f"*{r.get('code')}*" for r in s.get("reason_codes", [])) or "-"
    defects = (air.get("defects") or []) + [d.get("message", str(d)) for d in dr.get("defects", [])]
    fields = [
        {"type": "mrkdwn", "text": f"*Case*\n`{res.get('case_id')}`"},
        {"type": "mrkdwn", "text": f"*STR artifact*\n`{res.get('artifact_id') or 'not written back'}`"},
        {"type": "mrkdwn", "text": f"*Drafted by*\n{air.get('drafter_model', '-')} (attempt {res.get('attempt', '-')})"},
        {"type": "mrkdwn", "text": f"*Agent reviewer*\n{air.get('decision', '-')} -- {air.get('reviewer_model', '-')}"},
        {"type": "mrkdwn", "text": f"*Figure check*\n{'PASS' if (air.get('numeric_check') or {}).get('passed') else 'FAIL'}"
                                   f" ({(air.get('numeric_check') or {}).get('tokens_checked', 0)} traced)"},
        {"type": "mrkdwn", "text": f"*Data-account REVIEW_STR*\n{dr.get('decision', 'not run')}"},
    ]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Risk Copilot -- AI STR draft", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": status}},
        {"type": "section", "fields": fields},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Summary*\n{s.get('summary_of_suspicion', '-')}\n\n*FIU-IND reason codes:* {codes}"}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": f"*Grounds of suspicion*\n{(s.get('grounds_of_suspicion') or '-')[:2800]}"}},
    ]
    if defects:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": "*Defects*\n" + "\n".join(f"- {d}" for d in defects[:8])}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        f"_package `{res.get('package_id')}` | run `{res.get('run_id')}` | DRAFT -- attestation only by the "
        f"Principal Officer in Risk Copilot | reply in this thread to ask the agent_"}]})
    return blocks


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
def _upload_pdf(channel: str, thread_ts: str, res: dict, comment: str) -> None:
    try:
        app.client.files_upload_v2(
            channel=channel, thread_ts=thread_ts, content=res["pdf_bytes"], filename=res["pdf_name"],
            title=f"STR {res.get('case_id')} ({res.get('artifact_status') or res.get('status')})",
            initial_comment=comment,
        )
    except Exception as exc:
        log.warning("PDF upload failed: %s", exc)
        app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":warning: Could not upload the PDF ({exc}). Add the `files:write` scope to the Slack app "
                 f"and reinstall it. The PDF is on `{res.get('stage_path')}` in the agent account.")


def _remember(thread_ts: str, case_id: str, package_id: str | None) -> None:
    with _threads_lock:
        _threads[thread_ts] = {"case_id": case_id, "package_id": package_id,
                               "agent_thread": create_thread(), "parent": 0}


def _run_pdf(channel: str, thread_ts: str, case_id: str, user: str) -> None:
    """Send the case's current STR PDF; generate one only when none is current."""
    rep = latest_report(case_id)
    if rep is None:
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text=f"No current STR PDF for `{case_id}` yet -- generating one now (about 3-4 minutes).")
        _run_str(channel, thread_ts, case_id, user)
        return
    _remember(thread_ts, case_id, rep.get("package_id"))
    note = {
        "REVIEWED": "reviewed, awaiting Principal Officer attestation",
        "FILED": "attested and filed (the PDF shows its pre-attestation state)",
        "DRAFT": "drafted, not yet reviewed",
    }.get(rep["artifact_status"], rep["artifact_status"])
    _upload_pdf(channel, thread_ts, rep,
                f":page_facing_up: Current STR for `{case_id}`: `{rep['artifact_id']}` -- {note}.\n"
                f"Generated {str(rep['created_at'])[:16]} | SHA-256 `{rep['pdf_sha256'][:16]}...` | "
                f"say `str {case_id}` to regenerate, or reply here to ask the agent.")


def _run_str(channel: str, thread_ts: str, case_id: str, user: str) -> None:
    progress = app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                           text=f"Generating AI STR for {case_id}...",
                                           blocks=_progress_blocks(case_id, [], "baseline"))
    done: list[str] = []
    current = {"step": None}

    def on_progress(step: str, msg: str) -> None:
        if step in ("done", "error"):
            return
        if current["step"]:
            done.append(current["step"])
        current["step"] = step
        try:
            app.client.chat_update(channel=channel, ts=progress["ts"], text=msg,
                                   blocks=_progress_blocks(case_id, done, step))
        except Exception:  # progress is cosmetic
            pass

    res = generate_ai_str(case_id, progress=on_progress, requested_by=f"slack:{user}", channel="slack")
    review = (res.get("data_review") or {}).get("decision", "not run")
    record_turn(f"str {case_id}",
                res.get("error") or f"AI STR {res.get('artifact_id') or ''}: {res.get('status')}, "
                f"data-account REVIEW_STR {review}",
                kind="ERROR" if res.get("error") else "STR", author=f"slack:{user}",
                thread_ref=thread_ts, case_id=case_id)
    if current["step"]:
        done.append(current["step"])
    app.client.chat_update(channel=channel, ts=progress["ts"], text="STR run finished",
                           blocks=_progress_blocks(case_id, done, None))

    if res.get("error") and not res.get("sections"):
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text=f":x: STR generation failed for {case_id}: {res['error'][:1500]}")
        return

    _remember(thread_ts, case_id, res.get("package_id"))
    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                text=f"AI STR draft for {case_id}: {res.get('status')}", blocks=_str_blocks(res))
    if res.get("pdf_bytes"):
        _upload_pdf(channel, thread_ts, res, f":page_facing_up: STR PDF -- SHA-256 `{res.get('pdf_sha256', '')[:16]}...`")


def _run_agent(channel: str, thread_ts: str, question: str, case_id: str | None = None) -> None:
    with _threads_lock:
        ctx = dict(_threads.get(thread_ts) or {})
    case_id = case_id or ctx.get("case_id")
    thinking = app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                           text=":brain: Asking the STR agent...")
    r = ask_agent(question, package_id=ctx.get("package_id") if ctx.get("case_id") == case_id else None,
                  case_id=case_id, thread_id=ctx.get("agent_thread"), parent_message_id=ctx.get("parent", 0))
    if ctx and r.assistant_message_id is not None:
        with _threads_lock:
            _threads[thread_ts]["parent"] = r.assistant_message_id
    text = r.text or (f":x: {r.error}" if r.error else "The agent returned no answer.")
    record_turn(question, text, kind="ERROR" if r.error else "AGENT", author="slack",
                thread_ref=thread_ts, case_id=case_id, latency_ms=r.latency_ms)
    tools = ", ".join(c.get("name") for c in r.tool_calls) or "none"
    app.client.chat_update(channel=channel, ts=thinking["ts"], text=text[:3000], blocks=[
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
        {"type": "context", "elements": [{"type": "mrkdwn",
                                          "text": f"_RC_STR_AGENT | tools: {tools} | {r.latency_ms}ms_"}]},
    ])


def _run_analyst(channel: str, thread_ts: str | None, question: str, update_ts: str | None = None,
                 user: str = "unknown") -> None:
    result = analyst.ask(question)
    log.info("Answer: %s (%dms, demo=%s)", result.get("text", "")[:80], result.get("latency_ms", 0), result.get("demo"))
    if not result.get("demo"):  # canned demo answers are not part of the shared record
        record_turn(question, result.get("text") or result.get("error") or "",
                    kind="ERROR" if result.get("error") else "ANALYST", author=f"slack:{user}",
                    thread_ref=thread_ts, sql=result.get("sql"), rows=len(result.get("rows") or []),
                    latency_ms=result.get("latency_ms"))
    blocks = _format_blocks(question, result)
    try:
        if update_ts:
            app.client.chat_update(channel=channel, ts=update_ts, text=result.get("text", ""), blocks=blocks)
            return
    except Exception:
        pass
    # If update fails (e.g., permissions), send a new message instead
    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=result.get("text", ""), blocks=blocks)


def _dispatch(channel: str, thread_ts: str, question: str, user: str, in_thread: bool, say) -> None:
    kind, case_id = _route(question)
    with _threads_lock:
        str_thread = in_thread and thread_ts in _threads
    if kind == "pdf":
        _workers.submit(_guard, _run_pdf, channel, thread_ts, case_id, user)
    elif kind == "str":
        _workers.submit(_guard, _run_str, channel, thread_ts, case_id, user)
    elif kind == "agent" or str_thread:
        _workers.submit(_guard, _run_agent, channel, thread_ts, _AGENT_RE.sub("", question), case_id)
    else:
        thinking = say(text=":hourglass_flowing_sand: Querying the data...", thread_ts=thread_ts)
        _workers.submit(_guard, _run_analyst, channel, thread_ts, question, thinking["ts"], user)


def _guard(fn, channel: str, thread_ts: str | None, *args) -> None:
    try:
        fn(channel, thread_ts, *args)
    except Exception as exc:
        log.exception("worker failed")
        app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f":x: {exc}")


_HELP = (
    "*Risk Copilot* -- try:\n"
    "- `pdf CASE-63708232dec1c0d6` -- the case's current STR PDF (generated if there is none yet)\n"
    "- `str CASE-ca1079bca1c29184` -- a fresh AI STR draft + PDF (drafted and cross-reviewed by the STR agent)\n"
    "- `why is CASE-63708232dec1c0d6 suspicious?` -- the STR agent explains a case from its evidence\n"
    "- `agent which FIU-IND codes fit structuring?` -- ask the STR agent anything\n"
    "- `Which branch has the most cash deposits?` -- Cortex Analyst over the data\n"
    "Reply inside an STR thread to ask the agent about that case."
)


# ---------------------------------------------------------------------------
# Event handler: @mention
# ---------------------------------------------------------------------------
@app.event("app_mention")
def handle_mention(event, say):
    question = _extract_question(event.get("text", ""))
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user", "unknown")

    if not question or question.lower() in ("help", "?"):
        say(text=_HELP, thread_ts=thread_ts)
        return

    log.info("Question from %s: %s", user, question)
    _dispatch(event["channel"], thread_ts, question, user, bool(event.get("thread_ts")), say)


# ---------------------------------------------------------------------------
# Handle DMs too (same logic)
# ---------------------------------------------------------------------------
@app.event("message")
def handle_dm(event, say):
    if event.get("channel_type") != "im":
        return  # only handle DMs here; channel messages go through app_mention
    if event.get("bot_id") or event.get("subtype"):
        return  # ignore the bot's own messages and edits/file shares

    question = event.get("text", "").strip()
    if not question:
        return
    if question.lower() in ("help", "?"):
        say(text=_HELP)
        return

    log.info("DM question: %s", question)
    thread_ts = event.get("thread_ts") or event.get("ts")
    _dispatch(event["channel"], thread_ts, question, event.get("user", "unknown"), bool(event.get("thread_ts")), say)


# ---------------------------------------------------------------------------
# App Home tab: show the help text instead of Slack's placeholder
# ---------------------------------------------------------------------------
@app.event("app_home_opened")
def handle_home(event, client):
    if event.get("tab") != "home":
        return
    try:
        client.views_publish(user_id=event["user"], view={
            "type": "home",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": _HELP}}],
        })
    except Exception as exc:  # Home tab is cosmetic
        log.warning("Home tab publish failed: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mode = "DEMO" if analyst.demo_mode else "LIVE"
    log.info("Starting Risk Copilot Slack bot (%s mode)...", mode)
    log.info("Semantic view: %s", analyst.semantic_view)
    log.info("STR agent: %s in %s", os.getenv("AGENT_NAME", "RC_STR_AGENT"),
             os.getenv("AGENT_SNOWFLAKE_ACCOUNT", "<AGENT_SNOWFLAKE_ACCOUNT not set>"))

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()  # blocks forever, listening for Slack events
