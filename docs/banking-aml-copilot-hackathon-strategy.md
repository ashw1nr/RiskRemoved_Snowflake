# Banking/NBFC Risk & Regulatory Copilot — Research & Design Strategy

> **Design document (pre-build research).** It describes the full target vision,
> including later ribs and integrations. What is built and running is recorded in
> the [README](../README.md) (Status, Demo runbook, Roadmap).
*Snowflake Cortex Code (CoCo) Hackathon — Problem Statement: Fraud, Liquidity, Credit Risk & Regulatory Reporting Copilot*

---

## Part 0 — What this problem statement is actually asking

The brief does **not** ask for a better fraud model. It asks for a **copilot** that goes "from signal to evidence to a documented finding or report." The words doing the work are *governed*, *explainable*, *evidence backed*, and *audit ready*. Those are not ML words — they are provenance and workflow words.

Most teams on a fraud PS will build: synthetic transactions → anomaly score → Streamlit dashboard → chat box over it. That is a demo of Cortex Analyst, not a solution to this PS. It scores mid on Technical Execution and low on Solution Completeness, because it stops at "signal" and never reaches "documented finding."

The three judging criteria map cleanly:

| Criterion | What it actually tests | Where teams lose it |
|---|---|---|
| Real World Relevance | Do you know what an AML analyst's day, an ALCO pack, and an STR filing actually look like? | Generic "suspicious transaction" framing with no regulatory specificity |
| Technical Execution | Depth of the Snowflake stack used correctly, and CoCo evidence across all four lifecycle phases | Everything built by hand, CoCo used as autocomplete, no tests |
| Solution Completeness | Does the flow terminate in an artifact a human can sign and a regulator can inspect? | Ends at a dashboard or a chat answer |

**Thesis: the deliverable is not an answer, it is a defensible file.**

---

## Part 1 — Scope decision

The brief lists fraud, liquidity risk, credit risk, and regulatory reporting. Four verticals cannot be done well in a hackathon; one alone under-serves the "cover the flow" requirement.

**Recommended shape: one spine, two ribs.**

- **Spine (build deep): AML / transaction monitoring → STR.** Richest evidence chain, best public synthetic data, a real published filing format, genuinely explainable typologies (structuring, layering, fan-in/fan-out, mule networks).
- **Rib 1 (complete vertical slice): Liquidity — ALM Structural Liquidity Statement.** Cheap to build (deterministic SQL over a cashflow table); proves the architecture generalises from narrative finding to numeric regulatory return.
- **Rib 2 (stub, one working question): Credit — ECL / early-warning.** ~10% of the effort; shows the ontology extends.

This proves the evidence/governance layer is domain-independent (the real product idea) while paying full engineering cost only once.

### Why NBFC/India is a strong anchor (verified facts)

- Under the PML Rules, entities file **Cash Transaction Reports (CTR)** for cash transactions above **₹10 lakh** in a month, and **Suspicious Transaction Reports (STR)** where transactions lack economic rationale or are inconsistent with the client's profile, submitted to FIU-IND via the FINGate gateway.
- CTR is due by the **15th of the succeeding month**; STR is due within **7 working days** of forming the suspicion.
- FIU-IND's reporting format guide specifies an Account Based Reporting Format, a Transaction Based Reporting Format, and a CCR format, all as a single XML schema.
- RBI has moved NBFC regulatory/supervisory returns onto **CIMS**, replacing the older XBRL system.
- For liquidity: the Statement of Structural Liquidity's 1–30 day bucket is split into **1–7, 8–14, and 15–30 day** buckets for NBFCs, with net cumulative negative mismatches capped at **10%, 10%, and 20%** of cumulative cash outflows respectively. Applies to non-deposit-taking NBFCs with asset size ≥ ₹100 crore, systemically important CICs, and all deposit-taking NBFCs, effective December 1, 2020.
  - *Note:* this NBFC number set (10/10/20) is distinct from the bank ALM framework's finer-bucket limits (5%/10%/15%/20% for Next-day/2–7/8–14/15–28 day buckets). Don't conflate them — use the NBFC framework for this build.

These thresholds (₹10 lakh, 7 working days, 10/10/20%) are the specificity that makes a judge sit up — and they're exactly what your synthetic data must be built to straddle.

---

## Part 2 — Conceptual architecture: Signal → Evidence → Finding → Filing

Four layers, four object types, one rule. Put this diagram on slide 2.

