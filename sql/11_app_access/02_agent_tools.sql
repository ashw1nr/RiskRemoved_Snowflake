-- ===========================================================================
-- 11_app_access / 02_agent_tools.sql
-- ===========================================================================
-- What GENERATE_AI_STR (sql/procedures/09_generate_ai_str.sql, owned by the
-- admin role, owner's rights) may use in the STR agent's database.
--
-- One direction only: the data admin role may call the agent's tools and read
-- their drafts and reviews. The agent role is granted NOTHING here and holds no
-- grant on the data database, so the agent still sees only the hashed evidence
-- package handed to PUT_EVIDENCE_PACKAGE (decision 3).
--
-- The agent schema is MANAGED ACCESS and its database is owned by ACCOUNTADMIN,
-- so ACCOUNTADMIN issues the grants. The tools are CREATE OR REPLACE ... COPY
-- GRANTS (agent_account/sql/03_tools.sql), so redeploying them keeps these.
-- ===========================================================================
{% set AG = cortex.agent_schema_fq %}
{% set ADM = role.admin %}
{% if AG %}
USE ROLE ACCOUNTADMIN;

GRANT USAGE ON DATABASE {{ cortex.agent_database }} TO ROLE {{ ADM }};
GRANT USAGE ON SCHEMA {{ AG }} TO ROLE {{ ADM }};
GRANT USAGE ON PROCEDURE {{ AG }}.PUT_EVIDENCE_PACKAGE(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARIANT) TO ROLE {{ ADM }};
GRANT USAGE ON PROCEDURE {{ AG }}.DRAFT_STR_NARRATIVE(VARCHAR, VARCHAR) TO ROLE {{ ADM }};
GRANT USAGE ON PROCEDURE {{ AG }}.REVIEW_STR_DRAFT(VARCHAR) TO ROLE {{ ADM }};
GRANT USAGE ON PROCEDURE {{ AG }}.RENDER_STR_PDF(VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO ROLE {{ ADM }};
GRANT SELECT ON TABLE {{ AG }}.AI_DRAFT TO ROLE {{ ADM }};
GRANT SELECT ON TABLE {{ AG }}.AI_REVIEW TO ROLE {{ ADM }};
GRANT SELECT, INSERT ON TABLE {{ AG }}.AGENT_RUN_LOG TO ROLE {{ ADM }};

USE ROLE {{ ADM }};
{% endif %}
