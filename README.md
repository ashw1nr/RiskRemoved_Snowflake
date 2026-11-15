# Risk & Compliance Copilot

An AML analyst's copilot that carries a detection signal all the way to a
regulator-ready STR filing, built Snowflake-native.

**The governing constraint:** no number in a regulatory filing is ever produced
by a language model. Every figure comes from SQL; the LLM selects, sequences and
narrates.

```
SIGNAL  ->  EVIDENCE  ->  FINDING  ->  ARTIFACT  ->  ATTESTATION
 rules      immutable      LLM        STR filing      human
 fire      row pointers  narrative   (all numbers    Principal
           + result       + citations  from SQL)      Officer
           snapshots
```

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Repo scaffold, layered config, CLI, `doctor`, capability gating | **Done** |
| 1 | Bootstrap + full DDL (28 tables, 7 schemas) | **Done, deployed** |
| 2 | Data synthesis: subgraph sampling, ID maps, reskin, injection, validation gates | **Done** (dev; demo profile gated, D24) |
| 3 | Load, dynamic tables, idempotent streams/tasks, replay harness | **Done** on dev |
| 4 | Streamlit-in-Snowflake demo app, case-tool procedures, Reviewer, human gate, semantic view + Analyst | **Done** on dev ([demo runbook](#demo-runbook)) |
| 5 | ML, evaluation suites, governance, agent object, `AI_COMPLETE` narrative | **Done** |
| 6 | New account (Nov 2026): data + STR agent in one account, retail-scaled data, in-Snowflake live simulator, narrative trace in the Reviewer, shared Ask/Slack conversation | **Done** on dev |

647 unit tests pass (3 skip without capability evidence for the configured
connection); all SQL templates render for both profiles; key-pair auth works
unattended. Build history and measurements: [`docs/build-log.md`](docs/build-log.md);
the why/what/how of every architectural choice:
[`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md).

**Account capability is measured, not assumed.** A 98-probe sweep against each
build account writes `conf/generated/capabilities.<connection>.yml`, which
`riskcopilot doctor` reads to auto-disable unavailable features. That file
overrides any prose claim in this repository.

The current account (`capabilities.4ASHWINRAVI0045.yml`, Standard edition,
`AWS_AP_NORTHEAST_1`) serves what the first trial blocked: `AI_COMPLETE` on 20
models (Claude, GPT-5, Llama, Mistral), Cortex Agent invocation, Cortex Search,
masking / row-access / projection policies, DMFs and 14-day Time Travel. Still
blocked: External Access Integration, tag-based masking, `TOP_INSIGHTS`. The
earlier account's evidence (`capabilities.ASHWINRAVI004.yml`) is kept for the
record.

So the STR narrative can be written by a model -- and **no figure in it is
trusted because a model wrote it**: `REVIEW_STR`'s `narrative_trace` check
rejects any amount, date or count in the prose that is not the display of a
re-proven logged figure, whatever the agent's own reviewer said.

**Detection scope, stated plainly.** One rule is implemented and evaluated end
to end: `STRUCTURING_CROSS_ACCOUNT` (recall and precision 10/10 on dev). The
other typologies in `conf/detection.yml` are registered, with generation support
(shared devices, under-declared income, OpenSanctions lists), but have no
detection SQL yet.

## Quick start

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

cp .env.example .env              # pointers only; no secrets

riskcopilot --profile dev config -s topology   # resolved object names
riskcopilot --profile dev doctor               # preflight
riskcopilot --profile dev deploy --dry-run     # render SQL, no connection
```

### Datasets

The four source datasets total ~950 MB and are **not committed**. They are
declared in [`data/MANIFEST.yml`](data/MANIFEST.yml) with sizes and SHA-256
hashes; `riskcopilot doctor --checksums` verifies them.

| File | Source | Licence |
|---|---|---|
| `data/Kaggle/HI-Small_Trans.csv` | IBM AMLworld | CDLA-Sharing-1.0 |
| `data/Kaggle/HI-Small_accounts.csv` | IBM AMLworld | CDLA-Sharing-1.0 |
| `data/Kaggle/HI-Small_Patterns.txt` | IBM AMLworld | CDLA-Sharing-1.0 |
| `data/targets.simple.csv` | OpenSanctions | CC-BY-NC-4.0 |

## Configuration — three tiers, nothing hardcoded

| Tier | What | Where | Rule |
|---|---|---|---|
| 1 | Credentials | `~/.snowflake/connections.toml`, `cortex secret`, Snowflake `SECRET` | Never in the repo. `conf/` holds only secret *names*. |
| 2 | Topology (db, schema, warehouse, role, lag) | `conf/base.yml` + `conf/profiles/<env>.yml` | No `.sql` file may contain a literal object name; the deployer rejects it. |
| 3 | Business thresholds (₹10L CTR, 3× income, `COUNT>=3`) | `GOVERNANCE.POLICY_CLAUSE` at runtime, seeded from `conf/detection.yml` | Detection SQL joins to the table. A threshold in code is an ungoverned threshold. |

Two profiles coexist in one account: `dev` suffixes every object with `_DEV` and
relaxes target lags, `demo` uses canonical names and full freshness.

```
conf/
  base.yml            topology
  profiles/dev.yml    _DEV suffix, 60-min lags, 500-party sample
  profiles/demo.yml   canonical names, 1-min/5-min lags, 5000-party sample
  generation.yml      seeds, FX rates, injection cohorts, validation gates
  detection.yml       POLICY_CLAUSE seed with provenance per clause
  models.yml          ordered Cortex model candidates per agent mode
  external.yml        MCP/EAI endpoints and secret names
  ml.yml              Tier-1 ML + documented Future Scope
```

Every clause in `conf/detection.yml` must cite a real source document;
`doctor` fails the build otherwise. This is what keeps the demo from citing a
circular number that does not exist.

## Architecture gaps fixed during implementation

The design docs left 17 gaps that would have blocked or silently broken the
build. The load-bearing ones:

| Gap | Problem | Fix |
|---|---|---|
| **02** | L1 generated Faker parties with fresh ids while transactions referenced AMLworld hex accounts. Every join would return nothing — no income (kills `INCOME_ACTIVITY_MISMATCH`), no device (kills `SHARED_DEVICE_CLUSTER`), no region (kills RLS). | AMLworld keys are authoritative. `RAW.ID_MAP_PARTY` / `ID_MAP_ACCOUNT` derive surrogate ids deterministically from source keys; Faker only *dresses* preserved keys. Referential integrity holds by construction. |
| **03** | `RAW_TRANSACTIONS` defined twice — pair-level AMLworld-shaped and leg-level signed INR. | Three tables: `RAW_AMLWORLD_TRANS` (verbatim) → `RAW_TRANSACTIONS` (leg-level, **no label**) + `GROUND_TRUTH.TXN_LABELS`. |
| **04** | Random `declared_annual_income` makes the income rule fire on everyone or no one. | Derived from each party's observed legitimate inflow; the illicit cohort deliberately under-declares. |
| **05** | Random 8% `device_fingerprint` yields **zero** detections — the rule needs shared device **and** fund flow. | Shared fingerprints are co-located with existing flows; 30% are decoys, as false-positive control. |
| **06** | "×83 for INR" across 14 currencies, and FX conversion cannot create ₹9.4–9.8L structuring clusters. | Per-currency rate table + structuring as a separate labelled injection cohort. |
| **08** | Labels are txn-grain, signals are party-window-grain. "F1 > 0.7" had no denominator. | `GROUND_TRUTH.EVAL_GRAIN_PARTY_MONTH` declares party-month as the grain, with `expected_rule_ids` per typology. |
| **09/10** | Rolling-window velocity forces full refresh every 5 min; one non-incremental rule degrades the whole signals DT. | Velocity decomposed into incremental-safe daily aggregate + view; signals split into fast (row-level) and aggregate DTs. |
| **11** | A DT recomputes its full result set, so the stream + task inserts duplicate cases forever. | Deterministic `signal_business_key` (`rule|party|window[|account]`) with a unique constraint; the router MERGEs. Plus a sanctions watermark table. |
| **12** | Reviewer re-verification via Time Travel can't work: `AT(TIMESTAMP=>)` binds per table reference, retention defaults to 1 day, and DTs aren't freely time-travellable. Nor was SHA-256 canonicalisation ever defined. | `EVIDENCE_QUERY_LOG.result_snapshot` + versioned canonical hashing (`risk_copilot.hashing`); Time Travel becomes demonstrable provenance, not the correctness mechanism. |
| **13** | RLS covered 2 DTs; cases/findings were unprotected, and owner's-rights procedures bypass RLS entirely. | `CASE_RECORD.branch_region` denormalised for filtering; procedure rights are an explicit Phase 5 decision. |
| **17** | FIU-IND publishes a *form*, not an XML schema. | Validate against our own JSON-Schema in `REF_FILING_TEMPLATES`, with `schema_authority = INTERNAL_TRANSCRIPTION`. No FINGate conformance claim. |
| **18** | Demo hero party `P-10042 / Rajesh Kumar` was hardcoded but ids are generated — the demo script was fiction. | A selection step picks a party that provably trips every listed rule and pins it to `conf/generated/`. |

Found by tests during implementation: 12-digit surrogate ids are required —
7 digits produced 3 collisions in 5,000 entities and would produce ~24,500
across AMLworld's full population, silently merging distinct parties.

## Snowflake-native ML

**Tier 1** -- designed as built-in SQL; no `snowflake-ml-python`, no compute pool.
**Neither is built yet** (no detection SQL calls them); `doctor` confirms both
functions are available on the current account:

- **`JAROWINKLER_SIMILARITY` fuzzy sanctions screening.** The design *cut* fuzzy
  matching as too complex; it is one scalar function. Exact match raises a
  signal, the fuzzy band raises a review task — never an automatic filing.
- **`SNOWFLAKE.ML.ANOMALY_DETECTION` velocity baselines.** The honest answer to
  "rule-based TMS produce an 80–95% false-positive rate". Unsupervised, so it
  cannot leak labels.

**Future Scope** (documented in `conf/ml.yml` with rationale): Feature Store for
point-in-time-correct training sets, `SNOWFLAKE.ML.CLASSIFICATION` triage
ranking, `TOP_INSIGHTS`, Model Registry, `snowflake.ml.jobs` graph embeddings,
`AI_EMBED` entity resolution.

**Hard boundary:** ML output may rank, prioritise and flag for review. It may
never supply a number that appears in an artifact.
`RULE_DEFINITION.admissible_as_primary_evidence` enforces this in the schema.

## Demo runbook

The same app runs in two hosted places:

- **Streamlit Community Cloud**, a public URL that needs no Snowflake account. It
  connects as the key-pair service user `RC_PUBLIC_APP_USER_DEV`, whose role can read
  the demo and run the case tools and nothing else. To host it:
  1. Run `riskcopilot -p dev deploy --stage app_access` and
     `riskcopilot -p dev app public-access` (it signs in as the user and checks it).
  2. Push the branch.
  3. At share.streamlit.io, create an app from the repository. Set the main file to
     `app/streamlit_app.py` and Python to 3.11.
  4. Paste `~/.snowflake/keys/rc_public_app_user_dev.streamlit-secrets.toml` into
     Settings > Secrets. Never commit that file.
- **Streamlit in Snowflake**, in Snowsight under Projects > Streamlit >
  `RISK_COPILOT_APP_DEV`. It runs on the container runtime with the owner's rights.

Both open on a sign-in page that shows the two demo accounts. The **AML Analyst**
(`analyst` / `Analyst@2026`) investigates, drafts and reviews. The **Principal
Officer** (`officer` / `Officer@2026`) signs. Each persona sees only their own
duty's controls (maker-checker).

Every button is one stored-procedure call, and every call writes a row to
`AUDIT.AGENT_ACTION_LOG`.

**Before the demo**, reset every case. All three ways call the same procedure,
`EVIDENCE_CHAIN.RESET_CASES_DEV`, and each shows what it will delete first:

- **In the app:** Governance & audit > *Demo controls: reset every case*, then type `RESET`.
- **In a Snowsight worksheet:** `CALL RISK_COPILOT_DEV.EVIDENCE_CHAIN.RESET_CASES_DEV('SQL', TRUE);`
  previews the reset, and `CALL ...RESET_CASES_DEV('SQL', FALSE);` runs it.
- **From the CLI:** `riskcopilot -p dev app reset-cases`, which asks before it runs.

Run `riskcopilot -p dev app deploy` only if `app/` changed since the last deploy.

The reset deletes the workflow's rows (EVIDENCE, EVIDENCE_QUERY_LOG, FINDING,
REVIEW, ARTIFACT, ATTESTATION) and reopens the cases, in one transaction. Signals
and case links are untouched. The audit log is kept, and the reset adds its own
row, recording where it was run from (`via`). This breaks the evidence chain's
append-only rule on purpose, so use it only on a demo database.

Then open the deployed app once and check that it loads. Also click through one
sign-off on a case you will not demo, then reset again. That sign-off has not yet
been exercised from inside Streamlit in Snowflake; Ask has, and works there.

**Click path** (times measured on the dev trial account, X-Small warehouse)

| # | Page | Action | Shows | Time |
|---|---|---|---|---|
| 1 | Overview | -- | 5,032 transactions, 10 signals, 10 open cases, 0 STRs filed, recall / precision 10/10 | -- |
| 2 | Overview | **Investigate** (the hero is preselected) | Kamya Dhar, `CASE-4399c4c86db5c6ea`: 10 cash deposits, ₹94.03 lakh, 2 accounts, all under the ₹10 lakh line, against a declared income of ₹38.5 lakh | -- |
| 3 | Case | **1 · Gather evidence** | Status INVESTIGATING; 18 evidence pointers, 4 logged queries with hashes | ~4 s |
| 4 | Case | Turn on **Plant a defect**, then **2 · Draft STR** | Draft v1 claims ₹1,03,42,961 and cites `CTR_CASH_THRESHOLD_2026` | ~5 s |
| 5 | Case | **3 · Run Reviewer** | **REJECT**, with 2 defects: the stated total vs the ₹94,02,691 its query returns, and a clause that does not exist. 4 queries re-run, no model | ~4 s |
| 6 | Case | Turn the defect off, then **2 · Draft STR**, then **3 · Run Reviewer** | v2 states ₹94,02,691 and gets **APPROVE**: all 5 checks pass, including the narrative trace; v1 stays in the version history | ~9 s |
| 7 | Case | **Go to sign-off**, then **Switch to the Principal Officer** and sign in | The approved STR, its hash, the Reviewer's checklist; the analyst is told only the officer can sign | -- |
| 8 | Sign-off | Name, Attest, **Sign** without ticking the box | Refused: "Tick the confirmation to sign." | -- |
| 9 | Sign-off | Tick, **Sign** | ATTESTED: the STR is FILED, the attestation records its hash; "filed without attestation" stays 0 | ~2 s |
| 10 | Ask the data | "How many cases are there by status?" | Cortex Analyst's SQL and a chart: 1 CLOSED, 9 OPEN | ~5 s |
| 11 | Governance & audit | Action log tab | The run above, one row per call, from the reset onward | -- |

After step 9 the hero's buttons are disabled: a filed case cannot be redrafted.

**Live streaming, from the dashboard (no laptop, no CLI)**

Every transaction is already loaded; `RAW_TRANSACTIONS.held_back` decides which
ones have "arrived". Overview > **Live simulator** drives one procedure,
`EVIDENCE_CHAIN.SIM_CONTROL_DEV`, and every click refreshes detection and routes
new cases inside Snowflake:

| Button | What it does (measured on dev) |
|---|---|
| **Arm** | Clears the case queue, holds back everything booked from 03 Sep 2022: 3,764 legs visible, 6,300 held, 2 baseline cases |
| **Tick** | Releases the next 6 h of bookings (110, then 66 legs) |
| **Auto: 1 tick/min** | Resumes `SIM_TICK_TASK_DEV`; it stops itself when nothing is held |
| **Inject demo deposits** | Releases the hero's held legs at once: `CASE-4399c4c86db5c6ea` opens in the same call |
| **Release all** | Everything arrives: 10,064 legs, 10 cases, `verify` passes |

Live mode switches on by itself after Arm, Tick, Auto or Inject. Release only
flips the arrival columns, which the equivalence fingerprint leaves out, so the
data is identical to a full load. End a demo with **Release all**, then reset
the cases.

The older CLI path (`riskcopilot load --replace` + `replay`) still works.

**The AI-drafted STR** (same account, `RISK_COPILOT_AGENT.STR.RC_STR_AGENT`):
on the Case page, **4 · AI STR + PDF** calls `GENERATE_AI_STR`, which runs the
whole chain inside Snowflake, so it works on the hosted links too. It builds the
hashed evidence package, retrieves the matching regulation text with Cortex
Search, and runs the Cortex Agent, which drafts with claude-sonnet-4-6 and
cross-reviews with openai-gpt-5.1 / llama3.3-70b. It then writes the draft back
as a new FINDING version, runs the data account's own `REVIEW_STR` on it, and
returns the FIU-IND PDF. A run takes about a minute and is capped at one per
case per 5 minutes and 30 a day.
From the laptop, `python -m risk_copilot.agentic.str_pipeline CASE-4399c4c86db5c6ea --out str.pdf`
runs the earlier bridge version of the same chain. The Slack bot uses it
(`str CASE-...`); it needs the Slack tokens in `bridge/.env`.

## Development

```bash
pytest -q                                  # unit tests, no Snowflake needed
ruff check src tests && ruff format src
mypy src
python -m risk_copilot.pipeline.lint       # render all SQL, both profiles
pre-commit install
```

### Repository layout

```
conf/            layered configuration (see above)
src/risk_copilot/
  config.py      layered loader, fail-fast validation, sql_context contract
  session.py     connections.toml resolution, key-pair auth, error explanation
  ids.py         deterministic identity derivation (GAP-02)
  hashing.py     canonical result hashing (GAP-12)
  doctor.py      preflight diagnostics
  cli.py         single entry point
  pipeline/      idempotent ordered SQL deployer + offline lint
sql/             Jinja SQL templates -- see sql/README.md
tests/unit/      offline tests
data/            MANIFEST.yml; payloads gitignored
docs/            architecture docs and CoCo lifecycle evidence
```