1. **Signal** — a deterministic, versioned detection rule or model output. `SIGNAL` (rule_id, rule_version, entity_id, window, score, triggering_rows).
2. **Evidence** — the immutable, re-computable set of supporting facts: transaction rows, screening hits, KYC profile as-of, policy clause invoked, prior alert history. `EVIDENCE_ITEM` (source pointer: table+PK+as-of timestamp, or document+page+span).
3. **Finding** — the human-readable, LLM-composed narrative connecting evidence to a conclusion and recommended disposition. `FINDING` (narrative, disposition, confidence, evidence_item_ids[], author_model, prompt_version, reviewer).
4. **Filing** — a schema-valid regulatory artifact generated from the finding plus deterministic aggregates. `REGULATORY_ARTIFACT` (format, schema_version, validation_status, hash, generated_at).

**Rule 1 (say it out loud in the pitch): no number in a filing is ever produced by a language model.** Every figure comes from SQL in the semantic layer; the model only selects, sequences, and narrates. This converts "AI wrote my regulatory report" from liability to feature, and is easy to demonstrate — show the query hash next to the number.

**Rule 2: every claim carries a pointer.** A finding paragraph claiming "14 inbound transfers totalling ₹48.7 lakh over 9 days from 11 distinct accounts opened within 60 days" must be clickable down to the 14 rows and the account-opening dates.

This framing wins because it mirrors how a real Financial Crime Compliance unit works (alert → case → narrative → SAR/STR) and gives a non-generic data model beyond "transactions table plus vector index."

---

## Part 3 — Ontology and data model design

The semantic view determines whether the copilot answers correctly. Cortex Analyst reads descriptions, synonyms, join paths, and verified examples from it — this metadata is what reduces hallucination.

### Core entity layer
- `PARTY` (customer, counterparty, beneficial owner): type, risk rating, PEP flag, onboarding date
- `ACCOUNT`: product, open date, status, branch/channel
- `RELATIONSHIP` (party↔account, party↔party for UBO/guarantor/director) — enables network typologies
- `TRANSACTION` (grain: one leg, signed, with channel, instrument, counterparty account, country, MCC)
- `POSITION` / `CASHFLOW` (liquidity: contractual and behavioural maturity buckets)
- `LOAN` / `REPAYMENT_SCHEDULE` / `DPD_HISTORY` (credit)
- `EXTERNAL_HIT` (sanctions/PEP/adverse-media match with score and list version)

### Governance layer (the differentiator)
- `RULE_DEFINITION` (versioned, linked to the policy clause it implements)
- `SIGNAL`, `EVIDENCE_ITEM`, `FINDING`, `REGULATORY_ARTIFACT`
- `AGENT_ACTION_LOG` (who/what asked, tools fired, SQL run, model version, tokens, latency, outcome)
- `REVIEW` (maker-checker: analyst decision, timestamp, override reason)

### Three ontology decisions worth defending on stage

1. **Grain of `TRANSACTION`: leg vs. pair.** Leg-level (one row per debit/credit) makes account-centric aggregation and CTR computation exact; pair-level (sender→receiver) makes graph traversal natural. **Recommendation:** store leg-level as physical fact, expose a pair-level view for network queries.
2. **Point-in-time correctness.** An STR narrative must reflect what was known *when the alert fired*, not today — so KYC attributes and risk ratings need effective-dated (SCD2) treatment, and evidence pointers need an as-of timestamp. Most teams skip this; an auditor never would.
3. **Typologies as first-class objects.** A `TYPOLOGY` table (structuring, smurfing, rapid pass-through, round-tripping, mule fan-in, dormant-account reactivation) linked to the FATF/RBI text describing them lets the copilot explain *why* in regulator language, not model language.

---

## Part 4 — Data synthesis: what to generate, with what, from what

### Central principle: three layers, three different technologies

**Do not ask an LLM to generate the transaction ledger.** LLMs produce tabular data that is locally plausible and globally wrong: no stable balances, no conservation of money, no realistic degree distribution, no reproducible label — and it's expensive at scale.

| Layer | What it is | Generate with | Why |
|---|---|---|---|
| **L1 — Reference/entity skeleton** | Parties, accounts, UBO graph, geography, products | Deterministic Python generator (Faker + custom graph builder), seeded | Needs referential integrity and reproducibility |
| **L2 — Behavioural ledger** | Transactions, balances, positions, repayment history | Agent-based simulator with explicit typology injection | Needs money conservation, temporal structure, complete ground-truth labels |
| **L3 — Narrative/documents** | KYC notes, policy manuals, case notes, complaints, past STR narratives, board minutes, credit memos | Cortex AISQL / `AI_COMPLETE` with structured prompts | Genuinely unstructured — LLMs are good at this |

