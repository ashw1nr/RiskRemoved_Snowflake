# Architecture and infrastructure decisions

Why each choice was made, what it is, and how it is built. Numbers in brackets
(D41, M47) point to `docs/build-log.md`, where the measurement behind a choice is
recorded. Everything marked *verified* was run on the current account
(`IBIEQZU-HF18990`, Standard edition, AWS ap-northeast-1).

---

## 1. No number in a filing comes from a model

**Why.** An STR is a legal filing signed by the Principal Officer (PML Rules,
Rule 7). A hallucinated amount in it is a compliance failure, not a UX bug.

**What.** Every figure is computed in SQL, logged with its query and a hash, and
re-proven before anyone can sign. A model may write prose; it may not supply a
number.

**How.**
- `GATHER_EVIDENCE` runs each evidence query and stores its text, binds,
  result snapshot and a canonical SHA-256 (`EVIDENCE_QUERY_LOG`, form SQL-1).
- `DRAFT_STR` fills the template from those logged results only
  (`FINDING.computed_values`, each tied to a `query_log_id`).
- `REVIEW_STR` (no model) re-runs every logged query and compares hashes and
  figures (`numeric_recompute`), resolves every cited clause and evidence id,
  checks each clause was in force on every deposit date, and validates the
  schema.
- **`narrative_trace`** (added Nov 2026): every rupee amount, date and count in
  the prose must be the display string of a computed value or the subject's KYC
  record, and lakh/crore rounding is rejected. *Verified:* a self-approved AI
  narrative stating ₹1,12,40,000 and "94 lakh" was REJECTED with three defects;
  the genuine template draft was APPROVED; the planted-defect demo still shows
  exactly its two defects.
- `ATTEST_STR` refuses unless the latest review is APPROVE **and** its hash
  equals the content's hash now; the attestation records that hash.

## 2. Deterministic reviewer, cross-family AI reviewer: both, in that order

**Why.** An LLM reviewer catches tone, omissions and unsupported claims; it
cannot be trusted to catch a wrong number. A SQL reviewer catches numbers; it
cannot judge prose.

**What.** The agent's own reviewer (openai-gpt-5.1 / llama3.3-70b, never the
drafter's model family) gates writing back. The data account's `REVIEW_STR`
gates filing. The agent's `APPROVE` is passed to `SUBMIT_AI_DRAFT` but proves
nothing on its own; the write-back is a DRAFT until `REVIEW_STR` approves it.

