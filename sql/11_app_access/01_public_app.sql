-- ===========================================================================
-- 11_app_access / 01_public_app.sql
-- ===========================================================================
-- What the hosted app (Streamlit Community Cloud) may do, as a role.
--
-- The hosted app connects as one service user (conf/base.yml:app.public_user,
-- created by `riskcopilot app public-access` with a key pair, no password).
-- Its role can:
--   * read the demo: curated, governance and evidence through the service role
--     it inherits, the audit log directly, plus RAW_ACCOUNTS;
--   * run the case tools: USAGE on the procedures, with the service role's
--     INSERT / UPDATE on the evidence tables;
--   * ask Cortex Analyst: SELECT on the semantic view.
-- It does NOT hold the compliance officer role, so ATTEST_STR (caller's rights)
-- refuses it, and logs the refusal. Signing on the hosted app uses a second
-- service user (app.public_officer_user) whose role is this one plus the
-- compliance role; the app switches to it only while the officer is signed in.
-- Neither can read GROUND_TRUTH. The app's recall / precision come from
-- V_EVAL_SCORES, a secure view of run-level aggregates (obligation 4: no label
-- is reachable outside the admin role).
--
-- Applied after `procedures` and `semantic`, whose objects it grants on. The
-- procedures are CREATE OR REPLACE ... COPY GRANTS, so redeploying them keeps
-- these grants.
-- ===========================================================================
{% set T = tool %}
{% set PUB = role.public_app %}
{% set OFF = role.public_officer %}

-- Run-level scores only: no per-party, per-transaction or label value leaves.
CREATE OR REPLACE SECURE VIEW {{ fq.curated }}.{{ view.eval_scores }}
    COMMENT = 'Detection scores per EVAL_RUN, aggregates only. The app reads this, never GROUND_TRUTH.'
AS
SELECT run_id, suite_name, profile, git_commit, generation_seed, passed, finished_at,
       metrics:detection AS detection,
       metrics:hero:status::STRING AS hero_status
FROM {{ fq.ground_truth }}.EVAL_RUN;

{% if PUB %}
USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS {{ PUB }}
    COMMENT = 'The hosted demo app ({{ profile }}): reads the demo, runs the case tools. No GROUND_TRUTH.';
GRANT ROLE {{ PUB }} TO ROLE {{ role.admin }};
-- Revoked, not just not granted: earlier deploys gave it, and CREATE ... IF NOT
-- EXISTS keeps an existing role's grants.
REVOKE ROLE {{ role.compliance_officer }} FROM ROLE {{ PUB }};
GRANT ROLE {{ role.service }} TO ROLE {{ PUB }};
GRANT USAGE ON WAREHOUSE {{ app.warehouse }} TO ROLE {{ PUB }};

{% if OFF %}
CREATE ROLE IF NOT EXISTS {{ OFF }}
    COMMENT = 'The hosted demo app''s Principal Officer ({{ profile }}): the public app role plus the compliance role, so ATTEST_STR accepts it.';
GRANT ROLE {{ OFF }} TO ROLE {{ role.admin }};
GRANT ROLE {{ PUB }} TO ROLE {{ OFF }};
GRANT ROLE {{ role.compliance_officer }} TO ROLE {{ OFF }};
GRANT USAGE ON WAREHOUSE {{ app.warehouse }} TO ROLE {{ OFF }};
{% endif %}

USE ROLE {{ role.admin }};

GRANT USAGE ON SCHEMA {{ fq.raw }} TO ROLE {{ PUB }};
GRANT SELECT ON TABLE {{ fq.raw }}.RAW_ACCOUNTS TO ROLE {{ PUB }};
-- The Governance and Ask pages read these; the service role can only INSERT.
GRANT SELECT ON TABLE {{ fq.audit }}.AGENT_ACTION_LOG TO ROLE {{ PUB }};
GRANT SELECT ON TABLE {{ fq.audit }}.CHAT_MESSAGE TO ROLE {{ PUB }};
GRANT SELECT ON TABLE {{ fq.audit }}.DATA_QUALITY_LOG TO ROLE {{ PUB }};
GRANT SELECT ON VIEW {{ fq.curated }}.{{ view.eval_scores }} TO ROLE {{ PUB }};
GRANT SELECT ON SEMANTIC VIEW {{ fq.curated }}.{{ cortex.semantic_view }} TO ROLE {{ PUB }};

GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.gather_evidence }}(VARCHAR) TO ROLE {{ PUB }};
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.draft_str }}(VARCHAR, BOOLEAN) TO ROLE {{ PUB }};
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.review_str }}(VARCHAR) TO ROLE {{ PUB }};
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.attest_str }}(VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO ROLE {{ PUB }};
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.reset_cases }}(VARCHAR, BOOLEAN) TO ROLE {{ PUB }};
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.sim_control }}(VARCHAR, VARCHAR, VARCHAR) TO ROLE {{ PUB }};
{% if cortex.agent_schema_fq %}
-- The AI STR button: the whole agent chain runs inside the procedure (owner's
-- rights). The public role still holds nothing on SUBMIT_AI_DRAFT or the agent.
GRANT USAGE ON PROCEDURE {{ fq.evidence }}.{{ T.generate_ai_str }}(VARCHAR) TO ROLE {{ PUB }};
{% endif %}
{% endif %}