CoCo writes all three, giving clean "Planning + Development" evidence, and the judging guideline explicitly names synthetic data generation as a recommended CoCo task.

### L2 in detail: seed from published, labelled research datasets

Don't invent laundering patterns from scratch — calibrate against public benchmarks.

**Primary recommendation — IBM AMLworld.** A synthetic financial transaction dataset generator/dataset calibrated against real transactions, with complete ground-truth labels (better than real data, since many real laundering transactions are never detected). Ships as six datasets (HI/LI × small/medium/large); **HI-Small** has 515K accounts and 5M transactions with 5.1K laundering transactions over a 10-day window. Authors recommend a chronological 60/20/20 split. Generated from a multi-agent virtual world — no real individuals. Released under CDLA-Sharing-1.0 (credit it).

Use **HI-Small**: fits comfortably in a trial warehouse, higher illicit ratio makes demo scenes easier to find.

**Secondary — SAML-D:** 9.5M transactions annotated across 11 normal and 17 suspicious typologies, suspicious ~0.10% of total. Directly reusable as your `TYPOLOGY` table taxonomy.

**Other datasets and their use:**

| Dataset | Use it for | Caveat |
|---|---|---|
| IBM AMLworld (Kaggle) | AML spine, labelled networks | Pair-level grain; CDLA-Sharing license |
| SAML-D | Typology taxonomy, rarity calibration | Single-institution flavour |
| PaySim / AMLSim | Re-parameterisable agent-based generator | Mobile-money flavour, thin entity attributes |
| IEEE-CIS Fraud Detection | Card-fraud feature realism | Anonymised features, no narrative |
| Sparkov / credit-card generator | Card-present fraud, merchant/MCC realism | Synthetic, US-shaped |
| PKDD'99 Berka (Czech bank) | Realistic retail banking account/loan structure | Small, 1990s |
| LendingClub | Credit risk / ECL, real repayment outcomes | US consumer loans, no transactions |
| Elliptic | Graph-based illicit flow labels | Crypto-only |

**Critical move: re-skin, re-scale, re-localise.** Map IBM's structural realism onto an Indian NBFC: rupee amounts with realistic denomination clustering; UPI/IMPS/NEFT/RTGS/cash channels; PAN/Aadhaar-style identifiers (fake checksums); district-level geography; gold-loan and two-wheeler-loan products. Inject typologies calibrated to *your* regulatory thresholds: structuring deposits at ₹9.4–9.8 lakh/month against the ₹10 lakh CTR trigger; cash just under PAN-quoting thresholds; layering through newly onboarded mule accounts.

### Which models for which generation job

**L3 narrative generation** via Cortex AISQL, which runs unstructured analytics with models from OpenAI, Anthropic, Meta, Mistral and DeepSeek, hosted inside Snowflake. Tier it:

- **Bulk volume** (10,000 KYC notes, 2,000 case comments): small/mid model, high temperature, structured attribute seeds so text correlates with the row.
- **Golden documents** (internal AML policy, credit policy, exemplar STR narratives, board risk-appetite statement): frontier model, hand-reviewed — these are what you'll cite on stage.
- **Never** use an LLM to decide whether a generated case *is* suspicious — the label comes from the L2 injection step deterministically, or your evaluation is circular.

**Code generation:** CoCo itself, choosing between Claude Opus/Sonnet and OpenAI GPT variants by task (strong model for generator design/semantic view, cheaper for boilerplate). Deliberate model routing by task is a small but real ingenuity point.

**Subtlety worth raising:** inject correlated noise into the narrative layer — typos, contradictions with the structured record, missing fields, stale occupations. If the unstructured corpus agrees perfectly with the tables, the copilot never has to reconcile conflicting evidence, which is the most impressive thing it can do.

### Realism tests to run and show (put this on a slide)

1. **Benford's law** on leading digits — run separately on legitimate vs. injected-illicit populations. Structured amounts break Benford; doubles as a real detection technique and a data-quality check.
2. **Degree distribution** of the counterparty graph should be heavy-tailed, not Poisson.
3. **Temporal seasonality**: intraday peaks, weekday/weekend split, salary-day/month-end spikes, festival clustering.
4. **Amount roundness ratio**: high share ending in 000, but not absurd.
5. **Class imbalance sanity**: keep illicit rate in the 0.1–1% band (published benchmarks put suspicious activity around 0.1% of transactions). A 10% fraud rate reads as naive.
6. **Money conservation**: debits = credits per closed system; balances never impossibly negative.
7. **Leakage check**: confirm no label column, typology ID, or "is_suspicious" hint reaches the semantic view the agent queries. State this explicitly on stage.

---