**How.** `GENERATE_AI_STR` (`sql/procedures/09_generate_ai_str.sql`, Nov 2026)
runs it all inside Snowflake, so a hosted app needs only USAGE on that one
procedure: spend cap → DRAFT_STR baseline → evidence package (hashed by the
agent's `PUT_EVIDENCE_PACKAGE`) with semantic-view context and Cortex Search
regulation excerpts → `RC_STR_AGENT` via `DATA_AGENT_RUN` (it chooses its tool
calls: draft with claude-sonnet-4-6, cross-review, one redraft on REJECT; the
same tools in that order if the agent stops short) → `SUBMIT_AI_DRAFT` →
`REVIEW_STR` → PDF, returned base64. *Verified as the public service user:*
four cases, `READY_FOR_ATTESTATION` in 66-102 s, agent chose its own tools;
one miscounted draft ("13 deposits" for 12) was rejected by the agent's
figure check. The laptop pipeline (`str_pipeline.py`) still serves the Slack
bot.

**Spend cap.** The button is on a public URL: one run per case per 5 minutes
and 30 a day (`conf/base.yml:cortex`), counted from `AGENT_ACTION_LOG`; a
refusal is logged REFUSED.

**Regulation text (unstructured).** `REGULATORY_SEARCH_SVC`
(`sql/07_semantic/02_regulatory_search.sql`) indexes the governed clauses. The
drafter cites the retrieved clause; numbers in an excerpt are not allowed
figures, and `REVIEW_STR` still rejects any figure that is not computed.
*Verified:* the hero narrative cites PML Rules Rule 3(1)(A).

## 3. One Snowflake account for data and agent (was two)

**Why.** The first data account blocked `AI_COMPLETE` (399258) and agent
invocation (399504), so the agent was built in a second account and a bridge
copied evidence between them. The new account serves both (capability probe:
`conf/generated/capabilities.4ASHWINRAVI0045.yml`, 98 probes).

**What.** `RISK_COPILOT_DEV` (data, evidence chain, app) and
`RISK_COPILOT_AGENT` (agent, tools, PDFs) are two databases in one account. The
agent runs as its own SERVICE user and role (`RC_AGENT_SVC` /
`RC_AGENT_ROLE`) with no grant on the data database: it still sees only the
hashed package it is handed. The data admin role (owner of `GENERATE_AI_STR`)
holds USAGE on the agent, its tools and warehouse, and SELECT on its drafts
and reviews (`sql/11_app_access/02_agent_tools.sql`); the public role holds
nothing in the agent database, and still nothing on `SUBMIT_AI_DRAFT`.

**How.** `bridge/.env` points both sides at the same account. The expired
account's id was removed from three code defaults; `AGENT_SNOWFLAKE_ACCOUNT` is
now required. The bridge stays, so the two could be split again by changing
`.env` alone.

## 4. Configuration in three tiers; no literal object name in SQL

**Why.** Two profiles (`dev`, `demo`) share one account. A literal name in SQL
would let a dev deploy overwrite demo objects; a threshold in code is an
ungoverned threshold.

**What / how.**
1. Credentials: `~/.snowflake/connections.toml` and key files under
   `~/.snowflake/keys`, never the repo. `scripts/setup_data_account.py` creates
   the key pair, registers it and writes the connection in one run.
2. Topology: `conf/base.yml` + `conf/profiles/<env>.yml`, rendered into Jinja
   SQL. The deployer refuses a literal `RISK_COPILOT`, `RC_*_WH`, role name.
3. Business thresholds: `GOVERNANCE.POLICY_CLAUSE`, seeded from
   `conf/detection.yml`, each clause citing its source. Detection SQL joins to
   it; a test fails if any other stage renders a threshold.

## 5. Dynamic tables for detection, a triggered task for routing

**Why.** Detection must refresh incrementally as data lands, and every signal
must become exactly one case however often the tables recompute.

**What.** `DT_TRANSACTIONS_ENRICHED` → signals DT (INCREMENTAL, explicit, so a
construct that forces FULL fails the deploy). A stream on the signals DT feeds
`ROUTE_SIGNALS`, which MERGEs on a deterministic business key
(`rule|party|window`), so re-derivation never duplicates a case (GAP-11).

**How.** `sql/02_dynamic_tables`, `sql/03_streams_tasks`. Case ids are derived
from the key, so the hero's case is `CASE-4399c4c86db5c6ea` on every rebuild
(*verified* on two accounts).

## 6. Live data without a laptop: the release flag

**Why.** The old live demo streamed parquet from the laptop (`riskcopilot
replay`), needed a manual refresh and a resumed task, and stopped when the
machine slept (M39). The hosted dashboard could not drive it at all.

**What.** Every leg is loaded once. `RAW_TRANSACTIONS.held_back` says whether
it has "arrived"; `DT_TRANSACTIONS_ENRICHED` (the only reader of the ledger)
filters `WHERE NOT held_back`, so nothing downstream can see a future leg.
`released_at` becomes the arrival time the app shows.

**How.** `SIM_CONTROL(action)` (`sql/procedures/08_simulator.sql`) arms (hold
back from `replay.hold_back_from`), ticks (next `sim_step` of booking time,
capped at `batch_legs`, whole timestamps so pairs stay together), injects a
party's burst, releases all, and starts/stops `SIM_TICK_TASK` (1 tick/min, stops
itself when empty). Each call refreshes the leaf DTs and routes. Held-back is by
booking time only, never a label. The equivalence fingerprint excludes the
arrival columns. *Verified:* ARM 3,764 / 6,300 / 2 cases; the task ticked twice
in two minutes on its own; INJECT opened the hero's case; RELEASE_ALL restored
10,064 legs and `verify` passed.

## 7. Realistic amounts: rank-preserving compression

