# Technical Architecture & Schema Specification
## AI-Powered Risk & Compliance Copilot — Real-Time Fraud Detection & AML Compliance

| | |
|---|---|
| Status | Draft |
| Last updated | 2026-09-19 |
| Companion doc | [SOLUTION-PLAN.md](./SOLUTION-PLAN.md) |

> **Governing principle:** *The chain is the product.* Every fraud alert, entity link,
> and AML finding is backed by an immutable, deterministic, re-verifiable evidence chain
> stored inside Snowflake. No regulatory figure is ever computed by an LLM.

---

## Contents

- [0. Real-World Relevance — Why This Schema Exists](#0-real-world-relevance--why-this-schema-exists)
  - [0.1 How Compliance Actually Works (And Where It Breaks)](#01-how-compliance-actually-works-and-where-it-breaks)
  - [0.2 Schema-to-Workflow Mapping](#02-schema-to-workflow-mapping)
  - [0.3 Why Each Table Exists — Traced to Regulatory Requirements](#03-why-each-table-exists--traced-to-regulatory-requirements)
- [A. System-Wide Database Schema (Snowflake DDL)](#a-system-wide-database-schema-snowflake-ddl)
  - [A.1 Database & Schema Layout](#a1-database--schema-layout)
  - [A.2 CORE Schema — Entity Resolution & Transaction Spine](#a2-core-schema--entity-resolution--transaction-spine)
  - [A.3 DETECTION Schema — Signals, Cases & Evidence](#a3-detection-schema--signals-cases--evidence)
  - [A.4 POLICY Schema — Policy-as-Code](#a4-policy-schema--policy-as-code)
  - [A.5 COMPLIANCE Schema — Findings & Artifacts](#a5-compliance-schema--findings--artifacts)
  - [A.6 AUDIT Schema — Attestation & Governance Logs](#a6-audit-schema--attestation--governance-logs)
- [B. Graph Schema & Live Fraud Query Specification](#b-graph-schema--live-fraud-query-specification)
  - [B.1 Entity Resolution via Shared Attributes](#b1-entity-resolution-via-shared-attributes)
  - [B.2 Edge Types & Confidence Scoring](#b2-edge-types--confidence-scoring)
  - [B.3 Bounded Recursive CTE — 3-Hop Mule Ring Detection](#b3-bounded-recursive-cte--3-hop-mule-ring-detection)
- [C. Policy Document Ingestion & Change Management](#c-policy-document-ingestion--change-management)
  - [C.1 Ingestion Pipeline Schema](#c1-ingestion-pipeline-schema)
  - [C.2 Regulatory Change Management Workflow](#c2-regulatory-change-management-workflow)
- [D. Operational Lifecycle Walkthrough](#d-operational-lifecycle-walkthrough)
- [E. Trade-offs & Limitations](#e-trade-offs--limitations)
  - [E.1 Technology Comparison](#e1-technology-comparison)
  - [E.2 Failure Modes & Mitigations](#e2-failure-modes--mitigations)
- [G. AMLSim — Synthetic Data Generation & Schema Alignment](#g-amlsim--synthetic-data-generation--schema-alignment)
  - [G.1 Why AMLSim (And Why Not GANs or SMOTE)](#g1-why-amlsim-and-why-not-gans-or-smote)
  - [G.2 AMLSim Output-to-Schema Column Mapping](#g2-amlsim-output-to-schema-column-mapping)
  - [G.3 Typology-to-Detection Alignment](#g3-typology-to-detection-alignment)
  - [G.4 AMLSim Configuration for This Project](#g4-amlsim-configuration-for-this-project)
  - [G.5 Ingestion Pipeline — AMLSim Outputs to Snowflake](#g5-ingestion-pipeline--amlsim-outputs-to-snowflake)
  - [G.6 Ground Truth & Detection Validation](#g6-ground-truth--detection-validation)
- [H. Real-World Case Study — Bank of Baroda Advance Remittance Scam](#h-real-world-case-study--bank-of-baroda-advance-remittance-scam)
  - [H.1 Case Background](#h1-case-background)
  - [H.2 Phase-by-Phase Schema Mapping](#h2-phase-by-phase-schema-mapping)
  - [H.3 Production SQL — L1 Rule-Based Detection](#h3-production-sql--l1-rule-based-detection)
  - [H.4 Feature Engineering Pipeline — L2 ML-Based Detection](#h4-feature-engineering-pipeline--l2-ml-based-detection)
  - [H.5 Schema Gap Analysis — What's Missing for Full Regulatory Coverage](#h5-schema-gap-analysis--whats-missing-for-full-regulatory-coverage)
  - [H.6 How the Full Evidence Chain Would Have Worked](#h6-how-the-full-evidence-chain-would-have-worked)
- [F. Appendices](#f-appendices)
  - [F.1 Entity-Relationship Diagram](#f1-entity-relationship-diagram)
  - [F.2 Schema Privilege Matrix](#f2-schema-privilege-matrix)
  - [F.3 Glossary](#f3-glossary)

---

## 0. Real-World Relevance — Why This Schema Exists

### 0.1 How Compliance Actually Works (And Where It Breaks)

To understand why each table in this schema exists, you need to understand what a compliance team in an Indian bank or NBFC actually does day-to-day — and where the process breaks down.

**The real workflow in a bank's AML/Fraud unit:**

```
Customer onboards                 ← KYC + risk rating + PEP/sanctions screening
    │
Transactions flow in (24/7)       ← core banking, UPI, NEFT, RTGS, SWIFT, cards
    │
Transaction Monitoring System     ← rules engine fires alerts (e.g., "cash > 10L",
    │                                "velocity spike", "structuring pattern")
    │
    ├── 97% of alerts → closed    ← analyst triages, marks as false positive with reason
    │    as false positives           (THIS is the most time-consuming part of the job)
    │
    └── 3% of alerts → escalated  ← becomes a "case" assigned to a senior analyst
         │
         ├── Analyst investigates: pulls transaction history, checks KYC,
         │   looks at related accounts, checks sanctions lists, reads
         │   policy documents to determine which rules apply
         │
         ├── Writes a narrative:  "Party X received ₹15L across 6 transactions
         │   from 3 unrelated parties over 4 days, forwarding 95%+ within
         │   minutes. Accounts share device ID DEV_77. This is consistent
         │   with mule network activity per PMLA Rule 3(1)(D)."
         │
         ├── MLRO reviews the narrative, checks the numbers against source
         │   data, verifies the policy citation is correct and current
         │
         └── Files STR with FIU-IND (if warranted) OR documents why
             no filing is needed (equally important — the "no STR" path
             is itself a regulatory deliverable)
```

**Where it breaks in practice:**

| Real Problem | What Goes Wrong | How Our Schema Addresses It |
|---|---|---|
| **Analyst spends 70% of time pulling data** | Transaction history, KYC docs, related accounts, sanctions hits are in 4-5 different systems. Analyst manually copies data into Excel. | `CORE` schema unifies Party, Account, Transaction, and Exposure on a single `party_id`. One query, one truth. |
| **"Which rule applies?" is a 30-minute question** | Analyst must find the right circular, check if it's been superseded, verify the effective date, read 40 pages to find the threshold. | `POLICY_CLAUSE` stores every rule as structured data: metric + operator + threshold + effective_from/to. A SQL query answers in milliseconds what used to take 30 minutes of PDF reading. |
| **Numbers in the STR don't match source data** | Analyst manually calculates totals. Typos, stale data, wrong date range. Regulator finds a discrepancy during examination — credibility destroyed. | `EVIDENCE` stores pointers with `as_of_ts` + `query_id`. The adversarial reviewer verifies every numeral in the narrative traces to a query result. Numbers are never manually transcribed. |
| **"Who else is connected to this party?"** | Analyst checks one account at a time. Misses that accounts B, C, D share a device with the suspect. The mule ring is partially investigated. | `PARTY_RELATIONSHIP` graph edge table + bounded recursive CTE. The system finds the full ring in one query — connections the analyst would never manually discover. |
| **Policy changed but old threshold still in use** | A new RBI circular raised the CTR threshold 6 months ago. The TMS still uses the old value. Alerts fire on the wrong threshold. Compliance team doesn't realize until an audit. | `POLICY_CLAUSE` has `effective_from`/`effective_to`/`superseded_by`. The regulatory change management pipeline (Section C.2) diffs new circulars against existing clauses and surfaces impacts. |
| **"Prove you investigated this properly"** | During an RBI examination, the inspector asks: "On what data did you base this finding? When did you look at it? What was the data at that time?" The analyst has no answer beyond "I remember looking at it." | `EVIDENCE` with `as_of_ts` + Snowflake Time Travel = re-verifiable proof. The auditor can run `SELECT * FROM TRANSACTION AT(TIMESTAMP => '2026-09-19 14:30:00') WHERE txn_id = 'TXN001'` and see exactly what the analyst saw. |
| **Maker-checker is on paper** | Sign-off is an email or a wet signature on a printout. No machine-readable audit trail. During examination, proving segregation of duties requires pulling email archives. | `ATTESTATION` table: immutable, append-only, digitally hashed. Every sign-off is a row with attestor, role, action, timestamp, and signature hash. Segregation of duties is enforced by schema — an analyst cannot approve their own finding. |

### 0.2 Schema-to-Workflow Mapping

Every table in the schema corresponds to a concrete step in a real compliance workflow. None exist for architectural elegance — each earns its place by solving a documented operational problem.

```
REAL COMPLIANCE WORKFLOW                    SCHEMA TABLE(S)
═══════════════════════                    ═══════════════

Customer Onboarding & KYC ─────────────── CORE.PARTY
  "Who is this person? Are they a PEP?       (kyc_status, is_pep, risk_rating,
   Are they on a sanctions list?              sanctions_hit, cdd_level)
   What's their risk rating?"

Account Opening ───────────────────────── CORE.ACCOUNT
  "What product? What branch?                (account_type, status, current_balance)
   What's the balance?"

Daily Transaction Flow ────────────────── CORE.TRANSACTION
  "₹5L from Account A to Account B          (amount, channel, device_id, ip_address,
   via UPI at 14:32 from device DEV_77       geo_lat/lon, is_cash, is_cross_border)
   in Mumbai"

Credit Exposure Monitoring ────────────── CORE.EXPOSURE
  "This borrower is 45 DPD on a ₹2Cr        (dpd, sma_stage, ecl_stage,
   term loan. SMA-1 staging. Provision       provision_amount, rwa_amount)
   at 5%. RWA for CRAR."

Transaction Monitoring Alert ──────────── DETECTION.SIGNAL
  "Rule VELOCITY_PASSTHROUGH_V3 fired:       (rule_id, typology, severity,
   pass-through in 4 minutes, 99%            confidence_score, signal_detail,
   forwarded. Severity: HIGH."               policy_clause_id)

Alert Triage & Investigation ──────────── DETECTION.CASE
  "Group SIG001 + SIG002 into case           (case_type, status, priority,
   AML-2026-00347. Assign to Senior          assigned_to, sla_due_at)
   Analyst Ravi. SLA: 48 hours."

Evidence Gathering ────────────────────── DETECTION.EVIDENCE
  "Collect TXN001, TXN002, the device        (ref_type, ref_id, as_of_ts,
   link, the policy clause. Record           query_id — POINTERS not copies)
   exactly when and how we looked
   at each piece."

Policy Rule Lookup ────────────────────── POLICY.POLICY_CLAUSE
  "PMLA Rule 3(1)(D): pass-through           (metric, operator, threshold_value,
   velocity < 10 min with > 95%              effective_from, action_required)
   forwarded → FILE_STR"

Reasoned Conclusion ───────────────────── COMPLIANCE.FINDING
  "Based on evidence EV001-EV006 and         (narrative, cited_evidence_ids,
   policy clauses CL_AML_017 and             cited_clause_ids, status,
   CL_AML_022: STR is warranted.             reconciliation_passed)
   Status: DRAFT."

STR Narrative Drafting ────────────────── COMPLIANCE.ARTIFACT
  "Generated STR narrative with all          (artifact_type, content_text,
   required sections. Reconciliation         reconciliation_status)
   gate: every number verified."

MLRO Review & Sign-off ───────────────── AUDIT.ATTESTATION
  "MLRO Priya Sharma reviewed and            (attestor_role, action, comment,
   approved. Segregation verified:           signature_hash — APPEND-ONLY)
   different person from drafter."

Regulatory Filing ─────────────────────── COMPLIANCE.FINDING (status → FILED)
  "STR filed with FIU-IND.                   + AUDIT.GOVERNANCE_EVENT
   Acknowledgement: FIU-STR-2026-XXXXX."     (event_type: FINDING_STATUS_CHANGED)
```

### 0.3 Why Each Table Exists — Traced to Regulatory Requirements

| Table | Regulatory Requirement | What Happens Without It |
|---|---|---|
| **PARTY** | PMLA Section 12: Maintain records of identity of clients. RBI KYC Master Direction: risk-based CDD, PEP screening. | Cannot demonstrate you know who your customers are. Examination finding. |
| **PARTY_RELATIONSHIP** | FATF Recommendation 24/25: Beneficial ownership transparency. RBI KYC: identify related parties and beneficial owners. | Miss connected parties. A mule ring member is investigated alone when 3 others go undetected. Systemic failure. |
| **ACCOUNT** | PMLA Rules: maintain records of all accounts. RBI: account classification for regulatory reporting. | Cannot link transactions to customers. Basic operational failure. |
| **TRANSACTION** | PMLA Rule 3: records of all transactions. CTR for cash > threshold. | No data to monitor. Cannot file CTR/STR. Criminal non-compliance. |
| **EXPOSURE** | RBI IRAC norms: DPD/SMA/NPA staging. Ind AS 109: ECL provisioning. Basel/CRAR: risk-weighted assets. | Incorrect provisioning, incorrect capital adequacy. Financial misstatement. |
| **SIGNAL** | RBI Master Direction on Fraud: institutions must have automated transaction monitoring. PMLA: obligation to detect suspicious transactions. | Cannot demonstrate monitoring capability to the regulator. Also: buried in false positives because no systematic triage. |
| **CASE** | RBI Fraud Management: documented investigation for every escalated alert. PMLA: maintain investigation records for 5 years post-relationship. | No audit trail of investigation. Inspector asks "how many cases did you close last quarter and what was the outcome?" — no answer. |
| **EVIDENCE** | PMLA Section 12: records sufficient for reconstruction. RBI: "trail of evidence" requirement for fraud cases. FIU-IND STR form: requires supporting evidence references. | The STR says "₹15L in suspicious transactions" but the auditor cannot verify where that number came from. Credibility destroyed. |
| **POLICY_CLAUSE** | Every rule the system enforces must trace to a written policy or regulation. RBI examination: "show me which policy governs this threshold." | Threshold in code with no policy trace. Examiner asks "where is this ₹10L limit specified?" — team cannot answer. Also: policy changes silently, old threshold stays active, wrong alerts fire for months. |
| **FINDING** | PMLA Rule 7: principal officer must record reasons for filing or not filing STR. "No STR warranted" is itself a regulatory deliverable. | No documented reasoning. If challenged, the institution cannot explain its filing decisions. |
| **ARTIFACT** | FIU-IND: STR must contain specific narrative sections. RBI: regulatory returns in prescribed formats. | Hand-written narratives with inconsistent quality. Some miss required sections. Numbers don't match. Filing rejected or questioned. |
| **ATTESTATION** | PMLA: principal officer personally responsible. RBI: segregation of duties in compliance function. | Cannot prove who reviewed what and when. No evidence of segregation. During examination, this is a governance failure finding. |
| **GOVERNANCE_EVENT** | General audit requirement: all material state changes must be logged with actor and timestamp. | No trail of when a case was opened, when a policy was changed, when an alert was suppressed. Reconstruction during an inquiry is impossible. |

---

## A. System-Wide Database Schema (Snowflake DDL)

### A.1 Database & Schema Layout

```
RISK_COPILOT (database)
├── CORE           — Entity resolution, transaction spine, exposures
├── DETECTION      — Signals, cases, evidence pointers
├── POLICY         — Policy documents & extracted clauses (policy-as-code)
├── COMPLIANCE     — Findings, artifacts (STR drafts, LCR statements)
├── AUDIT          — Attestation logs, governance events (append-only)
└── STAGING        — Raw ingestion, intermediate parse outputs
```

**Design conventions applied throughout:**

- All primary keys use `VARCHAR` surrogate IDs (UUIDs) — never auto-increment integers — to support distributed generation and cross-system traceability.
- `created_at` / `updated_at` timestamps on every table, defaulting to `CURRENT_TIMESTAMP()`.
- Audit-critical tables (`FINDING`, `ATTESTATION`, `EVIDENCE`) are **append-only**: updates create new revision rows; no in-place mutation.
- `DATA_RETENTION_TIME_IN_DAYS = 90` on audit tables to guarantee Time Travel re-verification.
- `CHANGE_TRACKING = TRUE` on tables that feed dynamic tables or streams.
- Clustering keys on high-cardinality timestamp columns for partition pruning.

```sql
-- ============================================================
-- DATABASE & SCHEMA CREATION
-- ============================================================
CREATE DATABASE IF NOT EXISTS RISK_COPILOT
  DATA_RETENTION_TIME_IN_DAYS = 90
  COMMENT = 'AI-powered Risk & Compliance Copilot — Fraud Detection & AML';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.CORE
  COMMENT = 'Entity resolution, transaction spine, exposures';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.DETECTION
  COMMENT = 'Signals, cases, evidence chain';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.POLICY
  COMMENT = 'Policy-as-code: parsed documents and extracted clauses';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.COMPLIANCE
  COMMENT = 'Findings, artifacts, regulatory outputs';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.AUDIT
  COMMENT = 'Immutable attestation and governance event logs';

CREATE SCHEMA IF NOT EXISTS RISK_COPILOT.STAGING
  COMMENT = 'Raw ingestion and intermediate processing';
```

---

### A.2 CORE Schema — Entity Resolution & Transaction Spine

#### PARTY

The entity-resolution anchor. Every customer, counterparty, and beneficial owner resolves to a single `party_id`, regardless of which system originated the record.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.PARTY (
    party_id            VARCHAR(64)     NOT NULL,
    party_type          VARCHAR(20)     NOT NULL,       -- INDIVIDUAL | CORPORATE | TRUST | GOVT
    full_name           VARCHAR(500)    NOT NULL,
    date_of_birth       DATE,
    nationality         VARCHAR(3),                     -- ISO 3166-1 alpha-3
    jurisdiction        VARCHAR(3)      NOT NULL,       -- domicile jurisdiction
    tax_id              VARCHAR(100),
    -- KYC & risk
    kyc_status          VARCHAR(20)     NOT NULL        -- PENDING | VERIFIED | EXPIRED | REJECTED
                        DEFAULT 'PENDING',
    kyc_verified_at     TIMESTAMP_NTZ,
    kyc_expiry_date     DATE,
    cdd_level           VARCHAR(10)     NOT NULL        -- SDD | CDD | EDD
                        DEFAULT 'CDD',
    is_pep              BOOLEAN         NOT NULL DEFAULT FALSE,
    pep_category        VARCHAR(50),                    -- DOMESTIC | FOREIGN | IO | FAMILY | ASSOCIATE
    risk_rating         VARCHAR(10)     NOT NULL        -- LOW | MEDIUM | HIGH | VERY_HIGH
                        DEFAULT 'MEDIUM',
    risk_rating_reason  VARCHAR(2000),
    sanctions_hit       BOOLEAN         NOT NULL DEFAULT FALSE,
    sanctions_list_ref  VARCHAR(500),                   -- e.g. 'OFAC_SDN:12345; UN:6789'
    -- entity resolution metadata
    source_system       VARCHAR(100)    NOT NULL,       -- originating system
    source_entity_id    VARCHAR(200)    NOT NULL,       -- ID in source system
    match_cluster_id    VARCHAR(64),                    -- groups resolved duplicates
    match_confidence    FLOAT,                          -- 0.0–1.0 resolution confidence
    -- housekeeping
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_party PRIMARY KEY (party_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (jurisdiction, risk_rating)
COMMENT = 'Entity-resolution anchor for all customers, counterparties, and beneficial owners';
```

#### ACCOUNT

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.ACCOUNT (
    account_id          VARCHAR(64)     NOT NULL,
    party_id            VARCHAR(64)     NOT NULL,       -- owning party
    account_number      VARCHAR(100)    NOT NULL,
    account_type        VARCHAR(30)     NOT NULL,       -- SAVINGS | CURRENT | LOAN | OD | FD | NRE | NRO
    product_code        VARCHAR(50),
    currency            VARCHAR(3)      NOT NULL DEFAULT 'INR',
    opening_date        DATE            NOT NULL,
    closing_date        DATE,
    status              VARCHAR(20)     NOT NULL        -- ACTIVE | DORMANT | FROZEN | CLOSED
                        DEFAULT 'ACTIVE',
    current_balance     NUMBER(18,2)    NOT NULL DEFAULT 0,
    available_balance   NUMBER(18,2)    NOT NULL DEFAULT 0,
    credit_limit        NUMBER(18,2),
    branch_code         VARCHAR(20),
    relationship_manager VARCHAR(100),
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_account PRIMARY KEY (account_id),
    CONSTRAINT fk_account_party FOREIGN KEY (party_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (party_id)
COMMENT = 'Bank accounts owned by parties';
```

#### TRANSACTION

The operational spine. Every financial movement is a row here. This is the highest-volume table and the primary target for Snowpipe Streaming ingestion in the fast lane.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.TRANSACTION (
    txn_id              VARCHAR(64)     NOT NULL,
    txn_timestamp       TIMESTAMP_NTZ   NOT NULL,
    txn_date            DATE            NOT NULL,       -- partition-friendly date
    -- debit side
    debit_account_id    VARCHAR(64)     NOT NULL,
    debit_party_id      VARCHAR(64)     NOT NULL,
    -- credit side
    credit_account_id   VARCHAR(64)     NOT NULL,
    credit_party_id     VARCHAR(64)     NOT NULL,
    -- financial
    amount              NUMBER(18,2)    NOT NULL,
    currency            VARCHAR(3)      NOT NULL DEFAULT 'INR',
    amount_usd          NUMBER(18,2),                   -- normalized for cross-currency comparison
    -- channel & device
    channel             VARCHAR(30)     NOT NULL,       -- BRANCH | ATM | NETBANKING | UPI | NEFT | RTGS | SWIFT | CARD_POS | CARD_ECOM
    payment_rail        VARCHAR(30),                    -- UPI | IMPS | NEFT | RTGS | SWIFT | INTERNAL
    device_id           VARCHAR(200),
    ip_address          VARCHAR(45),                    -- IPv4 or IPv6
    geo_lat             FLOAT,
    geo_lon             FLOAT,
    geo_city            VARCHAR(100),
    geo_country         VARCHAR(3),
    -- classification
    txn_type            VARCHAR(30)     NOT NULL,       -- CREDIT | DEBIT | TRANSFER | CASH_DEPOSIT | CASH_WITHDRAWAL | CARD_PURCHASE
    txn_subtype         VARCHAR(50),
    purpose_code        VARCHAR(20),                    -- CBWTR purpose code
    remittance_info     VARCHAR(2000),
    -- counterparty (external, when not in our system)
    counterparty_name   VARCHAR(500),
    counterparty_bank   VARCHAR(200),
    counterparty_country VARCHAR(3),
    -- flags
    is_cash             BOOLEAN         NOT NULL DEFAULT FALSE,
    is_cross_border     BOOLEAN         NOT NULL DEFAULT FALSE,
    is_high_risk_country BOOLEAN        NOT NULL DEFAULT FALSE,
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_transaction PRIMARY KEY (txn_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (txn_date, debit_party_id, credit_party_id)
COMMENT = 'Financial transaction spine — primary ingestion target for Snowpipe Streaming';
```

#### PARTY_RELATIONSHIP

The graph edge table. Every shared attribute (device, IP, phone, address) and every known relationship (ownership, family, fund flow) between two parties is an edge. This table enables bounded recursive CTE traversals for mule-ring and related-party detection.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.PARTY_RELATIONSHIP (
    relationship_id     VARCHAR(64)     NOT NULL,
    party_id_a          VARCHAR(64)     NOT NULL,
    party_id_b          VARCHAR(64)     NOT NULL,
    relationship_type   VARCHAR(30)     NOT NULL,
    -- Relationship types:
    --   FUND_FLOW        — A sent money to B (directional)
    --   SHARED_DEVICE    — A and B transacted from the same device_id
    --   SHARED_IP        — A and B transacted from the same IP address
    --   SHARED_PHONE     — A and B registered the same phone number
    --   SHARED_ADDRESS   — A and B share a registered address
    --   SHARED_EMAIL     — A and B share an email address
    --   BENEFICIAL_OWNER — A is beneficial owner of B (corporate)
    --   FAMILY           — family/household relationship
    --   DIRECTOR         — A is director/officer of B (corporate)
    --   GUARANTOR        — A guarantees B's obligation
    direction           VARCHAR(10)     NOT NULL        -- DIRECTED | UNDIRECTED
                        DEFAULT 'UNDIRECTED',
    -- evidence for this edge
    shared_attribute_value VARCHAR(500),                -- the actual device_id, IP, phone, etc.
    supporting_txn_ids  ARRAY,                          -- txn_ids that evidence this link
    first_seen_at       TIMESTAMP_NTZ   NOT NULL,
    last_seen_at        TIMESTAMP_NTZ   NOT NULL,
    occurrence_count    INTEGER         NOT NULL DEFAULT 1,
    -- confidence
    match_confidence    FLOAT           NOT NULL        -- 0.0–1.0
                        DEFAULT 1.0,
    match_method        VARCHAR(50),                    -- EXACT | FUZZY | DEVICE_FINGERPRINT | IP_SUBNET
    -- fund flow specifics (when relationship_type = 'FUND_FLOW')
    total_flow_amount   NUMBER(18,2),
    avg_pass_through_minutes FLOAT,                     -- average time funds stay before forwarding
    -- status
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    reviewed_by         VARCHAR(100),
    reviewed_at         TIMESTAMP_NTZ,
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_party_relationship PRIMARY KEY (relationship_id),
    CONSTRAINT fk_rel_party_a FOREIGN KEY (party_id_a) REFERENCES RISK_COPILOT.CORE.PARTY(party_id),
    CONSTRAINT fk_rel_party_b FOREIGN KEY (party_id_b) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (relationship_type, party_id_a, party_id_b)
COMMENT = 'Graph edge table: shared attributes, fund flows, and known relationships between parties';
```

#### EXPOSURE

Credit position for the credit-risk and prudential-metric lanes.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.EXPOSURE (
    exposure_id         VARCHAR(64)     NOT NULL,
    account_id          VARCHAR(64)     NOT NULL,
    party_id            VARCHAR(64)     NOT NULL,
    -- position
    product_type        VARCHAR(30)     NOT NULL,       -- TERM_LOAN | OD | CC | MORTGAGE | WORKING_CAPITAL
    sanctioned_limit    NUMBER(18,2)    NOT NULL,
    outstanding_amount  NUMBER(18,2)    NOT NULL,
    drawn_amount        NUMBER(18,2)    NOT NULL,
    undrawn_amount      NUMBER(18,2)    NOT NULL,
    currency            VARCHAR(3)      NOT NULL DEFAULT 'INR',
    -- credit quality
    dpd                 INTEGER         NOT NULL DEFAULT 0,   -- days past due
    sma_stage           VARCHAR(10),                    -- SMA-0 | SMA-1 | SMA-2 | NPA
    npa_classification  VARCHAR(20),                    -- SUBSTANDARD | DOUBTFUL_1 | DOUBTFUL_2 | DOUBTFUL_3 | LOSS
    ecl_stage           INTEGER,                        -- 1 | 2 | 3 (Ind AS 109)
    provision_amount    NUMBER(18,2)    NOT NULL DEFAULT 0,
    provision_pct       FLOAT,
    -- collateral
    collateral_type     VARCHAR(50),
    collateral_value    NUMBER(18,2)    DEFAULT 0,
    ltv_ratio           FLOAT,
    -- risk weight (for CRAR)
    risk_weight_pct     FLOAT,                          -- Basel risk weight percentage
    rwa_amount          NUMBER(18,2),                   -- risk-weighted asset amount
    -- dates
    origination_date    DATE            NOT NULL,
    maturity_date       DATE,
    next_repayment_date DATE,
    last_repayment_date DATE,
    npa_date            DATE,
    -- as-of
    as_of_date          DATE            NOT NULL,       -- reporting date for this snapshot
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_exposure PRIMARY KEY (exposure_id),
    CONSTRAINT fk_exposure_account FOREIGN KEY (account_id) REFERENCES RISK_COPILOT.CORE.ACCOUNT(account_id),
    CONSTRAINT fk_exposure_party FOREIGN KEY (party_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (as_of_date, party_id)
COMMENT = 'Credit exposure positions — daily snapshots for DPD/SMA staging, provisioning, and CRAR';
```

---

### A.3 DETECTION Schema — Signals, Cases & Evidence

#### SIGNAL

Low-level, high-volume rule/model firings. One rule firing on one entity at one time = one signal row. Signals are cheap to create and most will be benign.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.DETECTION.SIGNAL (
    signal_id           VARCHAR(64)     NOT NULL,
    signal_timestamp    TIMESTAMP_NTZ   NOT NULL,
    -- what fired
    rule_id             VARCHAR(100)    NOT NULL,       -- detection rule or model identifier
    rule_version        VARCHAR(20)     NOT NULL,
    rule_category       VARCHAR(30)     NOT NULL,       -- VELOCITY | STRUCTURING | GRAPH | BEHAVIORAL | SANCTIONS | THRESHOLD
    typology            VARCHAR(50)     NOT NULL,       -- MULE_RING | ATO | SMURFING | PASSTHROUGH | PEP_TXN | HIGH_RISK_COUNTRY | CTR_THRESHOLD
    -- what it fired on
    entity_type         VARCHAR(20)     NOT NULL,       -- PARTY | ACCOUNT | TRANSACTION
    entity_id           VARCHAR(64)     NOT NULL,       -- party_id / account_id / txn_id
    related_txn_ids     ARRAY,                          -- transaction IDs that triggered this signal
    -- scoring
    severity            VARCHAR(10)     NOT NULL,       -- LOW | MEDIUM | HIGH | CRITICAL
    confidence_score    FLOAT           NOT NULL,       -- 0.0–1.0
    risk_score          FLOAT,                          -- composite risk score
    -- context
    signal_detail       VARIANT         NOT NULL,       -- structured detail: thresholds, computed values, matched patterns
    -- Example signal_detail for a velocity signal:
    -- {
    --   "pass_through_minutes": 4.2,
    --   "inbound_amount": 500000,
    --   "outbound_amount": 495000,
    --   "retained_pct": 1.0,
    --   "threshold_minutes": 10,
    --   "matched_device_id": "DEV_ABC123"
    -- }
    policy_clause_id    VARCHAR(64),                    -- policy clause that defines this threshold (FK to POLICY_CLAUSE)
    -- disposition
    disposition         VARCHAR(20)     NOT NULL        -- OPEN | ESCALATED | SUPPRESSED | FALSE_POSITIVE | CLOSED_NO_ACTION
                        DEFAULT 'OPEN',
    case_id             VARCHAR(64),                    -- set when escalated to a case
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_signal PRIMARY KEY (signal_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (signal_timestamp, typology)
COMMENT = 'Low-level detection signals: one rule/model firing on one entity at one time';
```

#### CASE

Groups related signals into an investigable unit. The case is the unit of work for a compliance analyst.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.DETECTION.CASE (
    case_id             VARCHAR(64)     NOT NULL,
    case_number         VARCHAR(30)     NOT NULL,       -- human-readable case number (e.g. FRD-2026-00142)
    case_type           VARCHAR(30)     NOT NULL,       -- FRAUD | AML | SANCTIONS | THRESHOLD | MULTI_DOMAIN
    -- subject
    primary_party_id    VARCHAR(64)     NOT NULL,
    related_party_ids   ARRAY,                          -- all parties involved (ring members, etc.)
    related_account_ids ARRAY,
    -- status & workflow
    status              VARCHAR(20)     NOT NULL        -- OPEN | INVESTIGATING | UNDER_REVIEW | ESCALATED | CLOSED_NO_ACTION | CLOSED_STR_FILED | CLOSED_FALSE_POSITIVE
                        DEFAULT 'OPEN',
    priority            VARCHAR(10)     NOT NULL        -- P1 | P2 | P3 | P4
                        DEFAULT 'P2',
    -- ownership
    assigned_to         VARCHAR(100),                   -- analyst user ID
    assigned_team       VARCHAR(50),
    escalated_to        VARCHAR(100),
    -- SLA
    opened_at           TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    sla_due_at          TIMESTAMP_NTZ,
    first_response_at   TIMESTAMP_NTZ,
    resolved_at         TIMESTAMP_NTZ,
    -- aggregated risk
    total_signals       INTEGER         NOT NULL DEFAULT 0,
    max_severity        VARCHAR(10),
    composite_risk_score FLOAT,
    -- narrative
    summary             VARCHAR(4000),
    -- external references
    jira_ticket_id      VARCHAR(100),
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_case PRIMARY KEY (case_id),
    CONSTRAINT fk_case_party FOREIGN KEY (primary_party_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (status, case_type)
COMMENT = 'Investigation cases grouping related signals into a unit of work';
```

#### EVIDENCE

**The critical design choice.** Evidence stores **pointers, not copies**. A `(ref_id, as_of_ts, query_id)` triple is re-verifiable via `AT(TIMESTAMP => as_of_ts)` — the literal definition of audit-ready. Copied values are snapshots you must trust; pointers are proofs you can re-execute.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.DETECTION.EVIDENCE (
    evidence_id         VARCHAR(64)     NOT NULL,
    case_id             VARCHAR(64)     NOT NULL,
    finding_id          VARCHAR(64),                    -- linked after finding is created
    -- the pointer (core of the evidence-as-pointers architecture)
    ref_type            VARCHAR(30)     NOT NULL,       -- TRANSACTION | SIGNAL | PARTY | ACCOUNT | EXPOSURE | POLICY_CLAUSE | PARTY_RELATIONSHIP | EXTERNAL
    ref_id              VARCHAR(64)     NOT NULL,       -- the ID of the referenced object
    ref_table           VARCHAR(200)    NOT NULL,       -- fully qualified table: RISK_COPILOT.CORE.TRANSACTION
    -- point-in-time verifiability
    as_of_ts            TIMESTAMP_NTZ   NOT NULL,       -- Time Travel anchor: AT(TIMESTAMP => as_of_ts)
    query_id            VARCHAR(100),                   -- Snowflake QUERY_ID that produced this evidence
    query_text          VARCHAR(10000),                 -- the SQL executed (for audit trail)
    -- context
    evidence_category   VARCHAR(30)     NOT NULL,       -- FINANCIAL | BEHAVIORAL | NETWORK | POLICY | IDENTITY | SANCTIONS
    description         VARCHAR(2000),                  -- human-readable note
    extracted_value     VARIANT,                        -- key computed values at collection time (for display only — source of truth is the pointer)
    -- Example extracted_value:
    -- {
    --   "amount": 500000,
    --   "pass_through_minutes": 4.2,
    --   "counterparty_count": 4,
    --   "note": "display copy only — re-verify via as_of_ts + query_id"
    -- }
    -- ordering
    sequence_num        INTEGER         NOT NULL,       -- order within the evidence bundle
    -- housekeeping (append-only: no updates)
    collected_at        TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    collected_by        VARCHAR(100)    NOT NULL,       -- 'SYSTEM:rule_engine' | 'AGENT:evidence_collector' | analyst user ID

    CONSTRAINT pk_evidence PRIMARY KEY (evidence_id),
    CONSTRAINT fk_evidence_case FOREIGN KEY (case_id) REFERENCES RISK_COPILOT.DETECTION.CASE(case_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Evidence chain: pointers (not copies) to source data with point-in-time re-verifiability';
```

---

### A.4 POLICY Schema — Policy-as-Code

#### POLICY_DOCUMENT

Metadata for ingested policy PDFs, circulars, and master directions.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.POLICY.POLICY_DOCUMENT (
    document_id         VARCHAR(64)     NOT NULL,
    -- source
    document_title      VARCHAR(1000)   NOT NULL,
    document_type       VARCHAR(30)     NOT NULL,       -- CIRCULAR | MASTER_DIRECTION | INTERNAL_POLICY | GUIDANCE_NOTE | AMENDMENT
    issuing_authority   VARCHAR(200)    NOT NULL,       -- RBI | FIU-IND | SEBI | INTERNAL | FATF | BCBS
    reference_number    VARCHAR(200),                   -- e.g. RBI/2024-25/XX or DoR.FIN.REC.XX/XX
    -- dates
    issue_date          DATE,
    effective_date      DATE,
    supersedes_doc_id   VARCHAR(64),                    -- FK to the document this replaces
    -- ingestion
    source_stage_path   VARCHAR(2000),                  -- @RISK_COPILOT.STAGING.POLICY_STAGE/path/to/file.pdf
    source_file_hash    VARCHAR(128),                   -- SHA-256 of original file
    ingestion_status    VARCHAR(20)     NOT NULL        -- PENDING | PARSED | EXTRACTED | REVIEWED | ACTIVE | REJECTED
                        DEFAULT 'PENDING',
    parsed_at           TIMESTAMP_NTZ,
    parsed_text         VARCHAR(16777216),              -- full extracted text from AI_PARSE_DOCUMENT
    -- review
    reviewed_by         VARCHAR(100),
    reviewed_at         TIMESTAMP_NTZ,
    review_comment      VARCHAR(4000),
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_policy_document PRIMARY KEY (document_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Metadata for ingested policy PDFs, circulars, and master directions';
```

#### POLICY_CLAUSE

The heart of policy-as-code. Each clause is a structured, effective-dated rule extracted from unstructured policy text. Detection rules and narrative citations reference the same `clause_id` — a threshold change is a data change, not a code change.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.POLICY.POLICY_CLAUSE (
    clause_id           VARCHAR(64)     NOT NULL,
    document_id         VARCHAR(64)     NOT NULL,
    -- identification
    clause_ref          VARCHAR(200)    NOT NULL,       -- e.g. 'Section 12.3(a)(ii)' or 'Para 4.2.1'
    clause_title        VARCHAR(500),
    -- source location
    source_page         INTEGER,
    source_section      VARCHAR(200),
    source_text_excerpt VARCHAR(4000),                  -- verbatim excerpt from the document
    -- structured rule
    domain              VARCHAR(30)     NOT NULL,       -- FRAUD | AML | CREDIT | LIQUIDITY | CAPITAL | OPERATIONAL
    subdomain           VARCHAR(50),                    -- e.g. CTR_REPORTING | STR_FILING | STRUCTURING_THRESHOLD | SMA_CLASSIFICATION
    applicability       VARIANT         NOT NULL,       -- JSON filter: {"entity_types": ["NBFC"], "asset_size_min": 5000000000, "jurisdictions": ["IN"]}
    -- the rule itself
    metric              VARCHAR(200)    NOT NULL,       -- what is measured: 'single_cash_txn_amount' | 'aggregate_cash_30d' | 'dpd' | 'pass_through_velocity_minutes'
    operator            VARCHAR(10)     NOT NULL,       -- GT | GTE | LT | LTE | EQ | NEQ | BETWEEN | IN
    threshold_value     VARIANT         NOT NULL,       -- single value or range: 1000000 or {"min": 0, "max": 10}
    threshold_currency  VARCHAR(3),                     -- when threshold is monetary
    action_required     VARCHAR(500)    NOT NULL,       -- 'FILE_CTR' | 'FILE_STR' | 'ESCALATE_TO_MLRO' | 'BLOCK_ACCOUNT' | 'EDD_REQUIRED'
    action_sla_hours    INTEGER,                        -- time to complete action
    -- temporal validity
    effective_from      DATE            NOT NULL,
    effective_to        DATE,                           -- NULL = currently in effect
    superseded_by       VARCHAR(64),                    -- clause_id of the replacing clause
    -- versioning
    version             INTEGER         NOT NULL DEFAULT 1,
    is_current          BOOLEAN         NOT NULL DEFAULT TRUE,
    -- extraction metadata
    extraction_confidence FLOAT,                        -- AI extraction confidence 0.0–1.0
    extraction_method   VARCHAR(30),                    -- AI_EXTRACT | MANUAL | HYBRID
    -- review & activation
    status              VARCHAR(20)     NOT NULL        -- PROPOSED | UNDER_REVIEW | ACTIVE | SUPERSEDED | WITHDRAWN
                        DEFAULT 'PROPOSED',
    reviewed_by         VARCHAR(100),
    reviewed_at         TIMESTAMP_NTZ,
    activated_by        VARCHAR(100),
    activated_at        TIMESTAMP_NTZ,
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_policy_clause PRIMARY KEY (clause_id),
    CONSTRAINT fk_clause_document FOREIGN KEY (document_id) REFERENCES RISK_COPILOT.POLICY.POLICY_DOCUMENT(document_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
CHANGE_TRACKING = TRUE
CLUSTER BY (domain, is_current)
COMMENT = 'Policy-as-code: structured, effective-dated rules extracted from policy documents';
```

---

### A.5 COMPLIANCE Schema — Findings & Artifacts

#### FINDING

A reasoned conclusion citing evidence and policy clauses. Append-only: revisions create new rows with `supersedes_finding_id` set. In-place updates are forbidden.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.COMPLIANCE.FINDING (
    finding_id          VARCHAR(64)     NOT NULL,
    case_id             VARCHAR(64)     NOT NULL,
    -- versioning (append-only)
    version             INTEGER         NOT NULL DEFAULT 1,
    supersedes_finding_id VARCHAR(64),                  -- previous version of this finding
    -- classification
    finding_type        VARCHAR(30)     NOT NULL,       -- STR_WARRANTED | NO_STR_WARRANTED | FRAUD_CONFIRMED | FRAUD_UNSUBSTANTIATED | THRESHOLD_BREACH | POLICY_VIOLATION
    domain              VARCHAR(30)     NOT NULL,       -- FRAUD | AML | CREDIT | LIQUIDITY | CAPITAL
    -- content
    title               VARCHAR(500)    NOT NULL,
    narrative           VARCHAR(16777216) NOT NULL,     -- the reasoned conclusion — cites evidence IDs and policy clause IDs
    summary             VARCHAR(4000),
    -- citations (structured references into the narrative)
    cited_evidence_ids  ARRAY           NOT NULL,       -- evidence_id[] that support this finding
    cited_clause_ids    ARRAY           NOT NULL,       -- clause_id[] from POLICY_CLAUSE
    cited_txn_count     INTEGER,
    cited_total_amount  NUMBER(18,2),
    -- point-in-time
    as_of_ts            TIMESTAMP_NTZ   NOT NULL,       -- the finding is valid as of this timestamp
    -- risk assessment
    severity            VARCHAR(10)     NOT NULL,       -- LOW | MEDIUM | HIGH | CRITICAL
    risk_score          FLOAT,
    -- workflow (maker-checker state machine)
    status              VARCHAR(20)     NOT NULL        -- DRAFT | UNDER_REVIEW | REVISION_REQUESTED | APPROVED | REJECTED | FILED | WITHDRAWN
                        DEFAULT 'DRAFT',
    -- Status transitions:
    --   DRAFT → UNDER_REVIEW (analyst submits)
    --   UNDER_REVIEW → APPROVED | REJECTED | REVISION_REQUESTED (reviewer acts)
    --   REVISION_REQUESTED → DRAFT (analyst revises)
    --   APPROVED → FILED (after regulatory submission)
    --   Any → WITHDRAWN (with reason)
    -- THE AGENT CANNOT TRANSITION TO FILED — only a human attester can.
    drafted_by          VARCHAR(100)    NOT NULL,       -- 'AGENT:narrative_drafter' | analyst user ID
    reviewed_by         VARCHAR(100),
    approved_by         VARCHAR(100),
    approved_at         TIMESTAMP_NTZ,
    filed_by            VARCHAR(100),
    filed_at            TIMESTAMP_NTZ,
    filing_reference    VARCHAR(200),                   -- FIU-IND acknowledgement number
    -- reconciliation gate
    reconciliation_passed BOOLEAN,                      -- TRUE if all numerals in narrative trace to evidence
    reconciliation_detail VARIANT,                      -- detailed check results from adversarial reviewer
    -- housekeeping (append-only: created_at only, no updated_at)
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_finding PRIMARY KEY (finding_id),
    CONSTRAINT fk_finding_case FOREIGN KEY (case_id) REFERENCES RISK_COPILOT.DETECTION.CASE(case_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Reasoned conclusions citing evidence and policy — append-only with revision chain';
```

#### ARTIFACT

Generated regulatory outputs: STR drafts, LCR statements, board memos. Each artifact links to exactly one finding.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.COMPLIANCE.ARTIFACT (
    artifact_id         VARCHAR(64)     NOT NULL,
    finding_id          VARCHAR(64)     NOT NULL,
    -- classification
    artifact_type       VARCHAR(30)     NOT NULL,       -- STR_DRAFT | CTR_DRAFT | LCR_STATEMENT | CRAR_REPORT | FRAUD_REPORT | BOARD_MEMO | CASE_SUMMARY
    format              VARCHAR(10)     NOT NULL        -- PDF | JSON | HTML | XML
                        DEFAULT 'JSON',
    -- content
    title               VARCHAR(500)    NOT NULL,
    content             VARIANT         NOT NULL,       -- structured artifact content
    content_text        VARCHAR(16777216),              -- rendered text/narrative version
    -- regulatory specifics
    regulatory_form     VARCHAR(50),                    -- FIU-IND-STR | FIU-IND-CTR | RBI-FMR
    reporting_period_start DATE,
    reporting_period_end   DATE,
    -- generation metadata
    generated_by        VARCHAR(100)    NOT NULL,       -- 'AGENT:narrative_drafter'
    generation_model    VARCHAR(100),                   -- LLM model used
    generation_prompt_hash VARCHAR(128),                -- hash of the prompt used
    -- reconciliation (from adversarial reviewer)
    reconciliation_status VARCHAR(20)   NOT NULL        -- PENDING | PASSED | FAILED | OVERRIDDEN
                        DEFAULT 'PENDING',
    reconciliation_report VARIANT,
    reconciliation_checked_at TIMESTAMP_NTZ,
    -- version
    version             INTEGER         NOT NULL DEFAULT 1,
    supersedes_artifact_id VARCHAR(64),
    -- storage
    stage_path          VARCHAR(2000),                  -- path to rendered file in stage
    -- housekeeping
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_artifact PRIMARY KEY (artifact_id),
    CONSTRAINT fk_artifact_finding FOREIGN KEY (finding_id) REFERENCES RISK_COPILOT.COMPLIANCE.FINDING(finding_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Generated regulatory outputs: STR drafts, LCR statements, board memos';
```

---

### A.6 AUDIT Schema — Attestation & Governance Logs

#### ATTESTATION

Immutable human sign-off records. **Append-only, no updates, no deletes.** Each row is a legally significant attestation that a human reviewed and accepted or rejected a finding/artifact.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.AUDIT.ATTESTATION (
    attestation_id      VARCHAR(64)     NOT NULL,
    -- what is being attested
    finding_id          VARCHAR(64)     NOT NULL,
    artifact_id         VARCHAR(64),
    -- who
    attestor_user_id    VARCHAR(100)    NOT NULL,
    attestor_name       VARCHAR(200)    NOT NULL,
    attestor_role       VARCHAR(50)     NOT NULL,       -- ANALYST | SENIOR_ANALYST | MLRO | COMPLIANCE_OFFICER | COMPLIANCE_HEAD
    attestor_department VARCHAR(100),
    -- action
    action              VARCHAR(20)     NOT NULL,       -- APPROVE | REJECT | ESCALATE | REQUEST_REVISION | ACKNOWLEDGE | FILE
    -- Segregation of duties:
    --   ANALYST can: ESCALATE, REQUEST_REVISION
    --   MLRO/COMPLIANCE_OFFICER can: APPROVE, REJECT, FILE
    --   No single person can both create a finding and approve it.
    comment             VARCHAR(4000),
    -- digital signature
    signature_hash      VARCHAR(256),                   -- SHA-256 of (finding_id || attestor_user_id || action || timestamp)
    -- context
    ip_address          VARCHAR(45),
    session_id          VARCHAR(200),
    -- housekeeping (append-only: created_at only)
    attested_at         TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_attestation PRIMARY KEY (attestation_id),
    CONSTRAINT fk_attestation_finding FOREIGN KEY (finding_id) REFERENCES RISK_COPILOT.COMPLIANCE.FINDING(finding_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Immutable human sign-off logs — append-only, legally significant attestations';
```

#### GOVERNANCE_EVENT

Audit trail for all state transitions, policy changes, and system actions.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.AUDIT.GOVERNANCE_EVENT (
    event_id            VARCHAR(64)     NOT NULL,
    event_timestamp     TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    -- what
    event_type          VARCHAR(50)     NOT NULL,
    -- Event types:
    --   CASE_CREATED | CASE_STATUS_CHANGED | CASE_ASSIGNED
    --   FINDING_CREATED | FINDING_STATUS_CHANGED
    --   EVIDENCE_COLLECTED
    --   POLICY_CLAUSE_PROPOSED | POLICY_CLAUSE_ACTIVATED | POLICY_CLAUSE_SUPERSEDED
    --   ARTIFACT_GENERATED | ARTIFACT_RECONCILIATION_PASSED | ARTIFACT_RECONCILIATION_FAILED
    --   ATTESTATION_RECORDED
    --   SANCTIONS_LIST_REFRESHED
    -- subject
    entity_type         VARCHAR(30)     NOT NULL,       -- CASE | FINDING | EVIDENCE | POLICY_CLAUSE | ARTIFACT | ATTESTATION | SIGNAL
    entity_id           VARCHAR(64)     NOT NULL,
    -- change detail
    old_value           VARIANT,
    new_value           VARIANT,
    change_reason       VARCHAR(2000),
    -- actor
    actor_type          VARCHAR(20)     NOT NULL,       -- SYSTEM | AGENT | HUMAN
    actor_id            VARCHAR(100)    NOT NULL,       -- user ID or agent name
    -- housekeeping (append-only)
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_governance_event PRIMARY KEY (event_id)
)
DATA_RETENTION_TIME_IN_DAYS = 90
COMMENT = 'Audit trail for all state transitions, policy changes, and system actions';
```

#### GROUND_TRUTH

For simulation validation: planted typologies with known labels enabling precision/recall measurement.

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.AUDIT.GROUND_TRUTH (
    ground_truth_id     VARCHAR(64)     NOT NULL,
    entity_type         VARCHAR(20)     NOT NULL,       -- PARTY | ACCOUNT | TRANSACTION
    entity_id           VARCHAR(64)     NOT NULL,
    typology            VARCHAR(50)     NOT NULL,       -- MULE_RING | ATO | SMURFING | SYNTHETIC_IDENTITY | HARD_NEGATIVE | CLEAN
    severity            VARCHAR(10)     NOT NULL,       -- LOW | MEDIUM | HIGH | CRITICAL | NONE (for hard negatives)
    expected_detection  BOOLEAN         NOT NULL,       -- TRUE = should be caught; FALSE = hard negative, should NOT fire
    planted_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    description         VARCHAR(2000),
    ring_id             VARCHAR(64),                    -- groups entities in the same planted ring/pattern

    CONSTRAINT pk_ground_truth PRIMARY KEY (ground_truth_id)
)
COMMENT = 'Planted typologies for detection validation — enables precision/recall measurement';
```

---

## B. Graph Schema & Live Fraud Query Specification

### B.1 Entity Resolution via Shared Attributes

Entity resolution in this system uses the `PARTY_RELATIONSHIP` edge table to connect parties who share identifying attributes. The resolution process works as follows:

1. **Attribute extraction.** During transaction ingestion and KYC processing, attributes are extracted: device IDs, IP addresses, phone numbers, email addresses, physical addresses.

2. **Edge creation.** When Party A and Party B share an attribute (e.g., the same `device_id` appears in transactions from both), a `PARTY_RELATIONSHIP` row is created with:
   - `relationship_type = 'SHARED_DEVICE'`
   - `shared_attribute_value = 'DEV_ABC123'`
   - `match_confidence` based on match quality (exact = 1.0, fuzzy/subnet = lower)

3. **Cluster assignment.** Connected components in the relationship graph are assigned a common `match_cluster_id` on `PARTY`. Parties in the same cluster are considered potentially the same entity or closely related.

4. **Confidence propagation.** Multi-attribute matches increase confidence: sharing both a device AND an IP address is stronger than either alone. This is stored as a composite score.

**Example: Two parties share a phone number**

```
Party A (party_id: P001) → registered phone: +91-98765-43210
Party B (party_id: P002) → registered phone: +91-98765-43210

→ PARTY_RELATIONSHIP row:
  party_id_a: P001, party_id_b: P002,
  relationship_type: SHARED_PHONE,
  shared_attribute_value: '+91-98765-43210',
  match_confidence: 1.0
```

### B.2 Edge Types & Confidence Scoring

| Edge Type | Direction | Confidence Factors | Fraud Relevance |
|---|---|---|---|
| `FUND_FLOW` | Directed (A→B) | Transaction verified = 1.0 | Core of mule-ring fan-in/fan-out |
| `SHARED_DEVICE` | Undirected | Exact device fingerprint = 1.0; browser fingerprint = 0.7 | Strong indicator of common control |
| `SHARED_IP` | Undirected | Static IP = 0.9; dynamic/VPN = 0.3 | Corroborating only (too many false positives alone) |
| `SHARED_PHONE` | Undirected | Exact match = 1.0 | Strong for synthetic identity detection |
| `SHARED_ADDRESS` | Undirected | Exact = 1.0; fuzzy/normalized = 0.7 | Moderate — legitimate for family |
| `SHARED_EMAIL` | Undirected | Exact = 1.0 | Strong for synthetic identity |
| `BENEFICIAL_OWNER` | Directed (owner→entity) | Registry verified = 1.0 | Required for EDD / related-party |
| `FAMILY` | Undirected | KYC declared = 1.0 | Context — reduces false positives |

### B.3 Bounded Recursive CTE — 3-Hop Mule Ring Detection

The following query detects mule-ring patterns by combining two signals:

1. **Fund-flow traversal**: money moves through a chain of accounts (fan-in → pass-through → fan-out) with rapid velocity (< N minutes) and near-zero retained balance.
2. **Shared-attribute corroboration**: accounts in the chain share devices or IPs, indicating common control despite nominally unrelated ownership.

```sql
-- ============================================================
-- MULE RING DETECTION: 3-Hop Bounded Recursive CTE
-- Combines fund-flow graph traversal with shared-device/IP corroboration
-- ============================================================
WITH RECURSIVE

-- Parameters (adjust thresholds here; these map to POLICY_CLAUSE entries)
params AS (
    SELECT
        10   AS max_pass_through_minutes,   -- funds must leave within this window
        5.0  AS max_retained_pct,           -- max % of inbound amount retained
        3    AS max_hops,                   -- traversal depth limit
        90   AS lookback_days               -- only consider recent activity
),

-- Step 1: Build directed fund-flow edges with velocity metrics
fund_flow_edges AS (
    SELECT
        t.debit_party_id   AS from_party,
        t.credit_party_id  AS to_party,
        t.txn_id,
        t.txn_timestamp,
        t.amount,
        t.device_id,
        t.ip_address
    FROM RISK_COPILOT.CORE.TRANSACTION t, params p
    WHERE t.txn_date >= DATEADD('day', -p.lookback_days, CURRENT_DATE())
      AND t.amount > 0
),

-- Step 2: Identify pass-through behavior (rapid in-then-out with near-zero retention)
pass_through_accounts AS (
    SELECT
        inbound.credit_party_id  AS party_id,
        inbound.credit_account_id AS account_id,
        inbound.txn_id            AS inbound_txn_id,
        outbound.txn_id           AS outbound_txn_id,
        inbound.amount            AS inbound_amount,
        outbound.amount           AS outbound_amount,
        ROUND(100.0 * (inbound.amount - outbound.amount) / NULLIF(inbound.amount, 0), 2)
                                  AS retained_pct,
        TIMESTAMPDIFF('minute', inbound.txn_timestamp, outbound.txn_timestamp)
                                  AS pass_through_minutes,
        inbound.txn_timestamp     AS inbound_ts,
        outbound.txn_timestamp    AS outbound_ts,
        outbound.debit_party_id   AS next_party
    FROM RISK_COPILOT.CORE.TRANSACTION inbound
    JOIN RISK_COPILOT.CORE.TRANSACTION outbound
      ON inbound.credit_account_id = outbound.debit_account_id
     AND outbound.txn_timestamp > inbound.txn_timestamp
    CROSS JOIN params p
    WHERE inbound.txn_date  >= DATEADD('day', -p.lookback_days, CURRENT_DATE())
      AND outbound.txn_date >= DATEADD('day', -p.lookback_days, CURRENT_DATE())
      AND TIMESTAMPDIFF('minute', inbound.txn_timestamp, outbound.txn_timestamp)
          <= p.max_pass_through_minutes
      AND ROUND(100.0 * (inbound.amount - outbound.amount) / NULLIF(inbound.amount, 0), 2)
          <= p.max_retained_pct
),

-- Step 3: Recursive traversal — follow the money up to max_hops
ring_traversal AS (
    -- Anchor: start from every pass-through account
    SELECT
        pt.party_id         AS origin_party,
        pt.party_id         AS current_party,
        pt.next_party       AS next_party,
        1                   AS hop,
        ARRAY_CONSTRUCT(pt.party_id, pt.next_party)
                            AS path,
        pt.inbound_amount   AS chain_amount,
        pt.pass_through_minutes AS total_pass_through_min,
        ARRAY_CONSTRUCT(pt.inbound_txn_id, pt.outbound_txn_id)
                            AS txn_chain
    FROM pass_through_accounts pt

    UNION ALL

    -- Recursive: follow next hop if it is also a pass-through
    SELECT
        rt.origin_party,
        rt.next_party       AS current_party,
        pt.next_party       AS next_party,
        rt.hop + 1          AS hop,
        ARRAY_APPEND(rt.path, pt.next_party)
                            AS path,
        rt.chain_amount,
        rt.total_pass_through_min + pt.pass_through_minutes
                            AS total_pass_through_min,
        ARRAY_CAT(rt.txn_chain, ARRAY_CONSTRUCT(pt.inbound_txn_id, pt.outbound_txn_id))
                            AS txn_chain
    FROM ring_traversal rt
    JOIN pass_through_accounts pt
      ON rt.next_party = pt.party_id
    CROSS JOIN params p
    WHERE rt.hop < p.max_hops
      -- Cycle guard: do not revisit a party already in the path
      AND NOT ARRAY_CONTAINS(pt.next_party::VARIANT, rt.path)
),

-- Step 4: Find rings — chains where money returns to origin OR fan-out detected
ring_candidates AS (
    SELECT
        rt.origin_party,
        rt.path,
        rt.hop                AS ring_hops,
        rt.chain_amount,
        rt.total_pass_through_min,
        rt.txn_chain,
        -- Flag: circular ring (money returns to origin)
        CASE WHEN rt.next_party = rt.origin_party THEN TRUE ELSE FALSE END
                              AS is_circular,
        -- Flag: fan-out (origin sends to 3+ distinct next-hops)
        COUNT(DISTINCT rt.next_party) OVER (PARTITION BY rt.origin_party)
                              AS distinct_endpoints
    FROM ring_traversal rt
    WHERE rt.hop >= 2   -- at least 2 hops to form a meaningful chain
),

-- Step 5: Corroborate with shared-device / shared-IP edges
corroborated_rings AS (
    SELECT
        rc.*,
        COUNT(DISTINCT pr.relationship_id) AS shared_attribute_count,
        ARRAY_AGG(DISTINCT pr.relationship_type) AS shared_attribute_types,
        ARRAY_AGG(DISTINCT pr.shared_attribute_value) AS shared_attribute_values
    FROM ring_candidates rc
    -- Explode the path array and look for shared attributes between any pair
    JOIN TABLE(FLATTEN(rc.path)) f_a
    JOIN TABLE(FLATTEN(rc.path)) f_b
      ON f_a.index < f_b.index   -- avoid self-join and duplicates
    LEFT JOIN RISK_COPILOT.CORE.PARTY_RELATIONSHIP pr
      ON pr.relationship_type IN ('SHARED_DEVICE', 'SHARED_IP', 'SHARED_PHONE', 'SHARED_EMAIL')
     AND pr.is_active = TRUE
     AND (
           (pr.party_id_a = f_a.value::VARCHAR AND pr.party_id_b = f_b.value::VARCHAR)
        OR (pr.party_id_a = f_b.value::VARCHAR AND pr.party_id_b = f_a.value::VARCHAR)
         )
    GROUP BY rc.origin_party, rc.path, rc.ring_hops, rc.chain_amount,
             rc.total_pass_through_min, rc.txn_chain, rc.is_circular, rc.distinct_endpoints
)

-- Final output: scored ring candidates
SELECT
    origin_party,
    path                    AS ring_members,
    ring_hops,
    chain_amount,
    total_pass_through_min,
    txn_chain,
    is_circular,
    distinct_endpoints,
    shared_attribute_count,
    shared_attribute_types,
    shared_attribute_values,
    -- Composite risk score
    ROUND(
        (CASE WHEN is_circular THEN 0.4 ELSE 0.2 END)              -- circular ring premium
      + (LEAST(shared_attribute_count, 5) * 0.1)                    -- shared attributes (capped)
      + (CASE WHEN distinct_endpoints >= 3 THEN 0.2 ELSE 0.0 END)  -- fan-out premium
      + (CASE WHEN total_pass_through_min < 5 THEN 0.1 ELSE 0.0 END) -- extreme velocity
    , 2) AS ring_risk_score
FROM corroborated_rings
WHERE shared_attribute_count > 0     -- require at least one shared attribute for corroboration
   OR is_circular                    -- or a circular fund flow (strong on its own)
ORDER BY ring_risk_score DESC, chain_amount DESC;
```

**How to read the output:**

| Column | Meaning |
|---|---|
| `ring_members` | Array of `party_id` values in the chain, in fund-flow order |
| `ring_hops` | Number of pass-through steps in the chain |
| `chain_amount` | Amount flowing through the first hop (proxy for ring volume) |
| `total_pass_through_min` | Cumulative minutes funds were held across all hops |
| `is_circular` | TRUE if funds return to the origin party |
| `shared_attribute_count` | Number of shared device/IP/phone edges between ring members |
| `ring_risk_score` | Composite score: higher = more suspicious (circular + shared attrs + fan-out + velocity) |

---

## C. Policy Document Ingestion & Change Management

### C.1 Ingestion Pipeline Schema

The pipeline converts unstructured policy PDFs into structured `POLICY_CLAUSE` rows through four stages.

```
                                 ┌─────────────────┐
                                 │  PDF/Circular    │
                                 │  dropped into    │
                                 │  @POLICY_STAGE   │
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                              1. │ AI_PARSE_DOCUMENT│  Layout-aware text extraction
                                 │ (OCR + structure)│
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                              2. │  AI_EXTRACT      │  Structured field extraction
                                 │  (clause-level)  │  → STAGING.POLICY_CLAUSE_RAW
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                              3. │  Diff & Impact   │  Compare against existing
                                 │  Analysis        │  POLICY_CLAUSE rows
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                              4. │  Human Review    │  Compliance officer reviews
                                 │  Queue           │  proposed changes
                                 └────────┬────────┘
                                          │
                                 ┌────────▼────────┐
                              5. │  Effective-dated │  Activated on the clause's
                                 │  Activation      │  effective_from date
                                 └─────────────────┘
```

#### Staging tables for intermediate outputs

```sql
-- Stage for incoming policy documents
CREATE OR REPLACE STAGE RISK_COPILOT.STAGING.POLICY_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Landing zone for policy PDFs and circulars';

-- Raw parsed text output from AI_PARSE_DOCUMENT
CREATE OR REPLACE TABLE RISK_COPILOT.STAGING.POLICY_PARSED_RAW (
    parse_id            VARCHAR(64)     NOT NULL,
    document_id         VARCHAR(64)     NOT NULL,
    stage_file_path     VARCHAR(2000)   NOT NULL,
    -- AI_PARSE_DOCUMENT output
    parsed_text         VARCHAR(16777216),
    parsed_layout       VARIANT,                -- page/section/paragraph structure
    parse_model         VARCHAR(100),
    parse_confidence    FLOAT,
    -- housekeeping
    parsed_at           TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_policy_parsed PRIMARY KEY (parse_id)
)
COMMENT = 'Raw output from AI_PARSE_DOCUMENT — intermediate staging';

-- Raw extracted clauses from AI_EXTRACT (before human review)
CREATE OR REPLACE TABLE RISK_COPILOT.STAGING.POLICY_CLAUSE_RAW (
    extraction_id       VARCHAR(64)     NOT NULL,
    document_id         VARCHAR(64)     NOT NULL,
    parse_id            VARCHAR(64)     NOT NULL,
    -- extracted fields (AI_EXTRACT output)
    clause_ref          VARCHAR(200),
    clause_title        VARCHAR(500),
    source_page         INTEGER,
    source_section      VARCHAR(200),
    source_text_excerpt VARCHAR(4000),
    domain              VARCHAR(30),
    subdomain           VARCHAR(50),
    metric              VARCHAR(200),
    operator            VARCHAR(10),
    threshold_value     VARIANT,
    threshold_currency  VARCHAR(3),
    action_required     VARCHAR(500),
    action_sla_hours    INTEGER,
    effective_from      DATE,
    applicability       VARIANT,
    -- extraction metadata
    extraction_confidence FLOAT,
    extraction_model    VARCHAR(100),
    -- diff results (populated in stage 3)
    diff_status         VARCHAR(20),            -- NEW | MODIFIED | UNCHANGED | SUPERSEDES
    existing_clause_id  VARCHAR(64),            -- clause it would supersede
    diff_detail         VARIANT,                -- what changed: {"field": "threshold_value", "old": 1000000, "new": 500000}
    -- review status
    review_status       VARCHAR(20)     NOT NULL DEFAULT 'PENDING_REVIEW',
    -- housekeeping
    extracted_at        TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_clause_raw PRIMARY KEY (extraction_id)
)
COMMENT = 'Raw AI_EXTRACT output — proposed clauses before human review';
```

#### Step 1: Parse the document

```sql
-- Triggered by stream on POLICY_STAGE directory
-- Creates a POLICY_DOCUMENT row and parses the PDF
INSERT INTO RISK_COPILOT.STAGING.POLICY_PARSED_RAW (parse_id, document_id, stage_file_path, parsed_text, parsed_layout, parse_model, parse_confidence)
SELECT
    UUID_STRING()                       AS parse_id,
    :document_id                        AS document_id,
    :stage_file_path                    AS stage_file_path,
    AI_PARSE_DOCUMENT(
        TO_VARCHAR(:stage_file_path),
        {'mode': 'LAYOUT'}              -- layout-aware extraction preserves section structure
    ):content::VARCHAR                  AS parsed_text,
    AI_PARSE_DOCUMENT(
        TO_VARCHAR(:stage_file_path),
        {'mode': 'LAYOUT'}
    )                                   AS parsed_layout,
    'ai_parse_document_v1'              AS parse_model,
    AI_PARSE_DOCUMENT(
        TO_VARCHAR(:stage_file_path),
        {'mode': 'LAYOUT'}
    ):confidence::FLOAT                 AS parse_confidence;
```

#### Step 2: Extract structured clauses

```sql
-- For each parsed document, extract structured clause fields
INSERT INTO RISK_COPILOT.STAGING.POLICY_CLAUSE_RAW
    (extraction_id, document_id, parse_id, clause_ref, clause_title,
     source_page, source_section, source_text_excerpt,
     domain, subdomain, metric, operator, threshold_value,
     threshold_currency, action_required, action_sla_hours,
     effective_from, applicability, extraction_confidence, extraction_model)
SELECT
    UUID_STRING()                                           AS extraction_id,
    pr.document_id,
    pr.parse_id,
    ex.value:clause_ref::VARCHAR                            AS clause_ref,
    ex.value:clause_title::VARCHAR                          AS clause_title,
    ex.value:source_page::INTEGER                           AS source_page,
    ex.value:source_section::VARCHAR                        AS source_section,
    ex.value:source_text_excerpt::VARCHAR                   AS source_text_excerpt,
    ex.value:domain::VARCHAR                                AS domain,
    ex.value:subdomain::VARCHAR                             AS subdomain,
    ex.value:metric::VARCHAR                                AS metric,
    ex.value:operator::VARCHAR                              AS operator,
    ex.value:threshold_value                                AS threshold_value,
    ex.value:threshold_currency::VARCHAR                    AS threshold_currency,
    ex.value:action_required::VARCHAR                       AS action_required,
    ex.value:action_sla_hours::INTEGER                      AS action_sla_hours,
    ex.value:effective_from::DATE                           AS effective_from,
    ex.value:applicability                                  AS applicability,
    ex.value:extraction_confidence::FLOAT                   AS extraction_confidence,
    'ai_extract_v1'                                         AS extraction_model
FROM RISK_COPILOT.STAGING.POLICY_PARSED_RAW pr,
    TABLE(FLATTEN(
        AI_EXTRACT(
            pr.parsed_text,
            {
                'question': 'Extract all regulatory clauses, thresholds, and rules from this document. For each clause return: clause_ref, clause_title, source_page, source_section, source_text_excerpt, domain (FRAUD/AML/CREDIT/LIQUIDITY/CAPITAL), subdomain, metric (what is measured), operator (GT/GTE/LT/LTE/EQ/BETWEEN), threshold_value, threshold_currency, action_required, action_sla_hours, effective_from date, applicability (entity types and conditions), and extraction_confidence.',
                'data_type': 'ARRAY',
                'element_type': 'OBJECT'
            }
        )
    )) ex
WHERE pr.parse_id = :parse_id;
```

#### Step 3: Diff against existing clauses

```sql
-- Identify which extracted clauses are new, modified, or unchanged
UPDATE RISK_COPILOT.STAGING.POLICY_CLAUSE_RAW raw
SET
    diff_status = CASE
        WHEN existing.clause_id IS NULL THEN 'NEW'
        WHEN existing.threshold_value != raw.threshold_value
          OR existing.operator != raw.operator
          OR existing.action_required != raw.action_required
        THEN 'MODIFIED'
        ELSE 'UNCHANGED'
    END,
    existing_clause_id = existing.clause_id,
    diff_detail = CASE
        WHEN existing.clause_id IS NOT NULL
         AND (existing.threshold_value != raw.threshold_value
           OR existing.operator != raw.operator
           OR existing.action_required != raw.action_required)
        THEN OBJECT_CONSTRUCT(
            'old_threshold', existing.threshold_value,
            'new_threshold', raw.threshold_value,
            'old_operator', existing.operator,
            'new_operator', raw.operator,
            'old_action', existing.action_required,
            'new_action', raw.action_required
        )
        ELSE NULL
    END
FROM RISK_COPILOT.POLICY.POLICY_CLAUSE existing
WHERE existing.is_current = TRUE
  AND existing.domain = raw.domain
  AND existing.subdomain = raw.subdomain
  AND existing.metric = raw.metric
  AND raw.document_id = :document_id;
```

### C.2 Regulatory Change Management Workflow

When a new circular updates or supersedes existing thresholds, the system follows this workflow:

**1. Impact analysis query** — identifies open findings that relied on the superseded clause:

```sql
-- Which open findings depend on a clause that is about to change?
SELECT
    f.finding_id,
    f.title,
    f.status,
    f.as_of_ts,
    pc.clause_id           AS affected_clause,
    pc.clause_ref,
    pc.metric,
    pc.threshold_value     AS current_threshold,
    raw.threshold_value    AS proposed_threshold,
    raw.diff_detail
FROM RISK_COPILOT.COMPLIANCE.FINDING f,
    TABLE(FLATTEN(f.cited_clause_ids)) cited
JOIN RISK_COPILOT.POLICY.POLICY_CLAUSE pc
  ON pc.clause_id = cited.value::VARCHAR
JOIN RISK_COPILOT.STAGING.POLICY_CLAUSE_RAW raw
  ON raw.existing_clause_id = pc.clause_id
 AND raw.diff_status = 'MODIFIED'
WHERE f.status IN ('DRAFT', 'UNDER_REVIEW', 'APPROVED')
  AND pc.is_current = TRUE
ORDER BY f.finding_id;
```

**2. Human review gate** — a compliance officer reviews the proposed changes, the impact report, and either approves or rejects each clause.

**3. Effective-dated activation** — upon approval, the system:

```sql
-- Supersede the old clause
UPDATE RISK_COPILOT.POLICY.POLICY_CLAUSE
SET
    is_current = FALSE,
    effective_to = :new_clause_effective_from - INTERVAL '1 day',
    superseded_by = :new_clause_id,
    updated_at = CURRENT_TIMESTAMP()
WHERE clause_id = :existing_clause_id;

-- Insert the new clause
INSERT INTO RISK_COPILOT.POLICY.POLICY_CLAUSE
    (clause_id, document_id, clause_ref, clause_title, source_page, source_section,
     source_text_excerpt, domain, subdomain, applicability, metric, operator,
     threshold_value, threshold_currency, action_required, action_sla_hours,
     effective_from, version, is_current, extraction_confidence, extraction_method,
     status, reviewed_by, reviewed_at, activated_by, activated_at)
VALUES
    (:new_clause_id, :document_id, :clause_ref, :clause_title, :source_page, :source_section,
     :source_text_excerpt, :domain, :subdomain, :applicability, :metric, :operator,
     :threshold_value, :threshold_currency, :action_required, :action_sla_hours,
     :effective_from, :new_version, TRUE, :extraction_confidence, 'HYBRID',
     'ACTIVE', :reviewer_user_id, CURRENT_TIMESTAMP(), :activator_user_id, CURRENT_TIMESTAMP());
```

**4. Governance event logging:**

```sql
INSERT INTO RISK_COPILOT.AUDIT.GOVERNANCE_EVENT
    (event_id, event_type, entity_type, entity_id, old_value, new_value, change_reason, actor_type, actor_id)
VALUES
    (UUID_STRING(), 'POLICY_CLAUSE_SUPERSEDED', 'POLICY_CLAUSE', :existing_clause_id,
     OBJECT_CONSTRUCT('threshold_value', :old_threshold),
     OBJECT_CONSTRUCT('threshold_value', :new_threshold),
     'Superseded by ' || :new_circular_reference,
     'HUMAN', :activator_user_id);
```

---

## D. Operational Lifecycle Walkthrough

> **Scenario:** A synthetic counterparty ("Party X") receives high-velocity pass-through
> transactions. Investigation reveals a 4-account mule ring with shared devices. The case
> escalates to AML, an STR is drafted, reviewed, and filed.

### Step 1: Transaction Ingestion (Fast Lane — Seconds)

Real-time transactions arrive via **Snowpipe Streaming** into `CORE.TRANSACTION`.

```
Party A ──₹5,00,000──▶ Party X (Account AX1)
    ... 4 minutes later ...
Party X (Account AX1) ──₹4,95,000──▶ Party B
Party X (Account AX1) ──₹4,500──▶ Party X (Account AX2)  [retained crumbs]
```

Two rows land in `CORE.TRANSACTION`:
- `txn_id: TXN001` — ₹5,00,000 from Party A → Party X, device `DEV_77`, IP `103.21.x.x`
- `txn_id: TXN002` — ₹4,95,000 from Party X → Party B, 4 minutes later, same device `DEV_77`

### Step 2: Signal Generation (Dynamic Table — Low Target Lag)

A velocity-monitoring dynamic table computes pass-through metrics and fires a **SIGNAL**:

```sql
-- Record in DETECTION.SIGNAL:
signal_id:        SIG001
rule_id:          VELOCITY_PASSTHROUGH_V3
typology:         MULE_RING
entity_id:        PARTY_X_ID
severity:         HIGH
confidence_score: 0.88
signal_detail: {
    "pass_through_minutes": 4,
    "inbound_amount": 500000,
    "outbound_amount": 495000,
    "retained_pct": 1.0,
    "device_id": "DEV_77"
}
policy_clause_id: CL_AML_017    -- "pass-through velocity < 10 min with > 95% forwarded"
```

### Step 3: Graph Traversal Reveals a 4-Account Mule Ring

The mule-ring detection query (Section B.3) runs and discovers:

```
                 Party A
                    │
                    ▼  ₹5,00,000
              ┌─ Party X ─┐
              │  (DEV_77)  │
              ▼            ▼
          Party B      Party C
          (DEV_77)     (DEV_77)      ← all 3 share device DEV_77
              │            │
              └──────┬─────┘
                     ▼
                 Party D              ← final aggregation point
```

The graph query returns:
- `ring_members: [PARTY_X, PARTY_B, PARTY_D]` and `[PARTY_X, PARTY_C, PARTY_D]`
- `shared_attribute_types: ['SHARED_DEVICE']`
- `ring_risk_score: 0.70`

A second signal fires:

```
signal_id:   SIG002
rule_id:     GRAPH_MULE_RING_V2
typology:    MULE_RING
severity:    CRITICAL
signal_detail: {
    "ring_members": ["PARTY_X", "PARTY_B", "PARTY_C", "PARTY_D"],
    "ring_hops": 3,
    "shared_device": "DEV_77",
    "total_flow": 1500000
}
```

### Step 4: Case Creation

Signals SIG001 and SIG002 are grouped into a **CASE**:

```
case_id:           CASE001
case_number:       AML-2026-00347
case_type:         AML
primary_party_id:  PARTY_X
related_party_ids: [PARTY_A, PARTY_B, PARTY_C, PARTY_D]
status:            INVESTIGATING
priority:          P1
total_signals:     2
max_severity:      CRITICAL
```

### Step 5: Rule Evaluation Against Policy Clauses

The system queries `POLICY_CLAUSE` for applicable AML thresholds:

```sql
SELECT clause_id, clause_ref, metric, operator, threshold_value, action_required
FROM RISK_COPILOT.POLICY.POLICY_CLAUSE
WHERE domain = 'AML'
  AND is_current = TRUE
  AND effective_from <= CURRENT_DATE()
  AND (effective_to IS NULL OR effective_to >= CURRENT_DATE());
```

Returns:
| clause_id | clause_ref | metric | operator | threshold_value | action_required |
|---|---|---|---|---|---|
| CL_AML_017 | PMLA Rule 3(1)(D) | pass_through_velocity_minutes | LT | 10 | FILE_STR |
| CL_AML_003 | PMLA Rule 3(1)(A) | single_cash_txn_amount | GTE | 1000000 | FILE_CTR |
| CL_AML_022 | Internal Policy 4.2.1 | shared_device_ring_members | GTE | 3 | ESCALATE_TO_MLRO |

Clause CL_AML_017 matches (pass-through < 10 min), and CL_AML_022 matches (ring with 4 members ≥ 3 threshold). Both require escalation and STR filing.

### Step 6: Evidence Collection (Pointers, Not Copies)

The evidence collector agent writes pointer rows to `DETECTION.EVIDENCE`:

| evidence_id | ref_type | ref_id | ref_table | as_of_ts | query_id | evidence_category |
|---|---|---|---|---|---|---|
| EV001 | TRANSACTION | TXN001 | RISK_COPILOT.CORE.TRANSACTION | 2026-09-19 14:30:00 | 01b7e... | FINANCIAL |
| EV002 | TRANSACTION | TXN002 | RISK_COPILOT.CORE.TRANSACTION | 2026-09-19 14:30:00 | 01b7e... | FINANCIAL |
| EV003 | SIGNAL | SIG001 | RISK_COPILOT.DETECTION.SIGNAL | 2026-09-19 14:30:00 | 01b7f... | BEHAVIORAL |
| EV004 | SIGNAL | SIG002 | RISK_COPILOT.DETECTION.SIGNAL | 2026-09-19 14:30:00 | 01b7f... | NETWORK |
| EV005 | PARTY_RELATIONSHIP | REL_DEV77 | RISK_COPILOT.CORE.PARTY_RELATIONSHIP | 2026-09-19 14:30:00 | 01b80... | NETWORK |
| EV006 | POLICY_CLAUSE | CL_AML_017 | RISK_COPILOT.POLICY.POLICY_CLAUSE | 2026-09-19 14:30:00 | 01b81... | POLICY |

**Re-verification proof:** An auditor can execute:
```sql
SELECT * FROM RISK_COPILOT.CORE.TRANSACTION
  AT(TIMESTAMP => '2026-09-19 14:30:00'::TIMESTAMP_NTZ)
WHERE txn_id = 'TXN001';
```
and reproduce the exact data that supported the finding.

### Step 7: STR Narrative Drafting (Artifact)

The narrative drafter agent creates an STR draft in `COMPLIANCE.ARTIFACT`:

```
artifact_id:   ART001
finding_id:    FND001
artifact_type: STR_DRAFT
content_text:  "Suspicious Transaction Report — Party X...
               Between 2026-09-15 and 2026-09-19, Account AX1 held by Party X
               received ₹15,00,000 across 6 transactions from 3 distinct parties
               (A, B, C). In each instance, >95% of funds were forwarded within
               10 minutes... [cites EV001-EV006, clauses CL_AML_017, CL_AML_022]"
reconciliation_status: PENDING
```

### Step 8: Adversarial Review (Numeric Reconciliation Gate)

The adversarial reviewer agent parses every numeral from the narrative and checks against evidence:

```
Checking narrative assertion: "₹15,00,000 across 6 transactions"
  → Query evidence TXN pointers AT(TIMESTAMP => as_of_ts)
  → SUM(amount) = 1500000 ✓, COUNT(*) = 6 ✓

Checking narrative assertion: ">95% of funds were forwarded"
  → Computed retained_pct from evidence: [1.0%, 0.8%, 1.2%] — all < 5% ✓

Checking narrative assertion: "within 10 minutes"
  → pass_through_minutes from signal_detail: [4, 3, 7] — all < 10 ✓

Checking policy citation: "CL_AML_017 is in effect on 2026-09-19"
  → effective_from: 2024-01-01, effective_to: NULL ✓

RESULT: ALL CHECKS PASSED
```

The artifact is updated: `reconciliation_status: PASSED`

### Step 9: Human Attestation & Filing

A compliance officer (MLRO) reviews the artifact and signs off:

```sql
-- Finding status: DRAFT → UNDER_REVIEW → APPROVED
-- (Only MLRO/COMPLIANCE_OFFICER role can approve)

-- Attestation record
INSERT INTO RISK_COPILOT.AUDIT.ATTESTATION
    (attestation_id, finding_id, artifact_id,
     attestor_user_id, attestor_name, attestor_role,
     action, comment, signature_hash, attested_at)
VALUES
    ('ATT001', 'FND001', 'ART001',
     'USER_MLRO_01', 'Priya Sharma', 'MLRO',
     'APPROVE', 'Reviewed evidence bundle. STR narrative is accurate and complete. Recommending filing with FIU-IND.',
     SHA2('FND001||USER_MLRO_01||APPROVE||2026-09-19T16:00:00'),
     '2026-09-19 16:00:00'::TIMESTAMP_NTZ);

-- After filing:
-- Finding status → FILED
-- finding.filed_by = 'USER_MLRO_01'
-- finding.filing_reference = 'FIU-IND-STR-2026-09-XXXXX'
```

The agent **cannot** transition the finding to FILED — only a human attester can. This segregation is enforced by the schema design and the workflow engine.

---

## E. Trade-offs & Limitations

### E.1 Technology Comparison

| Criterion | Relational Graph (Snowflake CTEs) | Dedicated Graph DB (Neo4j) | Vector DB (RAG) |
|---|---|---|---|
| **Traversal depth** | Practical limit ~5 hops (CTE materialization cost grows) | Native traversal, 10+ hops efficient | N/A — not a graph tool |
| **Graph algorithms** | Must implement manually (PageRank, community detection = complex SQL) | 60+ native algorithms (GDS library) | N/A |
| **Time Travel / as-of** | Native — `AT(TIMESTAMP =>)` reproduces exact state | Not native; requires snapshot architecture | Not native |
| **Auditability** | Full: query_id + as_of_ts = re-verifiable proof | Requires external audit logging | Non-deterministic — same query may return different results |
| **Regulatory figures** | Deterministic SQL — identical query = identical number | Deterministic but requires ETL sync | **Fatal flaw**: probabilistic retrieval cannot guarantee correct regulatory numbers |
| **Platform consolidation** | Single platform: compute, storage, governance, Time Travel | Separate system: ETL sync, separate auth, separate governance | Separate system with same issues |
| **Governance (masking, RBAC)** | Native policies, row-level security, tags | Must implement separately | Must implement separately |
| **Real-time ingestion** | Snowpipe Streaming, streams, low-lag dynamic tables | Kafka connector, custom ETL | Embedding pipeline required |
| **Maintenance** | Zero — managed service | Cluster ops, backup, scaling | Cluster ops, index management |
| **Cost model** | Pay-per-query, no idle cost | Always-on cluster | Always-on cluster |

**Verdict for this system:**

- **Relational Graph in Snowflake** is the right choice for this use case. The 3–5 hop depth limit is sufficient for mule-ring and related-party detection (real-world fraud rings rarely exceed 5 hops before consolidation). The decisive advantage is that graph evidence, Time Travel, policy evaluation, and regulatory computation all live on one governed platform — no ETL sync, no governance gaps, no "the graph DB says X but Snowflake says Y" discrepancies.

- **Neo4j** would be the right addition only if the system needed deep graph analytics (community detection across millions of nodes, shortest-path across 10+ hops). For this use case, the governance cost of maintaining a second platform outweighs the traversal benefit.

- **Vector DB / RAG** is the wrong tool for this problem. The system's core requirement is deterministic, re-verifiable evidence chains. RAG returns probabilistic results that cannot be re-verified (a different embedding or chunk boundary produces different context). It has a legitimate role in one narrow area: semantic search over unstructured policy text during the initial extraction phase. But it must never be the source of truth for numbers, thresholds, or evidence.

### E.2 Failure Modes & Mitigations

| Failure Mode | Impact | Mitigation |
|---|---|---|
| **High-volume edge creation lag** | `PARTY_RELATIONSHIP` falls behind real-time transactions → graph queries miss recent links | Micro-batch edge creation via stream + dynamic table with 1-minute target lag. Edge creation is a derived computation, not inline with transaction ingestion. |
| **Schema drift in policy extraction** | AI_EXTRACT produces inconsistent field mappings across different circular formats | Validation layer: check extracted clauses against expected schema before inserting into `POLICY_CLAUSE_RAW`. Reject and flag for manual extraction if required fields are missing or confidence < 0.6. |
| **Hard negatives not planted** | Precision metrics are artificially inflated — every flagged entity is actually suspicious | Plant documented hard negatives in `GROUND_TRUTH` with `expected_detection = FALSE`: legitimate high-cash businesses, frequent travelers, seasonal DPD borrowers. Measure false positive rate explicitly. |
| **Time Travel retention expiry** | Evidence pointers become unverifiable after `DATA_RETENTION_TIME_IN_DAYS` expires | For filed findings: at filing time, snapshot the evidence data into a long-term archive table (`AUDIT.EVIDENCE_ARCHIVE`) as a fallback. The pointer is still the primary proof; the snapshot is the insurance policy. |
| **CTE materialization cost on large graphs** | Recursive CTE over millions of edges may be slow or expensive | Bound depth (max 3–5 hops). Pre-filter edges to the lookback window. Cluster `PARTY_RELATIONSHIP` on `(relationship_type, party_id_a, party_id_b)`. Consider materializing a daily "active edges" dynamic table. |
| **LLM hallucinating numbers in narrative** | STR contains a figure that does not appear in the evidence → regulatory credibility destroyed | The adversarial reviewer agent parses every numeral from the narrative and asserts it traces to a query result. If any number fails, the artifact is rejected and regenerated. This is a hard gate, not a warning. |
| **Maker-checker bypass** | An agent transitions a finding to FILED without human attestation → compliance incident | Schema constraint: the `filed_by` field on `FINDING` must reference an `ATTESTATION` row with `action = 'APPROVE'` and `attestor_role IN ('MLRO', 'COMPLIANCE_OFFICER')`. The workflow engine enforces this; the agent cannot write to `ATTESTATION`. |
| **Policy clause activated without review** | An AI-extracted threshold goes live without human validation → incorrect rule fires → false alerts or missed true positives | `POLICY_CLAUSE.status` starts as `PROPOSED`. Only a human can transition to `ACTIVE`. The system polls for clauses where `status = 'ACTIVE' AND effective_from <= CURRENT_DATE()` — a proposed clause is invisible to detection rules. |
| **Single point of failure in evidence chain** | If `EVIDENCE` table is corrupted or dropped, the entire audit trail breaks | `DATA_RETENTION_TIME_IN_DAYS = 90` provides Time Travel recovery. Complement with fail-safe storage and periodic backup to an external stage. `EVIDENCE` is append-only with no UPDATE/DELETE permissions granted to any operational role. |

---

## G. AMLSim — Synthetic Data Generation & Schema Alignment

> **Source:** IBM AMLSim — `C:\Users\2010j\Desktop\Snowflake_hack\AMLSim`
> GitHub: https://github.com/IBM/AMLSim

### G.1 Why AMLSim (And Why Not GANs or SMOTE)

AMLSim is an **agent-based transaction simulator** purpose-built for AML research. It is the right choice for this project for reasons that directly connect to compliance reality:

**The fundamental problem:** We need synthetic data with **known ground-truth labels** so we can measure whether our detection logic actually works (precision, recall, confusion matrix). Without labels, "the system detects fraud" is an untestable claim.

| Approach | Can generate graph topologies (mule rings)? | Produces ground-truth labels? | Maintains referential integrity (balance = sum of txns)? | Needs real training data we don't have? |
|---|---|---|---|---|
| **SMOTE** | No — interpolates feature vectors, no concept of a graph | Partial — inherited from minority class | No | Yes |
| **Tabular GAN (CTGAN)** | No — learns distributions, cannot design topology | No — mode-collapses on rare events (fraud IS the rare event) | No | Yes (and we have none) |
| **AMLSim** | **Yes — typologies are designed graph structures** | **Yes — every SAR account and transaction is labeled by construction** | **Yes — double-entry balance tracking** | **No — parameters calibrated from public statistics** |

**What AMLSim gives us that no generative model can:**

1. **Planted typologies with exact labels.** AMLSim creates specific instances of fan-in, fan-out, cycle (mule ring), scatter-gather, etc. Each is tagged with `is_sar = True` and an `alert_id`. This is our `GROUND_TRUTH` table — the basis for every detection metric.

2. **Realistic graph structure.** AMLSim builds a scale-free transaction network using degree distributions. The typologies are embedded within this network, which means detection must find the signal in noise — just like production.

3. **Hard negatives built in.** AMLSim generates normal transaction patterns (single, fan-out, fan-in, forward, mutual, periodical) that structurally resemble AML typologies but are legitimate. A normal `fan_in` with `is_sar = False` is a hard negative that tests our false positive rate.

4. **Temporal narrative.** Transactions happen over simulated days with configurable scheduling. Patterns develop over time — exactly what our velocity features and pass-through detection need to handle.

### G.2 AMLSim Output-to-Schema Column Mapping

AMLSim produces 10 output CSV files. Here is exactly how each maps to our Snowflake schema:

#### accounts.csv → CORE.PARTY + CORE.ACCOUNT

AMLSim combines party and account into one file. We split them:

| AMLSim Column | Target Table | Target Column | Transformation |
|---|---|---|---|
| `acct_id` | `CORE.ACCOUNT` | `account_id` | Prefix: `'ACCT_' \|\| acct_id` |
| `acct_id` | `CORE.ACCOUNT` | `account_number` | Direct |
| `dsply_nm` | `CORE.PARTY` | `full_name` | Direct |
| `type` | `CORE.ACCOUNT` | `account_type` | Map: SAV→SAVINGS, CHK→CURRENT |
| `acct_stat` | `CORE.ACCOUNT` | `status` | Map: A→ACTIVE, I→DORMANT, C→CLOSED |
| `acct_rptng_crncy` | `CORE.ACCOUNT` | `currency` | Direct (default: USD → configurable to INR) |
| `branch_id` | `CORE.ACCOUNT` | `branch_code` | Direct |
| `open_dt` | `CORE.ACCOUNT` | `opening_date` | Parse date |
| `close_dt` | `CORE.ACCOUNT` | `closing_date` | Parse date (may be empty) |
| `initial_deposit` | `CORE.ACCOUNT` | `current_balance` | Initial value (updated by transaction replay) |
| `bank_id` | `CORE.ACCOUNT` | _(metadata)_ | Used for multi-bank scenarios |
| `first_name` | `CORE.PARTY` | `full_name` | Concatenate: `first_name \|\| ' ' \|\| last_name` |
| `last_name` | `CORE.PARTY` | `full_name` | (see above) |
| `country` | `CORE.PARTY` | `jurisdiction` | Map to ISO 3166-1 alpha-3 |
| `gender` | `CORE.PARTY` | _(stored in party detail)_ | M→MALE, F→FEMALE |
| `birth_date` | `CORE.PARTY` | `date_of_birth` | Parse date |
| `ssn` | `CORE.PARTY` | `tax_id` | Direct |
| `street_addr`, `city`, `state`, `zip` | _(address fields)_ | _(stored as structured address)_ | For shared-address entity resolution |
| `lon`, `lat` | _(geo fields)_ | _(stored for geo analysis)_ | Party home location |
| `prior_sar_count` | `CORE.PARTY` | `risk_rating` | If prior_sar > 0 → HIGH; else compute from other factors |
| `tx_behavior_id` | `DETECTION.SIGNAL` | _(metadata)_ | Links to which normal/AML model governs behavior |

**Party ID generation:** AMLSim doesn't have a separate party ID. We generate `party_id = 'P_' || acct_id` for the 1:1 account-to-party case. For shared ownership, use `accountMapping.csv`.

#### transactions.csv → CORE.TRANSACTION

| AMLSim Column | Target Column | Transformation |
|---|---|---|
| `tran_id` | `txn_id` | Prefix: `'TXN_' \|\| tran_id` |
| `tran_timestamp` | `txn_timestamp` | AMLSim outputs ISO 8601 dates (from `base_date` + simulation step) |
| `tran_timestamp` | `txn_date` | Extract DATE from timestamp |
| `base_amt` | `amount` | Parse to NUMBER(18,2) |
| `tx_type` | `channel` | Map: TRANSFER→NETBANKING, WIRE→NEFT, CASH-IN→BRANCH, CASH-OUT→BRANCH |
| `tx_type` | `payment_rail` | Map: TRANSFER→INTERNAL, WIRE→NEFT |
| `orig_acct` | `debit_account_id` | Prefix: `'ACCT_' \|\| orig_acct` |
| `orig_acct` | `debit_party_id` | Lookup from account-to-party mapping |
| `bene_acct` | `credit_account_id` | Prefix: `'ACCT_' \|\| bene_acct` |
| `bene_acct` | `credit_party_id` | Lookup from account-to-party mapping |
| `is_sar` | _(ground truth)_ | Stored in `AUDIT.GROUND_TRUTH`, NOT in the transaction table (production systems don't have this column) |
| `alert_id` | _(ground truth)_ | Links to `AUDIT.GROUND_TRUTH.ring_id` |

**Enrichment during load** (not from AMLSim — we generate these):
- `device_id`: Assign synthetic device IDs. For accounts in the same `alert_id` (same ring), assign **shared** device IDs to create `SHARED_DEVICE` edges in `PARTY_RELATIONSHIP`.
- `ip_address`: Same strategy — shared IPs within rings.
- `geo_lat`/`geo_lon`: Derived from the party's address (from `accounts.csv` lat/lon fields).
- `is_cash`: TRUE when `tx_type` is CASH-IN or CASH-OUT.
- `is_cross_border`: TRUE when `orig_acct` and `bene_acct` have different `country` values.

#### alert_transactions.csv → DETECTION.SIGNAL + DETECTION.EVIDENCE

This is AMLSim's most valuable output for our detection pipeline:

| AMLSim Column | Target Table | Target Column | Notes |
|---|---|---|---|
| `alert_id` | `DETECTION.SIGNAL` | _(groups into one signal per alert)_ | One AMLSim alert = one `SIGNAL` row |
| `alert_type` | `DETECTION.SIGNAL` | `typology` | Map: fan_in→FAN_IN, cycle→MULE_RING, scatter_gather→SCATTER_GATHER, etc. |
| `is_sar` | `DETECTION.SIGNAL` | `severity` | TRUE→HIGH/CRITICAL, FALSE→MEDIUM (false positive alert) |
| `tran_id` | `DETECTION.SIGNAL` | `related_txn_ids` | Aggregate all tran_ids per alert_id into ARRAY |
| `orig_acct`, `bene_acct` | `DETECTION.EVIDENCE` | `ref_id` | Each transaction in the alert becomes an evidence pointer |
| `base_amt` | `DETECTION.SIGNAL` | `signal_detail` | Aggregate: total_amount, txn_count, etc. |

#### alert_accounts.csv → DETECTION.CASE + CORE.PARTY_RELATIONSHIP

| AMLSim Column | Target Table | Target Column | Notes |
|---|---|---|---|
| `alert_id` | `DETECTION.CASE` | `case_id` | One alert = one case (for simulation; production may merge) |
| `alert_type` | `DETECTION.CASE` | `case_type` | Map to: FRAUD, AML |
| `acct_id` | `DETECTION.CASE` | `related_account_ids` | Aggregate all accounts in the alert |
| `is_sar` | `DETECTION.CASE` | `status` | TRUE→INVESTIGATING, FALSE→CLOSED_FALSE_POSITIVE |
| `model_id` | _(metadata)_ | | Typology model identifier |
| `start`, `end` | `DETECTION.CASE` | `opened_at`, `sla_due_at` | Alert period timestamps |

**Ring-to-relationship mapping:** For each `alert_id`, all member accounts are connected. We create `PARTY_RELATIONSHIP` edges:
- `FUND_FLOW` edges: from the transaction data (orig→bene)
- `SHARED_DEVICE` edges: when we assign shared device_ids to ring members during enrichment
- `SHARED_IP` edges: same strategy with IP addresses

#### individuals-bulkload.csv / organizations-bulkload.csv → CORE.PARTY (enrichment)

These provide detailed party attributes (30 columns including nationality, phone, email, etc.) that map directly to `CORE.PARTY` fields. Key fields:

| AMLSim Column | Target Column |
|---|---|
| `partyId` | `party_id` |
| `partyType` | `party_type` (INDIVIDUAL / CORPORATE) |
| `firstName`, `lastName` | `full_name` |
| `nationality` | `nationality` |
| `countryofResidency` | `jurisdiction` |
| `phone_number` | _(for SHARED_PHONE entity resolution)_ |
| `email` | _(for SHARED_EMAIL entity resolution)_ |

#### accountMapping.csv → CORE.ACCOUNT (FK to PARTY)

| AMLSim Column | Target Column |
|---|---|
| `acct_id` | `CORE.ACCOUNT.account_id` |
| `cust_id` | `CORE.ACCOUNT.party_id` (FK) |
| `cust_acct_role` | _(metadata: Primary, Joint, PoA)_ |

#### resolvedentities.csv → CORE.PARTY (entity resolution)

| AMLSim Column | Target Column |
|---|---|
| `partyid1`, `partyid2` | Two `CORE.PARTY` rows → set same `match_cluster_id` |
| `score` | `CORE.PARTY.match_confidence` (normalize: 180→1.0) |
| `reason` | _(metadata for entity resolution audit)_ |

#### sar_accounts.csv → AUDIT.GROUND_TRUTH

This is the **ground truth table** — directly:

| AMLSim Column | Target Column |
|---|---|
| `ALERT_ID` | `ground_truth_id` / `ring_id` |
| `ACCOUNT_ID` | `entity_id` |
| `ALERT_TYPE` | `typology` |
| `IS_SAR` | `expected_detection` (YES→TRUE, NO→FALSE) |
| `EVENT_DATE` | `planted_at` |
| `ACCOUNT_TYPE` | `entity_type` (INDIVIDUAL→PARTY, COMPANY→PARTY) |

### G.3 Typology-to-Detection Alignment

AMLSim's 8 typologies map directly to the detection scenarios our schema and graph queries are designed to catch:

| AMLSim Typology | Graph Shape | Our Detection Method | Schema Tables Involved | Real-World Pattern |
|---|---|---|---|---|
| **fan_in** | N accounts → 1 hub | Velocity signal + `SIGNAL.typology = 'SMURFING'` | TRANSACTION, SIGNAL | **Structuring/smurfing**: Many small deposits from different sources into one account to avoid CTR threshold. A hawala operator collecting from multiple walk-in customers. |
| **fan_out** | 1 hub → N accounts | Fan-out degree anomaly | TRANSACTION, SIGNAL | **Layering**: One account distributes to many recipients. Shell company dispersing received funds to dilute the trail. |
| **cycle** | A→B→C→...→A | **Bounded recursive CTE (Section B.3)** | TRANSACTION, PARTY_RELATIONSHIP, SIGNAL | **Mule ring / round-tripping**: Money circles through a ring of accounts and returns to origin. Classic money laundering pattern. Also used for inflating turnover in loan fraud. |
| **bipartite** | N originators ↔ M beneficiaries | Community detection on PARTY_RELATIONSHIP | TRANSACTION, PARTY_RELATIONSHIP | **Shell company network**: A cluster of originators all transact with a cluster of beneficiaries, suggesting coordinated activity across nominally unrelated entities. |
| **stack** | Layers: originators → intermediaries → beneficiaries | Multi-hop CTE traversal | TRANSACTION, PARTY_RELATIONSHIP | **Multi-layered laundering**: Adds intermediary layers to increase distance between illicit source and final destination. The "professional" version of a mule ring. |
| **random** | Random edges among N members | Anomalous clustering + shared attributes | TRANSACTION, PARTY_RELATIONSHIP | **Unstructured suspicious network**: Group of accounts with unusual mutual transaction patterns that don't fit a clean typology but share attributes (device, IP, address). |
| **scatter_gather** | 1 orig → N intermediaries → 1 bene | Two-phase CTE: fan-out then fan-in | TRANSACTION, PARTY_RELATIONSHIP | **Structuring with reassembly**: One party breaks funds into small amounts across intermediaries, who then consolidate to a single beneficiary. Evades single-transaction thresholds while preserving the total flow. |
| **gather_scatter** | N originators → 1 intermediary → M bene | Two-phase CTE: fan-in then fan-out | TRANSACTION, PARTY_RELATIONSHIP | **Collection then distribution**: Multiple sources pay into a central account (e.g., a remittance agent), which then distributes outward. Common in trade-based money laundering. |

**Why this alignment matters for the demo:**

Each AMLSim typology instance is a planted ground-truth scenario. When our detection logic runs:
- **True positives**: our CTE/signals correctly flag a planted `is_sar = True` ring → counts toward recall.
- **False negatives**: a planted ring is missed → recall gap, tells us where detection logic needs tuning.
- **True negatives**: a normal `fan_in` pattern (AMLSim's normal models with `is_sar = False`) is correctly ignored → specificity.
- **False positives**: a normal pattern is incorrectly flagged → precision gap, tells us thresholds are too sensitive.

This gives us a **confusion matrix** — the single most credible artifact for demonstrating detection quality.

### G.4 AMLSim Configuration for This Project

AMLSim is configured via `conf.json` + parameter CSVs. Here is the recommended configuration for our demo:

#### conf.json — Key Parameters

```json
{
  "general": {
    "random_seed": 42,
    "simulation_name": "risk_copilot_demo",
    "total_steps": 180,
    "base_date": "2026-01-01"
  },
  "default": {
    "min_amount": 100,
    "max_amount": 5000000,
    "min_balance": 10000,
    "max_balance": 50000000,
    "transaction_model": 1,
    "margin_ratio": 0.10,
    "cash_in": {
      "base_amount": {"mean": 50000, "std": 20000},
      "frequency": {"mean": 0.2, "std": 0.1}
    },
    "cash_out": {
      "base_amount": {"mean": 30000, "std": 15000},
      "frequency": {"mean": 0.15, "std": 0.08}
    }
  },
  "input": {
    "directory": "paramFiles/risk_copilot/",
    "schema": "schema.json",
    "accounts": "accounts.csv",
    "alert_patterns": "alertPatterns.csv",
    "degree": "degree.csv",
    "transaction_type": "transactionType.csv",
    "normal_models": "normalModels.csv",
    "is_aggregated_accounts": true
  },
  "output": {
    "directory": "outputs/risk_copilot/",
    "transaction_log": "transactions.csv",
    "cash_log": "cash_tx.csv",
    "alert_transactions": "alert_transactions.csv",
    "alert_members": "alert_accounts.csv",
    "sar_accounts": "sar_accounts.csv",
    "party_individuals": "individuals-bulkload.csv",
    "party_organizations": "organizations-bulkload.csv",
    "account_mapping": "accountMapping.csv",
    "resolved_entities": "resolvedentities.csv",
    "counter_log": "tx_count.csv",
    "diameter_log": "diameter.csv"
  },
  "simulator": {
    "transaction_interval": 7,
    "sar_interval": 28,
    "numBranches": 10
  }
}
```

#### accounts.csv — Account Population

```csv
count,min_balance,max_balance,country,business_type,model,bank_id
3000,10000,5000000,IN,I,1,bank_main
500,50000,50000000,IN,O,1,bank_main
200,10000,1000000,US,I,1,bank_intl
100,100000,10000000,AE,O,1,bank_intl
```

- 3000 individual accounts (India) + 500 corporate accounts (India) = realistic NBFC-scale
- 200 US + 100 UAE accounts for cross-border scenarios
- Business type: I=Individual, O=Organization

#### alertPatterns.csv — Planted Typologies

```csv
count,type,schedule_id,min_accounts,max_accounts,min_amount,max_amount,min_period,max_period,bank_id,is_sar
5,fan_in,2,4,8,50000,500000,5,15,bank_main,true
5,fan_out,2,4,8,50000,500000,5,15,bank_main,true
3,cycle,2,3,6,100000,2000000,3,10,bank_main,true
2,bipartite,2,4,10,50000,1000000,5,20,bank_main,true
2,stack,2,5,12,100000,5000000,5,20,bank_main,true
2,scatter_gather,2,4,8,200000,2000000,5,15,bank_main,true
2,gather_scatter,2,4,8,200000,2000000,5,15,bank_main,true
3,fan_in,2,3,6,40000,400000,5,15,bank_main,false
3,fan_out,2,3,6,40000,400000,5,15,bank_main,false
2,cycle,2,3,5,80000,800000,3,10,bank_main,false
```

Key design choices:
- **21 true SAR patterns** across all 7 typologies (excluding random) — realistic base rate (~0.5% of accounts)
- **8 false-positive patterns** (`is_sar = false`) — hard negatives that look like typologies but are legitimate
- Amounts calibrated to Indian transaction volumes (₹50K–₹50L range)
- Periods of 3–20 days — enough temporal spread for velocity detection

#### normalModels.csv — Legitimate Behavior Baseline

```csv
count,type,schedule_id,min_accounts,max_accounts,min_period,max_period,bank_id
1500,single,2,1,1,5,30,bank_main
500,fan_out,2,3,10,5,30,bank_main
500,fan_in,2,3,10,5,30,bank_main
300,forward,2,2,4,5,20,bank_main
200,mutual,2,2,2,5,30,bank_main
300,periodical,2,2,2,5,30,bank_main
```

This creates a realistic baseline of normal activity that the AML typologies are embedded within.

#### transactionType.csv — Transaction Mix

```csv
Type,Frequency
TRANSFER,40
WIRE,15
CASH-IN,20
CASH-OUT,15
CHECK,5
CREDIT,5
```

Weighted to reflect an Indian NBFC's typical channel mix (UPI/NEFT dominant, cash significant).

### G.5 Ingestion Pipeline — AMLSim Outputs to Snowflake

#### Step 1: Run AMLSim

```bash
cd C:\Users\2010j\Desktop\Snowflake_hack\AMLSim

# Build (once)
scripts/build_AMLSim.sh     # or: mvn clean package -DskipTests

# Step 1: Generate the transaction graph from parameters
python scripts/transaction_graph_generator.py paramFiles/risk_copilot/conf.json

# Step 2: Run the Java simulation (MASON agent-based)
scripts/run_AMLSim.sh paramFiles/risk_copilot/conf.json

# Step 3: Convert raw logs to final output CSVs
python scripts/convert_logs.py paramFiles/risk_copilot/conf.json
```

Output lands in `outputs/risk_copilot/`.

#### Step 2: Upload to Snowflake Stage

```sql
CREATE OR REPLACE STAGE RISK_COPILOT.STAGING.AMLSIM_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'AMLSim output files for ingestion';

-- Upload from local (via SnowSQL or PUT)
PUT file://C:\Users\2010j\Desktop\Snowflake_hack\AMLSim\outputs\risk_copilot\*.csv
    @RISK_COPILOT.STAGING.AMLSIM_STAGE/
    AUTO_COMPRESS = TRUE;
```

#### Step 3: Load into Schema Tables

```sql
-- =============================================
-- LOAD PARTIES (from accounts.csv + individuals-bulkload.csv)
-- =============================================
COPY INTO RISK_COPILOT.STAGING.AMLSIM_ACCOUNTS_RAW
FROM @RISK_COPILOT.STAGING.AMLSIM_STAGE/accounts.csv
FILE_FORMAT = (TYPE = CSV, SKIP_HEADER = 1, FIELD_OPTIONALLY_ENCLOSED_BY = '"');

-- Transform and insert into CORE.PARTY
INSERT INTO RISK_COPILOT.CORE.PARTY
    (party_id, party_type, full_name, date_of_birth, nationality, jurisdiction,
     tax_id, kyc_status, cdd_level, risk_rating, source_system, source_entity_id, is_active)
SELECT
    'P_' || acct_id                                     AS party_id,
    CASE WHEN business_type = 'I' THEN 'INDIVIDUAL' ELSE 'CORPORATE' END AS party_type,
    first_name || ' ' || last_name                      AS full_name,
    TRY_TO_DATE(birth_date)                             AS date_of_birth,
    country                                             AS nationality,
    country                                             AS jurisdiction,
    ssn                                                 AS tax_id,
    'VERIFIED'                                          AS kyc_status,
    'CDD'                                               AS cdd_level,
    CASE
        WHEN prior_sar_count > 0 THEN 'HIGH'
        WHEN country NOT IN ('IN', 'US', 'GB') THEN 'MEDIUM'
        ELSE 'LOW'
    END                                                 AS risk_rating,
    'AMLSIM'                                            AS source_system,
    acct_id::VARCHAR                                    AS source_entity_id,
    TRUE                                                AS is_active
FROM RISK_COPILOT.STAGING.AMLSIM_ACCOUNTS_RAW;

-- =============================================
-- LOAD ACCOUNTS
-- =============================================
INSERT INTO RISK_COPILOT.CORE.ACCOUNT
    (account_id, party_id, account_number, account_type, currency,
     opening_date, closing_date, status, current_balance, branch_code)
SELECT
    'ACCT_' || acct_id                                  AS account_id,
    'P_' || acct_id                                     AS party_id,
    acct_id::VARCHAR                                    AS account_number,
    CASE WHEN type = 'SAV' THEN 'SAVINGS' ELSE 'CURRENT' END AS account_type,
    COALESCE(acct_rptng_crncy, 'INR')                  AS currency,
    TRY_TO_DATE(open_dt)                                AS opening_date,
    TRY_TO_DATE(NULLIF(close_dt, ''))                   AS closing_date,
    CASE WHEN acct_stat = 'A' THEN 'ACTIVE' ELSE 'CLOSED' END AS status,
    initial_deposit                                     AS current_balance,
    branch_id::VARCHAR                                  AS branch_code
FROM RISK_COPILOT.STAGING.AMLSIM_ACCOUNTS_RAW;

-- =============================================
-- LOAD TRANSACTIONS
-- =============================================
COPY INTO RISK_COPILOT.STAGING.AMLSIM_TRANSACTIONS_RAW
FROM @RISK_COPILOT.STAGING.AMLSIM_STAGE/transactions.csv
FILE_FORMAT = (TYPE = CSV, SKIP_HEADER = 1, FIELD_OPTIONALLY_ENCLOSED_BY = '"');

INSERT INTO RISK_COPILOT.CORE.TRANSACTION
    (txn_id, txn_timestamp, txn_date, debit_account_id, debit_party_id,
     credit_account_id, credit_party_id, amount, currency, channel,
     payment_rail, txn_type, is_cash, is_cross_border)
SELECT
    'TXN_' || tran_id                                   AS txn_id,
    TRY_TO_TIMESTAMP(tran_timestamp)                    AS txn_timestamp,
    TRY_TO_DATE(tran_timestamp)                         AS txn_date,
    'ACCT_' || orig_acct                                AS debit_account_id,
    'P_' || orig_acct                                   AS debit_party_id,
    'ACCT_' || bene_acct                                AS credit_account_id,
    'P_' || bene_acct                                   AS credit_party_id,
    TRY_TO_NUMBER(base_amt, 18, 2)                      AS amount,
    'INR'                                               AS currency,
    CASE tx_type
        WHEN 'TRANSFER' THEN 'NETBANKING'
        WHEN 'WIRE' THEN 'NEFT'
        WHEN 'CASH-IN' THEN 'BRANCH'
        WHEN 'CASH-OUT' THEN 'BRANCH'
        WHEN 'CHECK' THEN 'BRANCH'
        ELSE 'NETBANKING'
    END                                                 AS channel,
    CASE tx_type
        WHEN 'WIRE' THEN 'NEFT'
        WHEN 'TRANSFER' THEN 'INTERNAL'
        ELSE NULL
    END                                                 AS payment_rail,
    CASE tx_type
        WHEN 'CASH-IN' THEN 'CASH_DEPOSIT'
        WHEN 'CASH-OUT' THEN 'CASH_WITHDRAWAL'
        ELSE 'TRANSFER'
    END                                                 AS txn_type,
    tx_type IN ('CASH-IN', 'CASH-OUT')                  AS is_cash,
    FALSE                                               AS is_cross_border
FROM RISK_COPILOT.STAGING.AMLSIM_TRANSACTIONS_RAW;

-- =============================================
-- LOAD GROUND TRUTH (from sar_accounts.csv)
-- =============================================
COPY INTO RISK_COPILOT.STAGING.AMLSIM_SAR_RAW
FROM @RISK_COPILOT.STAGING.AMLSIM_STAGE/sar_accounts.csv
FILE_FORMAT = (TYPE = CSV, SKIP_HEADER = 1, FIELD_OPTIONALLY_ENCLOSED_BY = '"');

INSERT INTO RISK_COPILOT.AUDIT.GROUND_TRUTH
    (ground_truth_id, entity_type, entity_id, typology, severity,
     expected_detection, ring_id, description)
SELECT
    UUID_STRING()                                       AS ground_truth_id,
    'PARTY'                                             AS entity_type,
    'P_' || ACCOUNT_ID                                  AS entity_id,
    UPPER(ALERT_TYPE)                                   AS typology,
    CASE WHEN IS_SAR = 'YES' THEN 'HIGH' ELSE 'NONE' END AS severity,
    IS_SAR = 'YES'                                      AS expected_detection,
    'RING_' || ALERT_ID                                 AS ring_id,
    'AMLSim planted ' || ALERT_TYPE || ' typology, alert_id=' || ALERT_ID
                                                        AS description
FROM RISK_COPILOT.STAGING.AMLSIM_SAR_RAW;

-- =============================================
-- GENERATE PARTY_RELATIONSHIP EDGES FROM ALERT MEMBERSHIP
-- =============================================
-- For each alert, create FUND_FLOW edges between member accounts
INSERT INTO RISK_COPILOT.CORE.PARTY_RELATIONSHIP
    (relationship_id, party_id_a, party_id_b, relationship_type, direction,
     match_confidence, first_seen_at, last_seen_at, occurrence_count, is_active)
SELECT DISTINCT
    UUID_STRING()                                       AS relationship_id,
    'P_' || at1.orig_acct                               AS party_id_a,
    'P_' || at1.bene_acct                               AS party_id_b,
    'FUND_FLOW'                                         AS relationship_type,
    'DIRECTED'                                          AS direction,
    1.0                                                 AS match_confidence,
    MIN(TRY_TO_TIMESTAMP(at1.tran_timestamp))           AS first_seen_at,
    MAX(TRY_TO_TIMESTAMP(at1.tran_timestamp))           AS last_seen_at,
    COUNT(*)                                            AS occurrence_count,
    TRUE                                                AS is_active
FROM RISK_COPILOT.STAGING.AMLSIM_ALERT_TX_RAW at1
GROUP BY at1.orig_acct, at1.bene_acct;
```

#### Step 4: Enrich with Shared-Device/IP Edges

AMLSim does not generate device IDs or IP addresses. We synthesize them to create the `SHARED_DEVICE` and `SHARED_IP` edges that our graph detection requires:

```sql
-- For each alert (ring), assign a shared device_id to all member accounts
-- This simulates the real-world pattern where mule accounts are controlled from one device
CREATE OR REPLACE TEMPORARY TABLE ring_devices AS
SELECT DISTINCT
    alert_id,
    'DEV_' || LPAD(alert_id::VARCHAR, 6, '0') AS shared_device_id,
    '103.21.' || MOD(alert_id, 255) || '.' || MOD(alert_id * 7, 255) AS shared_ip
FROM RISK_COPILOT.STAGING.AMLSIM_ALERT_ACCOUNTS_RAW
WHERE is_sar = 'true';

-- Create SHARED_DEVICE edges between all pairs in each ring
INSERT INTO RISK_COPILOT.CORE.PARTY_RELATIONSHIP
    (relationship_id, party_id_a, party_id_b, relationship_type, direction,
     shared_attribute_value, match_confidence, match_method,
     first_seen_at, last_seen_at, is_active)
SELECT
    UUID_STRING(),
    'P_' || a.acct_id,
    'P_' || b.acct_id,
    'SHARED_DEVICE',
    'UNDIRECTED',
    rd.shared_device_id,
    1.0,
    'EXACT',
    CURRENT_TIMESTAMP(),
    CURRENT_TIMESTAMP(),
    TRUE
FROM RISK_COPILOT.STAGING.AMLSIM_ALERT_ACCOUNTS_RAW a
JOIN RISK_COPILOT.STAGING.AMLSIM_ALERT_ACCOUNTS_RAW b
  ON a.alert_id = b.alert_id AND a.acct_id < b.acct_id
JOIN ring_devices rd ON rd.alert_id = a.alert_id;

-- Update TRANSACTION rows with device_id/ip for ring members
-- (so the evidence chain includes device information on the transactions themselves)
UPDATE RISK_COPILOT.CORE.TRANSACTION t
SET
    device_id = rd.shared_device_id,
    ip_address = rd.shared_ip
FROM RISK_COPILOT.STAGING.AMLSIM_ALERT_TX_RAW at
JOIN ring_devices rd ON rd.alert_id = at.alert_id
WHERE t.txn_id = 'TXN_' || at.tran_id;
```

### G.6 Ground Truth & Detection Validation

With AMLSim's labeled data loaded into both the schema and `GROUND_TRUTH`, we can measure detection performance:

```sql
-- ============================================================
-- CONFUSION MATRIX: Detection Logic vs. Ground Truth
-- ============================================================
WITH detection_results AS (
    -- What our detection logic flagged
    SELECT DISTINCT
        s.entity_id AS detected_party_id,
        s.typology  AS detected_typology
    FROM RISK_COPILOT.DETECTION.SIGNAL s
    WHERE s.severity IN ('HIGH', 'CRITICAL')
      AND s.disposition != 'FALSE_POSITIVE'
),
ground AS (
    -- What AMLSim planted
    SELECT
        entity_id AS planted_party_id,
        typology  AS planted_typology,
        expected_detection
    FROM RISK_COPILOT.AUDIT.GROUND_TRUTH
)
SELECT
    -- True Positives: planted SAR + detected
    COUNT(CASE WHEN g.expected_detection = TRUE AND d.detected_party_id IS NOT NULL THEN 1 END)
        AS true_positives,
    -- False Negatives: planted SAR + NOT detected
    COUNT(CASE WHEN g.expected_detection = TRUE AND d.detected_party_id IS NULL THEN 1 END)
        AS false_negatives,
    -- False Positives: NOT planted SAR + detected
    COUNT(CASE WHEN (g.expected_detection = FALSE OR g.planted_party_id IS NULL) AND d.detected_party_id IS NOT NULL THEN 1 END)
        AS false_positives,
    -- Derived metrics
    ROUND(true_positives / NULLIF(true_positives + false_negatives, 0), 3)
        AS recall,
    ROUND(true_positives / NULLIF(true_positives + false_positives, 0), 3)
        AS precision,
    ROUND(2.0 * precision * recall / NULLIF(precision + recall, 0), 3)
        AS f1_score
FROM ground g
FULL OUTER JOIN detection_results d
  ON g.planted_party_id = d.detected_party_id;
```

**Why this matters:** This query is the single most credible artifact for a judge with a risk background. It converts "we built a detection system" into "we built a detection system and here is how well it works — precision X, recall Y, F1 Z." No other team that skips ground-truth labeling can produce this.

---

## H. Real-World Case Study — Bank of Baroda Advance Remittance Scam

### H.1 Case Background

> **Disclaimer:** This analysis is based on publicly reported information about the
> Bank of Baroda (BoB) Ashok Vihar branch foreign remittance scam (2014-2015) and
> analogous patterns from the Danske Bank / Lantana Trade LLP case. It is used here
> purely as a pedagogical case study to demonstrate schema capabilities. All figures
> are approximate and drawn from public reporting.

**What happened:**

Between 2014-2015, over ₹6,100 crore (~$920M) was illegally remitted from a single branch (Ashok Vihar, New Delhi) of Bank of Baroda to Hong Kong, using India's Advance Remittance facility under FEMA. The CBI investigation revealed:

1. **59 current accounts** opened at the same branch within a short window — most belonging to shell companies with no genuine business activity.

2. **Dormant-to-active spike.** Accounts showed minimal or zero activity for weeks/months after opening, then suddenly began receiving large domestic inward transfers and immediately remitting outward to Hong Kong accounts.

3. **Synthetic identity clusters.** The shell companies shared directors (DINs), registered addresses, PAN-linked entities, and phone numbers. Many "directors" were low-income individuals whose identities were borrowed or fabricated.

4. **Structured remittances.** Individual remittances were kept below per-transaction reporting thresholds. Multiple remittances were sent on the same day from different accounts to the same Hong Kong beneficiary.

5. **Rapid pass-through.** Funds arrived domestically (RTGS/NEFT) and were remitted outward (SWIFT) within hours to days — classic mule/pass-through behavior with near-zero retained balance.

6. **Weak KYC exploitation.** All accounts were opened at the same branch, often by the same officials, with thin documentation that would not survive scrutiny.

**Why existing systems missed it:**

- The bank's transaction monitoring system (TMS) operated per-account. No system joined the 59 accounts into a network.
- KYC was branch-level with no cross-branch or cross-entity resolution.
- FEMA advance remittance rules were checked per-transaction, not in aggregate.
- No shared-attribute detection (common directors, addresses, phones across "unrelated" companies).
- No dormancy-to-velocity anomaly detection.

This is precisely the class of fraud our schema is designed to catch.

---

### H.2 Phase-by-Phase Schema Mapping

#### Phase 1: Shell Company Onboarding & Weak KYC

**What happened:** 59 current accounts opened for shell companies at the same branch. Thin KYC. Common directors, addresses, phone numbers.

**Schema tables and columns involved:**

| Table | Column(s) | What It Captures |
|---|---|---|
| `CORE.PARTY` | `party_type = 'CORPORATE'` | Shell companies registered as corporate parties |
| `CORE.PARTY` | `kyc_status`, `kyc_verified_at`, `kyc_expiry_date` | KYC verification status and timing — flags parties verified with thin documentation |
| `CORE.PARTY` | `cdd_level` | Should be `EDD` for corporate accounts with no operating history — if it's `CDD` or `SDD`, that's itself a red flag |
| `CORE.PARTY` | `risk_rating`, `risk_rating_reason` | New corporate accounts with no business history should rate `HIGH` — if rated `LOW`, the rating process failed |
| `CORE.PARTY` | `source_system`, `source_entity_id` | Traces which system/branch originated the KYC |
| `CORE.ACCOUNT` | `opening_date`, `branch_code`, `account_type = 'CURRENT'` | 59 current accounts, same branch, clustered opening dates |
| `CORE.PARTY_RELATIONSHIP` | `relationship_type = 'SHARED_ADDRESS'` | Shell companies sharing the same registered office |
| `CORE.PARTY_RELATIONSHIP` | `relationship_type = 'DIRECTOR'` | Common directors across multiple "unrelated" companies |
| `CORE.PARTY_RELATIONSHIP` | `relationship_type = 'SHARED_PHONE'` | Same contact number registered for multiple entities |
| `CORE.PARTY_RELATIONSHIP` | `match_confidence`, `match_method` | Exact matches on PAN/DIN/address = confidence 1.0 |

#### Phase 2: Dormancy Followed by Sudden Velocity Spike

**What happened:** Accounts sat dormant for weeks, then suddenly began receiving large domestic transfers and immediately remitting outward.

| Table | Column(s) | What It Captures |
|---|---|---|
| `CORE.TRANSACTION` | `txn_timestamp`, `txn_date` | Time series for velocity computation: zero transactions for N days, then burst |
| `CORE.TRANSACTION` | `amount`, `currency` | Sudden large inflows where previously there were none |
| `CORE.ACCOUNT` | `current_balance`, `status` | Balance near zero for extended periods, then spikes |
| `DETECTION.SIGNAL` | `rule_id = 'DORMANCY_VELOCITY_SPIKE'`, `typology = 'ATO'` | Rule fires when a dormant account (< K transactions in last 90 days) suddenly exceeds N transactions or ₹X in a 7-day window |
| `DETECTION.SIGNAL` | `signal_detail` | Captures: `{"dormant_days": 67, "burst_txn_count": 23, "burst_amount": 12500000, "burst_window_days": 3}` |

#### Phase 3: Rapid Pass-Through (Domestic Inward → Foreign Outward)

**What happened:** Funds received via RTGS/NEFT were remitted outward via SWIFT within hours, with near-zero retained balance. Classic mule behavior.

| Table | Column(s) | What It Captures |
|---|---|---|
| `CORE.TRANSACTION` | `channel = 'NEFT'/'RTGS'` (inbound), `channel = 'SWIFT'` (outbound) | Channel-switching: domestic rail in, foreign rail out |
| `CORE.TRANSACTION` | `is_cross_border = TRUE` | Flags outbound leg |
| `CORE.TRANSACTION` | `counterparty_country = 'HKG'` | Hong Kong destination — high-risk corridor for India-origin remittances |
| `CORE.TRANSACTION` | `purpose_code` | FEMA advance remittance purpose codes — certain codes are higher risk |
| `CORE.PARTY_RELATIONSHIP` | `relationship_type = 'FUND_FLOW'`, `avg_pass_through_minutes` | Measures velocity between inbound and outbound legs |
| `CORE.PARTY_RELATIONSHIP` | `total_flow_amount` | Aggregate flow through the pass-through account |
| `DETECTION.SIGNAL` | `typology = 'PASSTHROUGH'`, `rule_category = 'VELOCITY'` | Pass-through signal: inbound amount ≈ outbound amount within N hours |
| `DETECTION.SIGNAL` | `signal_detail` | `{"inbound_amount": 5000000, "outbound_amount": 4950000, "retained_pct": 1.0, "pass_through_hours": 6, "inbound_channel": "RTGS", "outbound_channel": "SWIFT"}` |

#### Phase 4: Structured Smurfing Below Reporting Limits

**What happened:** Individual remittances kept below per-transaction thresholds. Multiple remittances on the same day from different accounts to the same Hong Kong beneficiary.

| Table | Column(s) | What It Captures |
|---|---|---|
| `CORE.TRANSACTION` | `amount` | Individual amounts just below the reporting threshold |
| `CORE.TRANSACTION` | `credit_party_id` (or `counterparty_name` for external) | Same external beneficiary receiving from multiple accounts |
| `CORE.TRANSACTION` | `txn_date` | Multiple transactions on the same day — temporal clustering |
| `POLICY.POLICY_CLAUSE` | `metric = 'single_remittance_amount'`, `operator = 'GTE'`, `threshold_value = 500000` | The threshold the fraudsters structured below |
| `DETECTION.SIGNAL` | `typology = 'SMURFING'`, `rule_category = 'STRUCTURING'` | Aggregate across accounts exceeds threshold while individual transactions stay below it |
| `DETECTION.SIGNAL` | `signal_detail` | `{"individual_amounts": [490000, 495000, 480000], "aggregate_amount": 1465000, "threshold": 500000, "num_accounts": 3, "same_beneficiary": "HK_COMPANY_X", "same_day": true}` |

#### Phase 5: The Network — All Phases Connected

**What happened:** The 59 accounts were not independent. They were a coordinated network controlled by a small group operating shell companies from the same addresses with the same directors.

| Table | Column(s) | What It Captures |
|---|---|---|
| `CORE.PARTY_RELATIONSHIP` | ALL relationship types | The full graph: DIRECTOR + SHARED_ADDRESS + SHARED_PHONE + FUND_FLOW edges connecting all 59 entities |
| `CORE.PARTY` | `match_cluster_id` | Entity resolution: 59 "different" companies → 1 cluster controlled by the same group |
| `DETECTION.CASE` | `case_type = 'AML'`, `related_party_ids` (array of all 59) | Single case grouping the entire network |
| `DETECTION.EVIDENCE` | All evidence rows pointing to transactions, relationships, and policy clauses | The complete evidence bundle — re-verifiable |
| `COMPLIANCE.FINDING` | `finding_type = 'STR_WARRANTED'`, `narrative` | The reasoned conclusion citing the network, the velocity, the structuring, and the applicable FEMA/PMLA clauses |

---

### H.3 Production SQL — L1 Rule-Based Detection

These are concrete SQL queries that would catch each phase of the BoB scam using our schema. Each maps to a `DETECTION.SIGNAL` row.

#### Signal 1: Account-Opening Cluster at a Single Branch

Detects an unusual concentration of new corporate current accounts at one branch within a short window.

```sql
-- SIGNAL: BRANCH_ONBOARDING_CLUSTER
-- Fires when > N corporate current accounts are opened at the same branch
-- within a rolling M-day window

WITH branch_openings AS (
    SELECT
        a.branch_code,
        a.account_id,
        a.party_id,
        a.opening_date,
        p.party_type,
        COUNT(*) OVER (
            PARTITION BY a.branch_code
            ORDER BY a.opening_date
            RANGE BETWEEN INTERVAL '60 DAYS' PRECEDING AND CURRENT ROW
        ) AS rolling_60d_corporate_opens
    FROM RISK_COPILOT.CORE.ACCOUNT a
    JOIN RISK_COPILOT.CORE.PARTY p ON p.party_id = a.party_id
    WHERE a.account_type = 'CURRENT'
      AND p.party_type = 'CORPORATE'
      AND a.opening_date >= DATEADD('day', -90, CURRENT_DATE())
)
SELECT
    branch_code,
    opening_date,
    rolling_60d_corporate_opens,
    ARRAY_AGG(DISTINCT account_id) AS cluster_accounts,
    ARRAY_AGG(DISTINCT party_id)   AS cluster_parties
FROM branch_openings
WHERE rolling_60d_corporate_opens >= 10    -- threshold: 10+ corporate accounts at one branch in 60 days
GROUP BY branch_code, opening_date, rolling_60d_corporate_opens
ORDER BY rolling_60d_corporate_opens DESC;

-- Each row above becomes a DETECTION.SIGNAL with:
--   rule_id:    'BRANCH_ONBOARDING_CLUSTER_V1'
--   typology:   'SHELL_COMPANY_CLUSTER'
--   severity:   'HIGH'
--   signal_detail: { branch_code, count, accounts[], parties[], window_days: 60 }
```

#### Signal 2: Dormancy-to-Velocity Spike

Detects accounts that were dormant (< K transactions in the past N days) and suddenly spike in volume or value.

```sql
-- SIGNAL: DORMANCY_VELOCITY_SPIKE
-- Fires when a previously dormant account suddenly shows high-velocity activity

WITH account_activity AS (
    SELECT
        t.debit_account_id AS account_id,
        -- Activity in the dormancy window (91-180 days ago)
        COUNT(CASE WHEN t.txn_date BETWEEN DATEADD('day', -180, CURRENT_DATE())
                                       AND DATEADD('day', -91, CURRENT_DATE())
              THEN 1 END)                                       AS dormant_window_txn_count,
        SUM(CASE WHEN t.txn_date BETWEEN DATEADD('day', -180, CURRENT_DATE())
                                      AND DATEADD('day', -91, CURRENT_DATE())
             THEN t.amount ELSE 0 END)                          AS dormant_window_volume,
        -- Activity in the burst window (last 7 days)
        COUNT(CASE WHEN t.txn_date >= DATEADD('day', -7, CURRENT_DATE())
              THEN 1 END)                                       AS burst_window_txn_count,
        SUM(CASE WHEN t.txn_date >= DATEADD('day', -7, CURRENT_DATE())
             THEN t.amount ELSE 0 END)                          AS burst_window_volume
    FROM RISK_COPILOT.CORE.TRANSACTION t
    WHERE t.txn_date >= DATEADD('day', -180, CURRENT_DATE())
    GROUP BY t.debit_account_id
)
SELECT
    aa.account_id,
    a.party_id,
    p.full_name,
    p.party_type,
    aa.dormant_window_txn_count,
    aa.dormant_window_volume,
    aa.burst_window_txn_count,
    aa.burst_window_volume,
    ROUND(aa.burst_window_volume / NULLIF(aa.dormant_window_volume, 0), 1)
        AS volume_multiplier
FROM account_activity aa
JOIN RISK_COPILOT.CORE.ACCOUNT a ON a.account_id = aa.account_id
JOIN RISK_COPILOT.CORE.PARTY p ON p.party_id = a.party_id
WHERE aa.dormant_window_txn_count <= 3          -- dormant: ≤3 transactions in 90 days
  AND aa.burst_window_txn_count >= 10           -- burst: 10+ transactions in 7 days
  AND aa.burst_window_volume >= 1000000         -- and ≥ ₹10L in value
ORDER BY aa.burst_window_volume DESC;

-- SIGNAL output:
--   rule_id:    'DORMANCY_VELOCITY_SPIKE_V1'
--   typology:   'ATO' (or 'MULE_RING' if combined with pass-through)
--   severity:   'HIGH' (CRITICAL if volume_multiplier > 100x)
```

#### Signal 3: Cross-Border Pass-Through Velocity

Detects funds arriving domestically and being remitted outward within N hours with near-zero retention — the core BoB pattern.

```sql
-- SIGNAL: CROSS_BORDER_PASSTHROUGH
-- Fires when domestic inbound is followed by foreign outbound within N hours
-- with retained balance < M%

WITH inbound AS (
    SELECT
        txn_id AS inbound_txn_id,
        credit_account_id,
        credit_party_id,
        amount AS inbound_amount,
        txn_timestamp AS inbound_ts,
        channel AS inbound_channel
    FROM RISK_COPILOT.CORE.TRANSACTION
    WHERE is_cross_border = FALSE
      AND txn_date >= DATEADD('day', -30, CURRENT_DATE())
      AND amount >= 100000   -- ₹1L minimum to filter noise
),
outbound AS (
    SELECT
        txn_id AS outbound_txn_id,
        debit_account_id,
        debit_party_id,
        amount AS outbound_amount,
        txn_timestamp AS outbound_ts,
        channel AS outbound_channel,
        counterparty_country,
        counterparty_name,
        purpose_code
    FROM RISK_COPILOT.CORE.TRANSACTION
    WHERE is_cross_border = TRUE
      AND txn_date >= DATEADD('day', -30, CURRENT_DATE())
      AND channel IN ('SWIFT', 'NEFT')   -- foreign remittance channels
)
SELECT
    i.credit_account_id                                         AS account_id,
    i.credit_party_id                                           AS party_id,
    i.inbound_txn_id,
    o.outbound_txn_id,
    i.inbound_amount,
    o.outbound_amount,
    ROUND(100.0 * (i.inbound_amount - o.outbound_amount)
          / NULLIF(i.inbound_amount, 0), 2)                     AS retained_pct,
    TIMESTAMPDIFF('hour', i.inbound_ts, o.outbound_ts)          AS pass_through_hours,
    i.inbound_channel,
    o.outbound_channel,
    o.counterparty_country,
    o.counterparty_name,
    o.purpose_code
FROM inbound i
JOIN outbound o
  ON i.credit_account_id = o.debit_account_id
 AND o.outbound_ts > i.inbound_ts
 AND TIMESTAMPDIFF('hour', i.inbound_ts, o.outbound_ts) <= 72   -- within 72 hours
WHERE ROUND(100.0 * (i.inbound_amount - o.outbound_amount)
            / NULLIF(i.inbound_amount, 0), 2) <= 5.0             -- ≤5% retained
ORDER BY i.inbound_amount DESC;

-- SIGNAL output:
--   rule_id:    'CROSS_BORDER_PASSTHROUGH_V1'
--   typology:   'PASSTHROUGH'
--   severity:   'CRITICAL' (cross-border + high velocity + near-zero retention)
--   policy_clause_id: CL_AML_FEMA_001 (FEMA advance remittance rules)
```

#### Signal 4: Same-Beneficiary Structuring Across Multiple Accounts

Detects multiple accounts sending to the same foreign beneficiary on the same day, with individual amounts below the reporting threshold.

```sql
-- SIGNAL: MULTI_ACCOUNT_STRUCTURING
-- Fires when N+ accounts send to the same external beneficiary within M days,
-- individual amounts below threshold T, aggregate above threshold T

WITH beneficiary_flows AS (
    SELECT
        counterparty_name,
        counterparty_country,
        txn_date,
        debit_account_id,
        debit_party_id,
        amount,
        txn_id
    FROM RISK_COPILOT.CORE.TRANSACTION
    WHERE is_cross_border = TRUE
      AND counterparty_name IS NOT NULL
      AND txn_date >= DATEADD('day', -30, CURRENT_DATE())
),
aggregated AS (
    SELECT
        counterparty_name,
        counterparty_country,
        txn_date,
        COUNT(DISTINCT debit_account_id)    AS num_accounts,
        COUNT(*)                            AS num_transactions,
        SUM(amount)                         AS aggregate_amount,
        MAX(amount)                         AS max_single_amount,
        MIN(amount)                         AS min_single_amount,
        ARRAY_AGG(DISTINCT debit_account_id) AS sending_accounts,
        ARRAY_AGG(DISTINCT debit_party_id)   AS sending_parties,
        ARRAY_AGG(txn_id)                    AS related_txn_ids
    FROM beneficiary_flows
    GROUP BY counterparty_name, counterparty_country, txn_date
)
SELECT *
FROM aggregated
WHERE num_accounts >= 3                    -- 3+ different accounts
  AND max_single_amount < 500000           -- all individual amounts below ₹5L threshold
  AND aggregate_amount >= 500000           -- but aggregate exceeds it
ORDER BY aggregate_amount DESC;

-- SIGNAL output:
--   rule_id:    'MULTI_ACCT_STRUCTURING_V1'
--   typology:   'SMURFING'
--   severity:   'CRITICAL'
--   policy_clause_id: CL_AML_STRUCT_001 (PMLA structuring provisions)
```

#### Signal 5: Shared-Director / Shared-Address Network Detection

Detects clusters of corporate entities sharing directors, addresses, or phone numbers — the shell company network fingerprint.

```sql
-- SIGNAL: SHARED_IDENTITY_CLUSTER
-- Finds clusters of corporate parties connected by shared directors, addresses, or phones

WITH identity_links AS (
    SELECT
        party_id_a,
        party_id_b,
        relationship_type,
        shared_attribute_value,
        match_confidence
    FROM RISK_COPILOT.CORE.PARTY_RELATIONSHIP
    WHERE relationship_type IN ('DIRECTOR', 'SHARED_ADDRESS', 'SHARED_PHONE', 'SHARED_EMAIL')
      AND is_active = TRUE
      AND match_confidence >= 0.8
),
-- Count how many distinct link types connect each pair
pair_strength AS (
    SELECT
        party_id_a,
        party_id_b,
        COUNT(DISTINCT relationship_type) AS link_type_count,
        ARRAY_AGG(DISTINCT relationship_type) AS link_types,
        ARRAY_AGG(DISTINCT shared_attribute_value) AS shared_values
    FROM identity_links
    GROUP BY party_id_a, party_id_b
),
-- Find parties with many connections (hub nodes in the shell network)
hub_parties AS (
    SELECT
        party_id_a AS party_id,
        COUNT(DISTINCT party_id_b) AS connected_entities,
        SUM(link_type_count) AS total_link_strength
    FROM pair_strength
    GROUP BY party_id_a
    HAVING connected_entities >= 3    -- connected to 3+ other entities
)
SELECT
    hp.party_id,
    p.full_name,
    p.party_type,
    p.kyc_status,
    p.risk_rating,
    hp.connected_entities,
    hp.total_link_strength,
    -- Pull the accounts associated with this party
    ARRAY_AGG(DISTINCT a.account_id) AS associated_accounts,
    -- Pull recent transaction volume
    (SELECT SUM(amount) FROM RISK_COPILOT.CORE.TRANSACTION t
     WHERE (t.debit_party_id = hp.party_id OR t.credit_party_id = hp.party_id)
       AND t.txn_date >= DATEADD('day', -90, CURRENT_DATE())) AS recent_90d_volume
FROM hub_parties hp
JOIN RISK_COPILOT.CORE.PARTY p ON p.party_id = hp.party_id
LEFT JOIN RISK_COPILOT.CORE.ACCOUNT a ON a.party_id = hp.party_id
GROUP BY hp.party_id, p.full_name, p.party_type, p.kyc_status, p.risk_rating,
         hp.connected_entities, hp.total_link_strength
ORDER BY hp.total_link_strength DESC;

-- SIGNAL output:
--   rule_id:    'SHARED_IDENTITY_CLUSTER_V1'
--   typology:   'SHELL_COMPANY_NETWORK'
--   severity:   'CRITICAL'
--   signal_detail: { hub_party, connected_entities, link_types, shared_values }
```

---

### H.4 Feature Engineering Pipeline — L2 ML-Based Detection

The L1 SQL rules above catch known patterns. L2 uses ML models trained on features extracted from the schema to catch novel patterns — variations the rule-writers didn't anticipate.

#### Feature Vectors for Classical ML (Isolation Forest, XGBoost)

Each feature is a SQL-derivable metric from our schema. The feature table is materialized as a dynamic table with daily refresh.

```sql
-- ============================================================
-- L2 FEATURE TABLE: One row per (party_id, as_of_date)
-- Input to Isolation Forest / XGBoost for anomaly detection
-- ============================================================
CREATE OR REPLACE DYNAMIC TABLE RISK_COPILOT.DETECTION.PARTY_FEATURES
    TARGET_LAG = '1 day'
    WAREHOUSE = RISK_COPILOT_WH
AS
WITH txn_stats AS (
    SELECT
        t.debit_party_id AS party_id,
        -- Volume features
        COUNT(*)                                            AS txn_count_90d,
        SUM(t.amount)                                       AS txn_volume_90d,
        AVG(t.amount)                                       AS avg_txn_amount,
        STDDEV(t.amount)                                    AS stddev_txn_amount,
        MAX(t.amount)                                       AS max_txn_amount,
        -- Velocity features
        COUNT(CASE WHEN t.txn_date >= DATEADD('day', -7, CURRENT_DATE()) THEN 1 END)
                                                            AS txn_count_7d,
        SUM(CASE WHEN t.txn_date >= DATEADD('day', -7, CURRENT_DATE()) THEN t.amount ELSE 0 END)
                                                            AS txn_volume_7d,
        -- Dormancy ratio: 7-day activity vs 90-day average
        NULLIF(txn_count_7d, 0) /
            NULLIF(txn_count_90d / 12.86, 0)                AS velocity_ratio_7d_vs_90d,
        -- Channel features
        COUNT(CASE WHEN t.is_cross_border = TRUE THEN 1 END)
                                                            AS cross_border_count,
        SUM(CASE WHEN t.is_cross_border = TRUE THEN t.amount ELSE 0 END)
                                                            AS cross_border_volume,
        cross_border_volume / NULLIF(txn_volume_90d, 0)     AS cross_border_ratio,
        COUNT(CASE WHEN t.is_cash = TRUE THEN 1 END)        AS cash_txn_count,
        SUM(CASE WHEN t.is_cash = TRUE THEN t.amount ELSE 0 END)
                                                            AS cash_volume,
        -- Counterparty features
        COUNT(DISTINCT t.credit_party_id)                   AS unique_counterparties,
        COUNT(DISTINCT t.counterparty_country)              AS unique_countries,
        COUNT(DISTINCT t.channel)                           AS unique_channels,
        -- Pass-through features
        COUNT(CASE WHEN t.is_cross_border = TRUE
                    AND t.channel IN ('SWIFT', 'NEFT') THEN 1 END)
                                                            AS outbound_remittance_count,
        -- Temporal features
        COUNT(DISTINCT t.txn_date)                          AS active_days_90d,
        DATEDIFF('day', MIN(t.txn_date), MAX(t.txn_date))  AS activity_span_days,
        -- Structuring features
        COUNT(CASE WHEN t.amount BETWEEN 450000 AND 500000 THEN 1 END)
                                                            AS near_threshold_count,
        -- Time-of-day features
        AVG(HOUR(t.txn_timestamp))                          AS avg_txn_hour,
        STDDEV(HOUR(t.txn_timestamp))                       AS stddev_txn_hour
    FROM RISK_COPILOT.CORE.TRANSACTION t
    WHERE t.txn_date >= DATEADD('day', -90, CURRENT_DATE())
    GROUP BY t.debit_party_id
),
graph_stats AS (
    SELECT
        party_id_a AS party_id,
        -- Network features
        COUNT(DISTINCT party_id_b)                          AS degree_total,
        COUNT(DISTINCT CASE WHEN relationship_type = 'FUND_FLOW' THEN party_id_b END)
                                                            AS degree_fund_flow,
        COUNT(DISTINCT CASE WHEN relationship_type IN ('SHARED_DEVICE', 'SHARED_IP', 'SHARED_PHONE', 'SHARED_ADDRESS')
                             THEN party_id_b END)
                                                            AS degree_shared_attributes,
        COUNT(DISTINCT CASE WHEN relationship_type = 'DIRECTOR' THEN party_id_b END)
                                                            AS degree_director_links,
        AVG(match_confidence)                               AS avg_link_confidence,
        SUM(total_flow_amount)                              AS total_network_flow
    FROM RISK_COPILOT.CORE.PARTY_RELATIONSHIP
    WHERE is_active = TRUE
    GROUP BY party_id_a
),
party_info AS (
    SELECT
        party_id,
        party_type,
        CASE WHEN party_type = 'CORPORATE' THEN 1 ELSE 0 END AS is_corporate,
        CASE WHEN is_pep = TRUE THEN 1 ELSE 0 END              AS is_pep_flag,
        CASE risk_rating
            WHEN 'LOW' THEN 1 WHEN 'MEDIUM' THEN 2
            WHEN 'HIGH' THEN 3 WHEN 'VERY_HIGH' THEN 4
            ELSE 2
        END                                                     AS risk_rating_numeric,
        DATEDIFF('day', (SELECT MIN(opening_date) FROM RISK_COPILOT.CORE.ACCOUNT a
                         WHERE a.party_id = p.party_id), CURRENT_DATE())
                                                                AS account_age_days
    FROM RISK_COPILOT.CORE.PARTY p
)
SELECT
    ts.party_id,
    CURRENT_DATE()                          AS as_of_date,
    -- Party features
    pi.is_corporate,
    pi.is_pep_flag,
    pi.risk_rating_numeric,
    pi.account_age_days,
    -- Volume features
    COALESCE(ts.txn_count_90d, 0)           AS txn_count_90d,
    COALESCE(ts.txn_volume_90d, 0)          AS txn_volume_90d,
    COALESCE(ts.avg_txn_amount, 0)          AS avg_txn_amount,
    COALESCE(ts.stddev_txn_amount, 0)       AS stddev_txn_amount,
    COALESCE(ts.max_txn_amount, 0)          AS max_txn_amount,
    -- Velocity features
    COALESCE(ts.txn_count_7d, 0)            AS txn_count_7d,
    COALESCE(ts.txn_volume_7d, 0)           AS txn_volume_7d,
    COALESCE(ts.velocity_ratio_7d_vs_90d, 0) AS velocity_ratio,
    -- Channel features
    COALESCE(ts.cross_border_count, 0)      AS cross_border_count,
    COALESCE(ts.cross_border_ratio, 0)      AS cross_border_ratio,
    COALESCE(ts.cash_txn_count, 0)          AS cash_txn_count,
    COALESCE(ts.cash_volume, 0)             AS cash_volume,
    -- Counterparty features
    COALESCE(ts.unique_counterparties, 0)   AS unique_counterparties,
    COALESCE(ts.unique_countries, 0)        AS unique_countries,
    COALESCE(ts.unique_channels, 0)         AS unique_channels,
    -- Pass-through features
    COALESCE(ts.outbound_remittance_count, 0) AS outbound_remittance_count,
    -- Structuring features
    COALESCE(ts.near_threshold_count, 0)    AS near_threshold_count,
    -- Temporal features
    COALESCE(ts.active_days_90d, 0)         AS active_days_90d,
    COALESCE(ts.activity_span_days, 0)      AS activity_span_days,
    COALESCE(ts.avg_txn_hour, 12)           AS avg_txn_hour,
    COALESCE(ts.stddev_txn_hour, 0)         AS stddev_txn_hour,
    -- Graph features
    COALESCE(gs.degree_total, 0)            AS degree_total,
    COALESCE(gs.degree_fund_flow, 0)        AS degree_fund_flow,
    COALESCE(gs.degree_shared_attributes, 0) AS degree_shared_attributes,
    COALESCE(gs.degree_director_links, 0)   AS degree_director_links,
    COALESCE(gs.avg_link_confidence, 0)     AS avg_link_confidence,
    COALESCE(gs.total_network_flow, 0)      AS total_network_flow
FROM txn_stats ts
JOIN party_info pi ON pi.party_id = ts.party_id
LEFT JOIN graph_stats gs ON gs.party_id = ts.party_id;
```

**Feature importance for the BoB case:**

| Feature | Why It Matters for This Case | Expected Signal |
|---|---|---|
| `velocity_ratio` | Dormancy-to-burst detection | >>10x for mule accounts (dormant then explode) |
| `cross_border_ratio` | Legitimate businesses have stable ratios | Near 1.0 (almost all activity is cross-border remittance) |
| `near_threshold_count` | Structuring fingerprint | High count of amounts at 90-99% of threshold |
| `degree_shared_attributes` | Shell company network | 5-10+ for entities in the BoB cluster |
| `degree_director_links` | Common directors | >0 for any corporate party; >3 = red flag |
| `account_age_days` (low) + `txn_volume_90d` (high) | Young account, high volume | Classic mule/shell pattern |
| `unique_countries` = 1 (only HK) | All remittances to one jurisdiction | Strong signal for layering to specific destination |
| `outbound_remittance_count` (high) + `is_corporate` | Corporate shell doing nothing but remitting | Smoking gun for the BoB pattern |

#### Graph Algorithm Definitions (Node & Edge Specifications)

For algorithms like Louvain community detection and PageRank, here are the exact node and edge definitions derived from our schema:

**Node Definition:**

```sql
-- Nodes for graph algorithms
SELECT
    p.party_id          AS node_id,
    p.party_type        AS node_type,       -- INDIVIDUAL | CORPORATE
    p.risk_rating       AS node_weight,     -- used for weighted algorithms
    p.jurisdiction      AS node_attribute
FROM RISK_COPILOT.CORE.PARTY p
WHERE p.is_active = TRUE;
```

**Edge Definition (multi-edge, weighted):**

```sql
-- Edges for graph algorithms
-- Each relationship type becomes a weighted edge
SELECT
    pr.party_id_a       AS source,
    pr.party_id_b       AS target,
    pr.relationship_type AS edge_type,
    -- Edge weight: combines confidence with relationship strength
    pr.match_confidence *
        CASE pr.relationship_type
            WHEN 'FUND_FLOW'       THEN 1.0  -- financial connection = highest weight
            WHEN 'SHARED_DEVICE'   THEN 0.9  -- strong: common control
            WHEN 'DIRECTOR'        THEN 0.85 -- strong: legal connection
            WHEN 'SHARED_PHONE'    THEN 0.8  -- strong: identity overlap
            WHEN 'SHARED_ADDRESS'  THEN 0.6  -- moderate: could be legitimate (same building)
            WHEN 'SHARED_IP'       THEN 0.4  -- weak: VPN, shared office
            WHEN 'SHARED_EMAIL'    THEN 0.7  -- moderate-strong
            WHEN 'BENEFICIAL_OWNER' THEN 0.95 -- very strong: ownership
            WHEN 'FAMILY'          THEN 0.3  -- context only
            WHEN 'GUARANTOR'       THEN 0.5  -- moderate: financial link
        END                         AS edge_weight,
    pr.direction                    AS edge_direction,
    pr.total_flow_amount            AS flow_amount,      -- for flow-based algorithms
    pr.occurrence_count             AS edge_frequency     -- repeated connections = stronger signal
FROM RISK_COPILOT.CORE.PARTY_RELATIONSHIP pr
WHERE pr.is_active = TRUE;
```

**Algorithm application to the BoB case:**

| Algorithm | What It Finds | BoB Application |
|---|---|---|
| **Louvain Community Detection** | Dense clusters of interconnected nodes | Identifies the cluster of 59 shell companies as a single community — even if no single SQL rule catches all links |
| **PageRank** | Nodes that are disproportionately connected or central | Finds the "controller" parties — the handful of real individuals behind the 59 shells. High PageRank = hub of the network |
| **Connected Components** | Groups nodes reachable from each other | Separates the BoB network from legitimate customers. All 59 shells would be one component |
| **Betweenness Centrality** | Nodes that act as bridges between sub-networks | Identifies the intermediary accounts through which funds are funneled — the chokepoints in the layering chain |
| **Jaccard Similarity (on edges)** | Pairs of nodes with similar connection patterns | Finds shell companies with near-identical counterparty sets — even if they don't transact directly with each other |

**Implementation note:** Snowflake does not have native graph algorithm libraries. These algorithms would be implemented as:
- **Snowpark Python UDTFs** running on the node/edge data extracted above
- or **External functions** calling a graph compute service
- or **Bounded recursive CTEs** for simpler algorithms (connected components, k-hop traversal)

The node/edge SQL above produces the input DataFrame for any of these approaches.

---

### H.5 Schema Gap Analysis — What's Missing for Full Regulatory Coverage

Evaluating our schema against the BoB case and RBI/FIU-IND mandates, these gaps exist:

#### Gap 1: Ultimate Beneficial Ownership (UBO) Chain

**Regulatory requirement:** RBI KYC Master Direction (2016, amended 2023) — Section 16: Banks must identify beneficial owners holding >10% (reduced from 25%) of share capital for companies, and all natural persons who ultimately control the customer entity.

**Current schema limitation:** `PARTY_RELATIONSHIP` has `BENEFICIAL_OWNER` and `DIRECTOR` edge types, but no structured chain of ownership. The BoB case involved layered ownership: Company A owned by Company B owned by Individual X. The current schema models each hop as a flat edge but has no way to compute the **cumulative ownership percentage** through a chain.

**Recommended addition:**

```sql
ALTER TABLE RISK_COPILOT.CORE.PARTY_RELATIONSHIP ADD COLUMN
    ownership_pct FLOAT;               -- percentage ownership (e.g., 0.51 = 51%)
    -- Only populated when relationship_type IN ('BENEFICIAL_OWNER', 'DIRECTOR')

-- New table: Materialized UBO chains (derived from recursive traversal of ownership edges)
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.UBO_CHAIN (
    ubo_chain_id        VARCHAR(64)     NOT NULL,
    ultimate_owner_id   VARCHAR(64)     NOT NULL,   -- FK to PARTY (natural person)
    controlled_entity_id VARCHAR(64)    NOT NULL,   -- FK to PARTY (corporate)
    ownership_path      ARRAY           NOT NULL,   -- [entity_1, entity_2, ..., ultimate_owner]
    cumulative_pct      FLOAT           NOT NULL,   -- product of ownership_pct along the chain
    chain_depth         INTEGER         NOT NULL,   -- number of hops
    computed_at         TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_ubo_chain PRIMARY KEY (ubo_chain_id),
    CONSTRAINT fk_ubo_owner FOREIGN KEY (ultimate_owner_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id),
    CONSTRAINT fk_ubo_entity FOREIGN KEY (controlled_entity_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
COMMENT = 'Materialized UBO chains — cumulative ownership through multi-hop corporate structures';
```

#### Gap 2: FEMA-Specific Transaction Metadata

**Regulatory requirement:** FEMA (Foreign Exchange Management Act) and RBI's AD (Authorized Dealer) guidelines require specific metadata for foreign remittances: purpose code (as per RBI schedule), AD category, FIRC (Foreign Inward Remittance Certificate) number, LRS (Liberalized Remittance Scheme) utilization tracking.

**Current schema limitation:** `CORE.TRANSACTION` has `purpose_code` and `is_cross_border` but lacks FEMA-specific fields that were directly relevant to the BoB scam.

**Recommended addition:**

```sql
ALTER TABLE RISK_COPILOT.CORE.TRANSACTION ADD COLUMN
    fema_purpose_code       VARCHAR(10),    -- RBI schedule code (e.g., S0001 to S1099)
    fema_ad_category        VARCHAR(5),     -- AD-I, AD-II, Full-fledged money changer
    fema_firc_number        VARCHAR(50),    -- Foreign Inward Remittance Certificate number
    lrs_utilized_ytd        NUMBER(18,2),   -- LRS utilization year-to-date for this party
    correspondent_bank      VARCHAR(200),   -- nostro/vostro correspondent bank
    swift_message_type      VARCHAR(10);    -- MT103, MT202, etc.
```

#### Gap 3: KYC Document Registry with Verification Status

**Regulatory requirement:** RBI KYC Master Direction requires specific documents (PAN, Aadhaar, passport, DIN for companies, MoA/AoA). The BoB case involved thin documentation — the system should track WHICH documents were collected and their verification status.

**Current schema limitation:** `CORE.PARTY` has `kyc_status` (overall) but not per-document tracking. Cannot answer "was this company's MoA verified?" or "is the DIN of this director valid on MCA portal?"

**Recommended addition:**

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.CORE.KYC_DOCUMENT (
    document_id         VARCHAR(64)     NOT NULL,
    party_id            VARCHAR(64)     NOT NULL,
    -- Document identity
    document_type       VARCHAR(30)     NOT NULL,   -- PAN | AADHAAR | PASSPORT | DIN | MOA | AOA | COI | GST | VOTER_ID | DRIVING_LICENSE
    document_number     VARCHAR(100)    NOT NULL,
    issuing_authority   VARCHAR(100),
    -- Verification
    verification_status VARCHAR(20)     NOT NULL    -- PENDING | VERIFIED | FAILED | EXPIRED | REVOKED
                        DEFAULT 'PENDING',
    verified_against    VARCHAR(100),               -- 'MCA_PORTAL' | 'UIDAI' | 'NSDL_PAN' | 'MANUAL'
    verified_at         TIMESTAMP_NTZ,
    verified_by         VARCHAR(100),               -- user or system that verified
    -- Validity
    issue_date          DATE,
    expiry_date         DATE,
    -- Audit
    collected_at        TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    collected_by        VARCHAR(100),
    branch_code         VARCHAR(20),

    CONSTRAINT pk_kyc_document PRIMARY KEY (document_id),
    CONSTRAINT fk_kyc_party FOREIGN KEY (party_id) REFERENCES RISK_COPILOT.CORE.PARTY(party_id)
)
COMMENT = 'Per-document KYC tracking — which documents collected, verification status, validity';
```

**BoB relevance:** A query like `SELECT party_id, COUNT(*) FROM KYC_DOCUMENT WHERE verification_status != 'VERIFIED' GROUP BY party_id HAVING COUNT(*) > 2` would immediately flag parties with multiple unverified documents — the thin-KYC pattern.

#### Gap 4: Regulatory Filing Register

**Regulatory requirement:** PMLA requires maintaining a register of all filings (CTR, STR, CCR, NTR, CBWTR) with filing dates, acknowledgements, and follow-up status. RBI examination routinely asks for this register.

**Current schema limitation:** `COMPLIANCE.FINDING` tracks whether a finding led to a filing (`status = 'FILED'`, `filing_reference`), but there's no dedicated register that tracks all filings by type, due date, and FIU-IND acknowledgement — especially non-STR filings like CTRs which are threshold-triggered, not investigation-driven.

**Recommended addition:**

```sql
CREATE OR REPLACE TABLE RISK_COPILOT.COMPLIANCE.FILING_REGISTER (
    filing_id           VARCHAR(64)     NOT NULL,
    -- Filing classification
    filing_type         VARCHAR(10)     NOT NULL,   -- STR | CTR | CCR | NTR | CBWTR
    reporting_entity    VARCHAR(200)    NOT NULL,   -- bank/NBFC name and code
    -- Trigger
    finding_id          VARCHAR(64),                -- FK to FINDING (for STR); NULL for auto-generated CTR
    trigger_type        VARCHAR(20)     NOT NULL,   -- INVESTIGATION | THRESHOLD | MANUAL | SCHEDULED
    -- Subject
    primary_party_id    VARCHAR(64),
    related_party_ids   ARRAY,
    related_account_ids ARRAY,
    -- Dates
    period_start        DATE            NOT NULL,   -- reporting period
    period_end          DATE            NOT NULL,
    due_date            DATE            NOT NULL,   -- regulatory deadline
    filed_date          DATE,
    -- FIU-IND response
    fiu_acknowledgement VARCHAR(200),
    fiu_status          VARCHAR(20),                -- ACCEPTED | QUERY_RAISED | REJECTED
    fiu_query_detail    VARCHAR(4000),
    fiu_response_date   DATE,
    -- Content
    narrative_summary   VARCHAR(4000),
    total_amount        NUMBER(18,2),
    transaction_count   INTEGER,
    -- Audit
    prepared_by         VARCHAR(100)    NOT NULL,
    reviewed_by         VARCHAR(100),
    filed_by            VARCHAR(100),
    -- Status
    status              VARCHAR(20)     NOT NULL    -- DRAFT | READY | FILED | ACKNOWLEDGED | QUERY | CLOSED
                        DEFAULT 'DRAFT',
    created_at          TIMESTAMP_NTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT pk_filing_register PRIMARY KEY (filing_id)
)
COMMENT = 'Regulatory filing register — all CTR/STR/CCR/NTR/CBWTR filings with FIU-IND status';
```

#### Gap 5: Missing Indexes and Clustering Keys

For the BoB-pattern queries to run efficiently at scale:

```sql
-- Additional clustering for cross-border pattern detection
ALTER TABLE RISK_COPILOT.CORE.TRANSACTION
  CLUSTER BY (is_cross_border, txn_date, counterparty_country);

-- Additional clustering for branch-level concentration detection
ALTER TABLE RISK_COPILOT.CORE.ACCOUNT
  CLUSTER BY (branch_code, opening_date);

-- Entity resolution: fast lookup by shared attribute value
ALTER TABLE RISK_COPILOT.CORE.PARTY_RELATIONSHIP
  CLUSTER BY (shared_attribute_value, relationship_type);
```

#### Summary of Gaps

| Gap | Regulatory Driver | Impact If Missing | Priority |
|---|---|---|---|
| UBO Chain table | RBI KYC MD Section 16 (10% BO threshold) | Cannot trace layered ownership — the core BoB pattern goes undetected | **P1 — Critical** |
| FEMA transaction fields | FEMA + AD guidelines | Cannot track LRS utilization, purpose codes, or correspondent banks for cross-border monitoring | **P1 — Critical** for cross-border |
| KYC Document Registry | RBI KYC MD Section 38 (record maintenance) | Cannot audit which documents were collected and verified per-party | **P2 — High** |
| Filing Register | PMLA Rule 7 (filing register) | No centralized register of CTR/STR filings for RBI examination | **P2 — High** |
| Clustering key adjustments | Performance (not regulatory) | Cross-border and branch-cluster queries will be slow at scale | **P3 — Medium** |

---

### H.6 How the Full Evidence Chain Would Have Worked

If our system had been in place at Bank of Baroda's Ashok Vihar branch in 2014, here is the exact sequence that would have triggered detection and prevented ₹6,100 crore in illicit remittances:

```
TIMELINE                          WHAT OUR SYSTEM DOES
════════                          ════════════════════

Month 1-2: Shell companies        SIGNAL: BRANCH_ONBOARDING_CLUSTER
onboard at Ashok Vihar            "15 corporate current accounts opened at branch
branch. 15 accounts opened        ASHOK_VIHAR in 45 days. Normal rate: 2/month."
in 45 days.                       severity: HIGH
                                  → Analyst investigates cluster. No action yet
                                    (opening accounts is not illegal).

Month 2: Entity resolution        SIGNAL: SHARED_IDENTITY_CLUSTER
runs. System discovers shared     "Party P_SHELL_03 shares directors with 8 other
directors (DIN), registered       corporate parties. Shared registered address:
addresses, phone numbers.         '42 Rajpur Road' across 11 entities."
                                  severity: CRITICAL
                                  → CASE opened: AML-2014-00891
                                  → Graph CTE reveals the full cluster
                                  → match_cluster_id assigned to all 59 entities

Month 3: First dormant accounts   SIGNAL: DORMANCY_VELOCITY_SPIKE
suddenly begin receiving          "Account ACCT_SHELL_07: 0 transactions in 67 days,
large RTGS inflows.               then ₹2.3Cr in 3 days (volume_multiplier: ∞)"
                                  severity: CRITICAL
                                  → Existing CASE escalated to P1

Same week: RTGS inflows           SIGNAL: CROSS_BORDER_PASSTHROUGH
remitted outward via SWIFT        "₹2.1Cr inbound (RTGS) → ₹2.05Cr outbound (SWIFT)
to Hong Kong within hours.        to HK_COMPANY_X within 18 hours. Retained: 2.4%"
                                  severity: CRITICAL
                                  policy_clause_id: CL_AML_FEMA_001
                                  → Evidence pointers collected for all transactions

Same week: Multiple shell         SIGNAL: MULTI_ACCT_STRUCTURING
accounts send to same HK          "5 accounts → same beneficiary HK_COMPANY_X on
beneficiary on same day.          2014-08-15. Individual amounts: ₹4.8L-₹4.95L.
Individual amounts < ₹5L.         Aggregate: ₹24.2L. All below ₹5L threshold."
                                  severity: CRITICAL
                                  → Structuring pattern confirmed

Month 3 (day 3):                  FINDING created (auto-draft):
All 4 signals combined into       finding_type: STR_WARRANTED
one CASE with 59 related          narrative: "Network of 59 shell companies at
parties.                          Ashok Vihar branch, sharing directors/addresses,
                                  dormant-then-active pattern, rapid RTGS→SWIFT
                                  pass-through to Hong Kong, structured below
                                  threshold. Total flow: ₹47Cr in 21 days.
                                  Cites: CL_AML_FEMA_001, CL_AML_STRUCT_001,
                                  CL_AML_KYC_004. Evidence: EV001-EV089."
                                  reconciliation_passed: TRUE (all numerals verified)

Month 3 (day 5):                  ATTESTATION:
MLRO reviews and approves.        attestor_role: MLRO
STR filed with FIU-IND.           action: APPROVE → FILE
                                  "Reviewed. 59-entity mule network confirmed.
                                   STR filed: FIU-IND-STR-2014-08-XXXXX"

                                  TOTAL TIME FROM FIRST ANOMALY TO FILING: ~5 DAYS
                                  (Actual BoB case: went undetected for ~1 YEAR,
                                   total illicit flow: ₹6,100 crore)
```

**The fundamental difference:** The BoB branch operated for nearly a year because no system connected the dots across all 59 accounts. Each individual transaction, viewed alone, was below thresholds. Our schema's graph-based entity resolution (`PARTY_RELATIONSHIP` with shared directors/addresses → `match_cluster_id` grouping → bounded recursive CTE) would have identified the full network within days of the first shared-attribute edge being created.

The evidence chain — immutable, timestamped, re-verifiable via Time Travel — would have given FIU-IND an airtight case rather than the years-long forensic reconstruction that actually occurred.

---

## F. Appendices

### F.1 Entity-Relationship Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CORE SCHEMA                                 │
│                                                                      │
│  ┌─────────┐    owns     ┌─────────┐   debit/credit  ┌───────────┐ │
│  │  PARTY   │───────────▶│ ACCOUNT │◀───────────────▶│TRANSACTION│ │
│  │          │            │         │                  │           │ │
│  │ party_id │            │acct_id  │                  │ txn_id    │ │
│  │ kyc      │            │ balance │                  │ amount    │ │
│  │ pep_flag │            │ status  │                  │ channel   │ │
│  │ risk_rtg │            └─────────┘                  │ device_id │ │
│  └────┬─────┘                 │                       │ ip_addr   │ │
│       │                       │                       └───────────┘ │
│       │ graph edge            │ credit position                     │
│       ▼                       ▼                                     │
│  ┌───────────────┐      ┌──────────┐                                │
│  │PARTY_          │      │ EXPOSURE │                                │
│  │ RELATIONSHIP   │      │          │                                │
│  │                │      │ dpd      │                                │
│  │ type           │      │ sma_stg  │                                │
│  │ confidence     │      │ ecl_stg  │                                │
│  │ shared_attr    │      │ rwa      │                                │
│  └────────────────┘      └──────────┘                                │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                       DETECTION SCHEMA                               │
│                                                                      │
│  ┌─────────┐  escalates  ┌─────────┐  gathers   ┌──────────┐       │
│  │ SIGNAL  │────────────▶│  CASE   │───────────▶│ EVIDENCE │       │
│  │         │             │         │            │ (pointers)│       │
│  │ rule_id │             │ status  │            │ ref_type  │       │
│  │ typology│             │ owner   │            │ ref_id    │       │
│  │ severity│             │ sla     │            │ as_of_ts  │       │
│  └─────────┘             └────┬────┘            │ query_id  │       │
│                               │                 └──────────┘       │
└───────────────────────────────┼──────────────────────────────────────┘
                                │
┌───────────────────────────────┼──────────────────────────────────────┐
│                       COMPLIANCE SCHEMA  │                           │
│                               │                                      │
│                               ▼                                      │
│                         ┌───────────┐  generates  ┌──────────┐      │
│                         │  FINDING  │────────────▶│ ARTIFACT │      │
│                         │           │             │          │      │
│                         │ narrative │             │ str_draft│      │
│                         │ status    │◀──cites──── │ recon_ok │      │
│                         │ as_of_ts  │             └──────────┘      │
│                         └─────┬─────┘                                │
│                               │                                      │
└───────────────────────────────┼──────────────────────────────────────┘
                                │
┌───────────────────────────────┼──────────────────────────────────────┐
│                       POLICY SCHEMA      │                           │
│                               │                                      │
│  ┌──────────────┐  extracts  ┌▼────────────┐                        │
│  │POLICY_       │───────────▶│POLICY_      │  ◀──cited by FINDING   │
│  │ DOCUMENT     │            │ CLAUSE      │                        │
│  │              │            │             │                        │
│  │ pdf_path     │            │ metric      │                        │
│  │ issuer       │            │ operator    │                        │
│  │ effective_dt │            │ threshold   │                        │
│  └──────────────┘            │ effective_  │                        │
│                              │  from/to    │                        │
│                              └─────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                        AUDIT SCHEMA                                  │
│                                                                      │
│  ┌─────────────┐     ┌──────────────────┐    ┌──────────────┐       │
│  │ ATTESTATION │     │ GOVERNANCE_EVENT │    │ GROUND_TRUTH │       │
│  │ (append-only)│     │ (append-only)    │    │              │       │
│  │             │     │                  │    │ planted      │       │
│  │ attestor    │     │ event_type       │    │ typologies   │       │
│  │ action      │     │ entity_type      │    │ for P/R      │       │
│  │ sig_hash    │     │ old/new_value    │    │ measurement  │       │
│  └─────────────┘     └──────────────────┘    └──────────────┘       │
└──────────────────────────────────────────────────────────────────────┘
```

### F.2 Schema Privilege Matrix

| Role | CORE | DETECTION | POLICY | COMPLIANCE | AUDIT | STAGING |
|---|---|---|---|---|---|---|
| `INGESTION_SVC` | INSERT on TRANSACTION, PARTY, ACCOUNT | — | — | — | — | ALL |
| `DETECTION_SVC` | SELECT | INSERT on SIGNAL, CASE, EVIDENCE | SELECT | — | INSERT on GOV_EVENT | — |
| `POLICY_SVC` | — | — | SELECT, INSERT on POLICY_DOCUMENT | — | INSERT on GOV_EVENT | ALL |
| `AGENT_DRAFTER` | SELECT | SELECT | SELECT | INSERT on FINDING, ARTIFACT | INSERT on GOV_EVENT | — |
| `AGENT_REVIEWER` | SELECT | SELECT | SELECT | SELECT, UPDATE (reconciliation fields only) | INSERT on GOV_EVENT | — |
| `COMPLIANCE_ANALYST` | SELECT | SELECT, UPDATE (case assignment) | SELECT | SELECT, UPDATE (finding status: DRAFT→UNDER_REVIEW) | INSERT on ATTESTATION, GOV_EVENT | — |
| `MLRO` | SELECT | SELECT | SELECT, UPDATE (clause activation) | SELECT, UPDATE (finding status: UNDER_REVIEW→APPROVED→FILED) | INSERT on ATTESTATION, GOV_EVENT | — |
| `AUDITOR` | SELECT | SELECT | SELECT | SELECT | SELECT | SELECT |

Key constraints:
- **No role can both create and approve a finding** (segregation of duties).
- **`AGENT_DRAFTER` cannot write to `ATTESTATION`** — agents propose, humans attest.
- **`AUDITOR` is read-only everywhere** — can verify but not modify.
- **`INGESTION_SVC` has no access to DETECTION, POLICY, COMPLIANCE, or AUDIT** — ingestion is isolated.

### F.3 Glossary

| Term | Definition |
|---|---|
| **AML** | Anti-Money Laundering — regulatory framework to detect and prevent money laundering |
| **ATO** | Account Takeover — unauthorized access to a legitimate account |
| **CDD** | Customer Due Diligence — standard identity verification at onboarding |
| **CRAR** | Capital to Risk-Weighted Assets Ratio — Indian equivalent of Basel capital adequacy ratio |
| **CTR** | Cash Transaction Report — mandatory report for cash transactions above threshold (filed with FIU-IND) |
| **DPD** | Days Past Due — number of days a borrower is overdue on a payment |
| **ECL** | Expected Credit Loss — provisioning model under Ind AS 109 (Stage 1/2/3) |
| **EDD** | Enhanced Due Diligence — additional scrutiny for high-risk parties (PEPs, complex structures) |
| **Evidence-as-Pointers** | Architecture where evidence stores `(ref_id, as_of_ts, query_id)` references rather than copied values, enabling re-verification via Time Travel |
| **FIU-IND** | Financial Intelligence Unit – India — the agency receiving STR/CTR/other suspicious activity reports |
| **Hard Negative** | A planted entity that resembles a typology but is intentionally legitimate — tests false positive rate |
| **KYC** | Know Your Customer — identity verification process |
| **LCR** | Liquidity Coverage Ratio — high-quality liquid assets / net cash outflows under 30-day stress |
| **Maker-Checker** | Workflow requiring one person to create (make) and a different person to approve (check) |
| **MLRO** | Money Laundering Reporting Officer — designated officer responsible for AML compliance and filing |
| **Mule Ring** | Network of accounts used to receive and rapidly forward illicit funds, obscuring the money trail |
| **NPA** | Non-Performing Asset — loan where repayment is overdue beyond a threshold (typically 90 DPD) |
| **PEP** | Politically Exposed Person — individual with prominent public function (higher AML risk) |
| **PMLA** | Prevention of Money Laundering Act, 2002 — India's primary AML legislation |
| **Policy-as-Code** | Structured, effective-dated rules extracted from unstructured policy text and stored as queryable data |
| **RBI** | Reserve Bank of India — the central bank and primary financial regulator |
| **RWA** | Risk-Weighted Assets — assets weighted by credit risk for capital adequacy calculation |
| **SMA** | Special Mention Account — early warning classification before NPA (SMA-0/1/2) |
| **Smurfing** | Structuring transactions into sub-threshold amounts to avoid reporting requirements |
| **STR** | Suspicious Transaction Report — narrative report filed with FIU-IND when suspicious activity is identified |
| **Time Travel** | Snowflake feature allowing queries `AT(TIMESTAMP => ...)` to reproduce historical table state |

---

*End of Technical Architecture & Schema Specification.*