## Part 5 — The policy and filing corpus (use real documents here)

Structured data should be synthetic; **the rulebook should be real** — this is what makes citations verifiable and "explainable" non-circular. A judge can click through to an actual RBI circular; they cannot verify a hallucinated one.

| Source | What it gives you |
|---|---|
| RBI Master Direction on KYC (2016, as amended) | CDD/EDD obligations, PEP handling, record retention |
| PMLA 2002 and PML Rules (Rule 3, Rule 7) | Legal basis for CTR/STR/NTR and the Principal Officer role |
| FIU-IND reporting format guide / FINGate 2.0 documentation | XML schema and field semantics your filing must satisfy |
| RBI Liquidity Risk Management Framework circular (Nov 2019) | ALM buckets, LCR, contingency funding plan language |
| RBI CIMS return documentation, DNBS/NBS return formats | Structure of periodic returns |
| FATF 40 Recommendations + FATF typology reports | Internationally recognised typology vocabulary |
| BIS/Basel Framework | LCR/NSFR definitions for Basel framing alongside RBI |
| RBI Master Direction on NBFC Scale Based Regulation | Layer definitions, applicability logic |

Ingest with `AI_PARSE_DOCUMENT` (layout mode → Markdown, preserving text/tables/structure), then `AI_EXTRACT` for specific structured fields, then index with Cortex Search — a hybrid of keyword and semantic retrieval, which matters because pure vector search can miss exact matches like a legal term or identifier. Regulatory work is full of exact-match tokens ("Rule 3(1)(D)", "DNBS13", "₹10 lakh") — call this out as a deliberate choice.

**Chunking decision to defend:** chunk regulatory text by *clause*, not fixed token window, carrying (document, section, clause, effective_date, superseded_by) as metadata — so a citation reads "Master Direction on KYC, para 38, as amended" rather than "chunk 412." Effective-dating also lets you demo the copilot refusing to cite a superseded circular — a strong ten-second demo moment.

**Synthesise only the internal layer:** the fictional NBFC's own AML policy, transaction-monitoring rule book, risk appetite statement, escalation matrix — documents that don't exist publicly, where the "our policy says X, the regulation requires Y, here's the gap" reasoning lives.

---

## Part 6 — Real-time: what's genuinely live vs. honestly simulated

### Genuinely live, free, legitimate

| Feed | Cadence | Use |
|---|---|---|
| **OpenSanctions** consolidated sanctions + PEP | Sanctions lists refresh every 4–12 hours; other sources daily/weekly; aggregated collections ~every 6 hours; stable `latest/` URLs, no API key for bulk download | Counterparty screening, list-version provenance |
| OFAC SDN / UN Consolidated / EU Consolidated / UK FCDO | Daily-ish, official | Cross-list corroboration |
| GLEIF LEI golden copy | Daily | Legal-entity resolution, parent/UBO chains |
| RBI reference rates / ECB daily FX | Daily | Cross-border valuation, threshold conversion |
| RBI press releases / circulars RSS | Event-driven | "Regulation changed — which controls are affected?" |
| GDELT or a news API | Continuous | Adverse media screening |

**Licensing honesty:** OpenSanctions is free for non-commercial use; businesses need a data licence. Note "hackathon use, non-commercial; production would require licence X" — reads as maturity.

High-leverage feature: `CORTEX_SEARCH_BATCH` runs millions of fuzzy-match queries against a Cortex Search service in a single SQL statement, no infrastructure to manage — batch-screening the whole party book against the day's refreshed sanctions list in one statement is a strong, specific technical beat.

### Honestly simulated: the transaction stream

No one hands you a live bank feed. Correct framing: a **replay harness**, not fake real-time. Stream the generated ledger through a producer at a compressed clock (1 simulated day per 30 seconds), with a "chaos" mode to inject a structuring burst live on stage.

Ingest via **Snowpipe Streaming** — loads rows as they arrive with no staging files, up to 10 GB/s per table, ingest-to-query latency as low as 5 seconds, exactly-once delivery via offset tokens, ordered ingestion per channel, in-flight transformations via COPY syntax inside the PIPE object. For a sub-second story, there's a published Kafka + Snowpipe Streaming v2 + Interactive Tables pattern (region-limited — check your account).

Transform with **Dynamic Tables** — declaratively defined in SQL/Python, Snowflake manages dependencies and materialises against a freshness target, operating only on changed data. A batch pipeline becomes streaming by changing one latency parameter — show a 1-hour target lag for regulatory aggregates and 1-minute for fraud signals in the same DAG, and explain the cost/latency tradeoff.