**Why.** AMLworld is a corporate ledger. Converted at period FX rates, its tail
reached ₹2,422 crore per leg, and declared income (derived from inflow,
annualised from a 10-day window) had a median of ₹7.7 crore; the hero, a
salaried scientist, declared ₹1,960 crore, which made ₹94 lakh of deposits look
normal.

**What.** Above a pivot, an amount becomes `pivot × (x / pivot)^k`. Strictly
increasing, so the order of amounts and every laundering pattern's shape
survive. Injected structuring is untouched (its amounts are CTR fractions
resolved at load).

**How.** `fx.retail_scale` (pivot ₹1 lakh, k 0.5) in `reskin.py`;
`entities.income.compress` (pivot ₹10 lakh, k 0.35) in `entities.py`, applied
before the illicit cohort's under-declaration. *Result:* max leg ₹4.9 crore
(none over ₹10 crore); median income ₹31 lakh, max ₹2.06 crore; hero ₹38.5
lakh. Detection unchanged (10/10). Cost, stated: the false-positive control for
structuring now has no near-miss in dev (the one genuine near-miss was scaled
below the band), so it reports "none" rather than "0 of 1".

## 8. Labels are unreachable from anything a model or the app can touch

**Why.** If any prompt or app role can read the answer key, recall/precision
are meaningless.

**What / how.** Labels live in `GROUND_TRUTH`, granted to no consumer role; the
ledger has no label column (GAP-03). The app reads run-level scores through a
secure view (`V_EVAL_SCORES`). *Verified:* the hosted app's service user is
refused `TXN_LABELS`.

## 9. Maker-checker: what is enforced where

**What.** The analyst drafts and reviews; only the Principal Officer signs.
`ATTEST_STR` runs with the caller's rights and checks the compliance role is in
the session, and binds the signature to the reviewed hash.

**Hosted app (Nov 2026).** The public URL connects as two service users. Everyone
runs as `RC_PUBLIC_APP_USER` (role `RC_PUBLIC_APP`, which no longer holds the
compliance role, so `ATTEST_STR` refuses it and logs REFUSED). Only while the
officer persona is signed in does `app/lib/db.py` switch to
`RC_PUBLIC_OFFICER_USER` (role `RC_PUBLIC_OFFICER` = public role + compliance
role), from a `[snowflake_officer]` secrets section; the officer's password
comes from `[demo_auth]` in the same secrets and is not shown on the sign-in
page. So on the public URL the database, not the sign-in page, keeps anyone
else from signing. *Verified:* analyst session refused ("only the compliance
officer role may attest"); officer session passes the role check.

**Stated limit.** Two demo logins are still not personal identities. In
production each officer connects under their own role (SSO), and the same
database check separates duties on its own. In Streamlit in Snowflake the app
runs with the owner's rights, so there the sign-in page separates them.

## 10. One conversation across the dashboard and Slack

**Why.** The Ask page kept history in the Streamlit session and the Slack bot in
process memory, so the two never agreed.

**What / how.** `AUDIT.CHAT_MESSAGE` (append-only). The app (`app/lib/chat.py`)
and the bot (`risk_copilot.agentic.chat_log`) append every turn -- question,
answer, SQL, kind, channel -- with bound values; the Ask page shows the shared
feed newest first. Recording is best effort and never hides an answer.

## 11. Hosting: three runtimes, one code path

**What.** Local (`riskcopilot app run`), Streamlit in Snowflake (owner's rights,
container runtime), and Streamlit Community Cloud (key-pair service user from
secrets). `app/lib/db.py` picks the session; names come from a rendered
`app_config.json`; values are always bound. An expired token reconnects once
(M50).

## 12. Audit everything, including refusals and resets

Every procedure writes `AUDIT.AGENT_ACTION_LOG`: tool, arguments, role, latency,
outcome; a refused attestation is logged as REFUSED with its reason. Fixed in
Nov 2026: `GATHER_EVIDENCE` logged a phantom row each time `DRAFT_STR` called
it as a no-op (M47); it now logs only when it gathers something (*verified*:
Gather +1 row, Draft +0).

## 13. Measured capability, not assumed capability

A 98-probe sweep per account classifies every failure (absent, edition, trial,
privilege, precondition) and `doctor` turns features off accordingly. Three
earlier assumptions about the first account were wrong; the probe file, not
prose, is the source of truth.
