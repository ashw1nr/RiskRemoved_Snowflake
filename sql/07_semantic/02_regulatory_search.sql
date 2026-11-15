-- ===========================================================================
-- 07_semantic / 02_regulatory_search.sql
-- ===========================================================================
-- Cortex Search over the governed policy clauses: the unstructured side of the
-- STR. GENERATE_AI_STR retrieves the clauses that match a case and hands their
-- text to the drafter, so the narrative cites the rule, not just its id.
--
-- The chunks are derived from GOVERNANCE.POLICY_CLAUSE (seeded by 05_policy),
-- one per clause in force: its source document, section and text. Rebuilt on
-- every deploy, so a clause change reaches search on the next run. The text is
-- background for prose only: REVIEW_STR still rejects any figure in the
-- narrative that is not a computed value, so a number in a clause cannot leak.
-- ===========================================================================
{% set G = fq.governance %}
{% set CH = G ~ "." ~ cortex.search_source_table %}

ALTER TABLE {{ CH }} SET CHANGE_TRACKING = TRUE;

DELETE FROM {{ CH }};

INSERT INTO {{ CH }} (chunk_id, clause_id, source_document, source_page, section_heading,
                      clause_text, effective_from, effective_to, supersedes_clause_id, document_type)
SELECT 'CHK-' || clause_id || '-' || clause_version,
       clause_id, source_document, source_page, section_heading,
       section_heading || '. ' || COALESCE(clause_text, 'Action required: ' || action_required || '.'),
       effective_from, effective_to, supersedes_clause_id,
       IFF(source_document ILIKE 'Internal%', 'INTERNAL_POLICY', 'REGULATION')
FROM {{ G }}.V_POLICY_CLAUSE_CURRENT;

CREATE OR REPLACE CORTEX SEARCH SERVICE {{ G }}.{{ cortex.search_service }}
    ON clause_text
    ATTRIBUTES clause_id, source_document, document_type
    WAREHOUSE = {{ wh.pipeline }}
    TARGET_LAG = '{{ cortex.search_target_lag }}'
    COMMENT = 'Regulatory clause retrieval for the AI STR (GENERATE_AI_STR). Built from POLICY_CLAUSE.'
AS
SELECT clause_text, clause_id, source_document, document_type, section_heading
FROM {{ CH }};