**Tradeoff to state explicitly:** very low target lag costs credits continuously. Real-time fraud interdiction at payment authorisation is a sub-100ms problem belonging in the payment rail, not the warehouse. The warehouse does **near-real-time detection, investigation, case building** well. Saying this correctly shows architectural understanding rather than pretending Snowflake is a rules engine at the switch.

---

## Part 7 — The agent design

### Why Cortex Agents, and how to configure

**Cortex Agents** is a managed agentic platform where an agent plans, calls tools, and generates a response without you building an orchestration loop — bringing structured and unstructured data into one governed workflow: SQL over semantic views via Cortex Analyst, retrieval via Cortex Search, a code-execution (Python sandbox) tool, a data-to-chart tool, custom tools from stored procedures/UDFs, packaged agent skills, and MCP connectors. Snowflake recommends transitioning from direct Cortex Analyst use to Cortex Agents (semantic views and verified queries carry over unchanged). **Build on Agents, not raw Analyst** — the recommended path and a richer tool surface.

### Multi-agent decomposition (maps 1:1 to a judging bonus)

Four roles, explicit handoffs, shared case context:

1. **Sentinel** — runs on schedule, scans signals, triages by risk score/policy tier, opens cases. Mostly deterministic; LLM only for prioritisation narrative.
2. **Investigator** — gathers evidence for a case: transaction set, counterparty graph traversal, sanctions/PEP screening, KYC history/prior alerts, governing policy clauses. Heavy tool use, no conclusions.
3. **Drafter** — composes the finding narrative and generates the filing artifact, strictly from the evidence bundle.
4. **Reviewer (critic)** — adversarial pass: checks every numeric claim against a recomputation, every citation resolves, no unsupported assertions, required filing fields populated. Approves or returns with specific defects.

**The Reviewer is the highest-ROI item here.** Cheap (one more agent + checklist prompt + deterministic validators), directly produces the "guardrails and graceful fallback" bonus, and lets you *deliberately break something* on stage for a memorable moment.

### Where the LLM boundary sits (draw this literally on a slide)

| Deterministic (SQL / Python / schema validators) | LLM |
|---|---|
| All amounts, counts, ratios, bucket aggregations | Question understanding and intent routing |
| Threshold comparisons and rule firing | Selecting which evidence is material |
| Sanctions name matching scores | Narrative composition |
| XML/XBRL schema validation | Explaining a rule in plain language |
| Cumulative mismatch percentages | Suggesting next investigative steps |

### Verified queries = accuracy insurance

For the ~30 questions you know will come up, author them as verified queries in the semantic view. Verified examples lift natural-language accuracy; start with the 20% of data that answers 80% of questions and validate with subject-matter experts. Narrow, deeply-curated semantic layer beats wide and shallow.

### Custom tools = actions, not text

Register stored procedures/UDFs as agent tools so the copilot *does* things: `open_case()`, `escalate_to_principal_officer()`, `generate_str_xml()`, `validate_filing()`, `freeze_account_recommendation()`. Explicitly named in the PS bonus list.

### MCP: pick connectors that close the loop

- **Jira / Linear** — case becomes a real ticket with the evidence pack attached (genuinely what FCC teams do)
- **Slack** — Principal Officer gets the finding for maker-checker approval, approves in-channel
- **Google Drive / SharePoint** — filing artifact lands where compliance actually keeps it

Agents support MCP connectors to remote servers (Atlassian Jira, Salesforce, or your own apps); CoCo automations can attach Snowflake-managed or customer-provided MCP servers via a repeatable `--mcp` option. Two well-chosen connectors beat five decorative ones.

**Bonus:** expose your own agent as an MCP server so external AI clients can call it — "our risk copilot is itself a tool other agents can call" is a strong closing line, low extra effort.

---

## Part 8 — Governance, explainability, and the guardrail story

- **Access control the AI cannot route around.** Row access policies on party/transaction data (branch-level visibility), masking policies on PAN/Aadhaar/account numbers. Demo the same question from two roles returning different answers. Agent data access is controlled by existing Snowflake roles/privileges and each tool's execution context.
- **Immutable audit trail.** Every agent turn logged: user, role, question, tools invoked, SQL text and hash, rows touched, model/prompt version, evidence IDs, latency, cost. Append-only.
- **Reproducibility.** Given a finding ID, re-run the exact evidence queries at the recorded as-of timestamp and prove the numbers reproduce. Demonstrate this live.
- **Refusal and confidence.** Define behaviour when the copilot can't ground an answer: no data in scope, ambiguous entity resolution, conflicting evidence, out-of-scope question. Show one deliberate refusal in the demo.
- **Maker-checker.** No filing artifact auto-submits. `DRAFT → REVIEWED → APPROVED → FILED`, human approval captured. The Principal Officer bears legal filing responsibility under the PML Rules — automatic submission would be legally wrong, not just cautious.
- **Data-quality gates.** Data Metric Functions on pipelines (null rates, freshness, referential integrity, volume anomalies); the agent surfaces "this answer is based on data that is 14 hours stale" rather than silently answering.

---

## Part 9 — Standout features, ranked by judge-ROI

1. **Evidence ledger with clickable provenance.** The whole thesis in one artifact.
2. **Reviewer agent catching a planted defect live.** Cheapest memorable moment.
3. **Deterministic metric layer + "no LLM computes a number" rule.** One-line policy reframing the entire risk objection.
4. **Published, documented, reusable CoCo skill pack.** Guidelines call this *the headline bonus*. Ship a real plugin repo: `synthetic-financial-data`, `regulatory-corpus-ingest`, `evidence-pack-builder`, `filing-validator`. Put the GitHub URL on the final slide.
5. **Scheduled automations doing unattended work.** Each stored as a Snowflake AGENT TASK, runs the saved prompt as the creating user in a managed sandbox with `/workspace` as working directory. Schedule three: overnight alert triage, monthly CTR pre-assembly, weekly control-coverage check against new RBI circulars.
6. **Evaluation harness with ground truth.** Directly satisfies the "Testing and validation" judging phase.
7. **Multi-agent orchestration with explicit handoffs.** Comes free from Part 7 if designed that way.
8. **Cross-surface demo.** Show CLI for pipeline work, Desktop for the app build, Snowsight/CoWork for the business user. Thirty seconds, big perceived coverage.
9. **Graceful degradation.** Kill the sanctions feed mid-demo; agent flags degraded screening confidence and continues with a caveat.
10. **Regulatory-change monitoring.** New circular lands → agent diffs against control inventory → opens a Jira ticket. Do only if 1–8 are solid.

---

## Part 10 — Testing and validation

Build four test suites; put the results table on a slide.

1. **Detection quality.** Report precision, recall, alert volume per typology, false-positive rate at your operating threshold (from known injected-typology ground truth). Frame against real TM systems' high false-positive rates.
2. **Text-to-SQL correctness.** 40–60 golden questions with hand-verified expected result sets, measured on result match (not SQL string). Break out by question type. Track failures fixed by adding a verified query.
3. **Groundedness and citation integrity.** AI Observability datasets hold ground-truth inputs/outputs; runs compute LLM-as-a-judge metrics (0–1, with explanation) including context relevance, groundedness, and correctness. Cortex Agent evaluations have a Snowsight Evaluations tab, GPA metrics, and `EXECUTE_AI_EVALUATION`. Add a deterministic check: every citation ID in a finding must resolve to an existing evidence row — target and report 100%.
4. **Adversarial / edge cases.** Prompt injection planted in a synthetic KYC note; entity resolution traps (same name, one sanctioned); near-threshold arithmetic (₹9,99,999 vs ₹10,00,001); ambiguous questions needing clarification; out-of-scope questions needing refusal; stale-data freshness-gate test.

Report all four as one table — worth more than an extra feature.

---

## Part 11 — Tradeoff register

| Decision | Option A | Option B | Recommend | Why |
|---|---|---|---|---|
| Data source | Pure generated | Reshaped public benchmark | B | Calibrated realism + complete labels + citable provenance |
| Ledger generation | LLM | Agent-based simulator | Simulator | Money conservation, reproducibility, cost |
| Detection | ML model (GNN/GBT) | Explainable rules + graph features | Rules-first, ML secondary | Explainability is the PS requirement |
| Scope | Four domains shallow | One deep + two slices | One deep + two slices | Completeness is judged on the chain, not breadth |
| Semantic layer | Wide auto-generated | Narrow hand-curated + verified queries | Narrow | Accuracy on demo questions is observed directly |
| Latency | Everything sub-minute | Tiered lag by use case | Tiered | Cost honesty; correct architecture |
| Filing | Auto-submit | Draft + maker-checker | Draft | Legally correct, stronger story |
| Real-time framing | Claim live feeds | Live reference data + replayed ledger | Hybrid, stated plainly | Overclaiming gets caught in Q&A |
| Interface | Streamlit only | Streamlit + CoWork/Snowsight + Slack | Multi-surface | Matches "working across surfaces" bonus |
| RAG chunking | Fixed token window | Clause-level with effective dates | Clause-level | Quotable, version-aware citations |

---

## Part 12 — Presentation strategy

### Narrative spine

Use a persona: **Priya, an AML analyst at a mid-size NBFC**, with a Monday morning alert queue and a seven-working-day STR clock. Everything demoed is one of her steps.

### Six-minute demo script

| Time | Beat | What it proves |
|---|---|---|
| 0:00–0:30 | The problem in numbers: alert volume, manual hours, filing deadline | Real World Relevance |
| 0:30–1:00 | Architecture slide: Signal → Evidence → Finding → Filing, LLM boundary line drawn | The idea |
| 1:00–2:00 | Live stream running; trigger the structuring burst; alert appears in seconds | Real-time execution |
| 2:00–3:15 | Priya asks in plain English; agent pulls transactions, traverses the graph, screens sanctions, cites the KYC policy clause; numbers expand to source rows | Governed, explainable, evidence-backed |
| 3:15–4:00 | Draft STR generated, schema-validated; **planted defect**; Reviewer rejects with a specific reason; regenerate; passes | Guardrails — the money shot |
| 4:00–4:30 | Switch role to a different branch officer; same question, different governed answer. Ask an out-of-scope question; clean refusal | Governance and honesty |
| 4:30–5:00 | Pivot to liquidity: 1–7 day cumulative mismatch against the 10% limit | Generalisation, completeness |
| 5:00–5:30 | Automations tab: overnight triage ran at 02:00, filed three cases to Jira | Unattended operation |
| 5:30–6:00 | Evaluation results table; skill pack repo URL; one line on what production would need | Technical rigour, maturity |

### Proving CoCo across all four phases

Make evidence *visible as artifacts*, not claims:

- **Planning:** commit CoCo planning transcripts, data-exploration outputs, ontology draft, design doc. Use Plan Mode for schema design and keep the plan artifact.
- **Development:** git history showing CoCo-authored pipelines, semantic views, agent config, Streamlit app. CLI can read/write local repos, invoke bash, run git, execute SQL directly.
- **Execution:** automations list with real run history and inspectable Cortex threads. Open one thread on stage.
- **Testing:** evaluation runs in Snowsight, golden-question results, adversarial suite.

One slide, "CoCo across the lifecycle," four screenshots, four captions — does more for Technical Execution than ten minutes of talking.

### Slide set (~10)

1. The problem, in a compliance officer's numbers
2. Architecture: four-layer chain with LLM boundary
3. Ontology and evidence model
4. Data strategy: synthesis method, seed sources, realism tests, leakage statement
5. Real-time: what's live vs. replayed, latency tiers
6. Agents and tools, MCP loop closing into Jira/Slack
7. Governance: RLS, audit log, maker-checker, reproducibility
8. Validation results table (all four suites)
9. CoCo across the lifecycle, four screenshots
10. What's reusable (skill pack + repo) and what production would need

### Language discipline

Use domain vocabulary: alert, case, disposition, escalation, Principal Officer, EDD, typology, HQLA, cumulative negative mismatch, four-eyes. Avoid "AI-powered," "revolutionary," "seamless."

---

## Part 13 — Failure modes to avoid

- Ending at a dashboard instead of a documented finding/report
- A 10% synthetic fraud rate (signals no domain contact with reality)
- Label leakage into the queryable semantic layer
- Hallucinated regulations or fabricated circular numbers
- Auto-filing without human sign-off
- CoCo used as pure autocomplete with no planning/automation evidence
- Over-broad scope (four half-built domains vs. one complete chain)
- A demo dependent on live internet (cache sanctions snapshots locally with version recorded)
- No refusal behaviour anywhere in the system

---

## Part 14 — Reading order

**Snowflake mechanics:**
1. CoCo overview → CLI → Desktop → bundled skills → automations
2. Snowflake's end-to-end guide (dbt models, Data Metric Functions, custom CoCo skills, Cortex Agent over structured/unstructured data via CoWork, ground-truth evaluation, row-level security, exposing the agent as an MCP server)
3. Cortex Agents (tools, custom tools, MCP connectors) and the Analyst→Agents transition note
4. Semantic views and verified queries
5. Cortex AISQL function reference (`AI_PARSE_DOCUMENT`, `AI_EXTRACT`, `AI_CLASSIFY`, `AI_FILTER`, `AI_AGG`) and `CORTEX_SEARCH_BATCH`
6. Snowpipe Streaming architecture and Dynamic Tables
7. AI Observability and Cortex Agent evaluations

**Domain:**
1. The AMLworld paper (arXiv 2306.16424) — read the generator design section as your L2 blueprint
2. FATF 40 Recommendations + one FATF typologies report
3. RBI Master Direction on KYC; PML Rules Rule 3 and Rule 7; FIU-IND reporting format guide
4. RBI Liquidity Risk Management Framework circular (Nov 2019)
5. Network-analytics-for-AML survey (arXiv 2405.19383) for graph feature ideas implementable as explainable SQL rather than a GNN

---

## Appendix — Domain reality check: analyst day, ALCO pack, STR filing

*What follows distinguishes verified regulatory/structural facts from reconstructed practitioner texture — an honest accounting of confidence levels.*

### An AML analyst's day

Queue-driven, not investigation-driven, for most of the day.

- **Morning: the queue.** Case management tool (Actimize, SAS AML, or in-house) opens to alerts generated overnight, ranked by priority tier. Convention: P1 (high risk) triaged within 24 hours, P2 within 5–10 business days, P3 within 30 days.
- **Triage dominates volume.** Is this a duplicate, obviously explainable, or does it need real work? A defining industry fact: **80–95% of alerts are false positives** — most of the day is defensibly closing alerts that were never real.
- **Real investigations are manual.** Swivel-chair across transaction monitoring, core banking, a separate screening tool, KYC files, and related alerts — manually stitched into a narrative.
- **Disposition:** close with documented rationale, escalate, or open a case heading toward a filing. Regulators inspect whether the reasoning and evidence are documented well enough to reconstruct the decision later — not just whether the decision was correct. Thin documentation on a correct decision still draws findings.
- Constant deadline pressure from statutory filing clocks running parallel to the triage queue, plus quality-review sampling.

### An STR filing (India / FIU-IND)

- **Trigger:** a transaction or pattern that's unusual, lacks economic rationale, or is inconsistent with the customer's known profile — the Principal Officer forms a "suspicion."
- **Clock:** filed within **7 working days** of forming that suspicion (vs. CTR, a monthly aggregate for cash activity over ₹10 lakh, due by the 15th of the next month).
- **Who signs it:** every reporting entity designates a **Principal Officer** under Rule 7 of the PML Rules, who bears filing responsibility — not the analyst who found it. This is why the design uses maker-checker, not auto-submission.
- **Format:** filed electronically through FINGate 2.0 as structured XML conforming to FIU-IND's reporting format guide (Transaction Based or Account Based Reporting Format) — not free text. The filing is a narrative plus a structured schema that must validate before acceptance.
- **Narrative contents:** factual chronology (dates, amounts, counterparties, channels), the specific inconsistency with stated occupation/business/income, prior alerts on the same customer, and reasoning written to stand alone — FIU-IND analysts read it with zero other context.

### An ALCO pack

ALCO (Asset-Liability Committee) is headed by the CEO/CMD or an ED, with the Board's management committee overseeing it. The pack is a set of standardised statements, not a discretionary report:

- **Statement of Structural Liquidity** — the maturity-bucketed gap statement. Original RBI guidelines: negative gap not exceeding **20%** of cash outflows in 1–14 day and 15–28 day buckets; later refined to finer buckets: **5%, 10%, 15%, 20%** for Next-day/2–7/8–14/15–28 day buckets (bank ALM framework). The NBFC-specific 2019 framework uses **10%/10%/20%** for 1–7/8–14/15–30 days — a related but distinct number set; use the NBFC numbers for this build.
- **Statement of Short-Term Dynamic Liquidity** — rolling forward-looking cash flow across a 1–90 day horizon, prepared more frequently than regulatory submission requires.
- **Statement of Interest Rate Sensitivity** — repricing gap between rate-sensitive assets and liabilities, determining asset-sensitivity vs. liability-sensitivity to rate moves.
- Alongside these: deposit concentration, investment/treasury book exposure, often a liquidity stress test/contingency funding plan view, and board-fixed tolerances per bucket — any breach requires documented explanation and, in some frameworks, Board approval to operate above the limit.

The pack goes to ALCO first, then to the **Board** — a governance artifact with its own paper trail, structurally parallel to the STR.

### Honest confidence note

The **regulatory skeleton** — filing deadlines, bucket structures, threshold percentages, who signs what — is grounded in primary sources (RBI circulars, PML Rules, FIU-IND documentation) and can be cited precisely. The **lived texture** — what it feels like to work a 1,400-alert Monday queue, the specific friction points a supervisor pushes back on — is reconstructed from how vendors, consultants, and regulators describe the job, not from direct practitioner experience. Before building the demo script around the "Priya" persona, it's worth pressure-testing that narrative against a real practitioner account (ACAMS forum threads, Wolfsberg Group publications, or AML analyst interviews) so the demo doesn't unintentionally recite vendor marketing copy as lived experience.
