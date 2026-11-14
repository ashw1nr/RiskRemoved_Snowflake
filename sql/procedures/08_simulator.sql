-- ===========================================================================
-- procedures / 08_simulator.sql
-- ===========================================================================
-- SIM_CONTROL(action, party_id, via): the live simulator, driven from the app.
--
-- Every leg is loaded once. RAW_TRANSACTIONS.held_back says whether a leg has
-- "arrived": DT_TRANSACTIONS_ENRICHED reads only arrived legs, so everything
-- downstream -- signals, cases, the semantic view, the app -- sees the ledger
-- grow as legs are released. No file, no laptop, no PUT / COPY: a tick is one
-- UPDATE, then a refresh and a route, all inside Snowflake.
--
--   STATUS        held / arrived counts, the next booking time, the task state
--   ARM           clear the case chain, hold back every leg booked at or after
--                 the cut (replay.hold_back_from), refresh, route the baseline
--   TICK          release the next sim_step of booking time (whole timestamps,
--                 at most batch_legs legs), refresh, route
--   INJECT        release every held leg of one party (the hero by default), so
--                 its structuring burst lands at once, then refresh and route
--   RELEASE_ALL   release everything still held, refresh, route
--   START / STOP  resume / suspend the task that TICKs once a minute; the task
--                 stops itself when nothing is left to release
--
-- held_back is decided by booking time alone, never by a label, so releasing
-- reveals nothing about which legs are illicit. Releasing everything restores
-- the full load exactly (raw_fingerprint leaves the arrival columns out).
--
-- ARM, like RESET_CASES, breaks the evidence chain's append-only rule on
-- purpose: it is a demo control. Every action except STATUS writes one OPERATOR
-- row to AGENT_ACTION_LOG.
-- ===========================================================================
{% set T = tool %}
{% set EV = fq.evidence %}
{% set TXN = fq.raw ~ ".RAW_TRANSACTIONS" %}
{% set TASK = EV ~ "." ~ T.sim_task %}
{% set ROUTE = EV ~ "." ~ stream_task.route_signals_procedure %}
{% set leaves = fq.curated ~ "." ~ dt.signals_fast.name ~ ", " ~ fq.curated ~ "." ~ dt.txn_daily_agg.name ~ ", " ~ fq.curated ~ "." ~ dt.party_relationship.name %}

CREATE OR REPLACE PROCEDURE {{ EV }}.{{ T.sim_control }}(
    ACTION VARCHAR, PARTY_ID VARCHAR DEFAULT NULL, VIA VARCHAR DEFAULT 'SQL'
)
    COPY GRANTS
    RETURNS VARIANT
    LANGUAGE SQL
    COMMENT = 'Live simulator: ARM / TICK / INJECT / RELEASE_ALL / START / STOP / STATUS over RAW_TRANSACTIONS.held_back.'
    EXECUTE AS OWNER
AS
$$
DECLARE
    bad_action EXCEPTION (-20181, 'SIM_CONTROL: action must be STATUS, ARM, TICK, INJECT, RELEASE_ALL, START or STOP');
    no_cut EXCEPTION (-20182, 'SIM_CONTROL: replay.hold_back_from is not set for this profile, so there is nothing to hold back');
    no_party EXCEPTION (-20183, 'SIM_CONTROL: INJECT needs a party_id');
    act VARCHAR;
    started TIMESTAMP_LTZ;
    now_ts TIMESTAMP_NTZ;
    cut TIMESTAMP_NTZ;
    next_ts TIMESTAMP_NTZ;
    window_end TIMESTAMP_NTZ;
    batch_end TIMESTAMP_NTZ;
    released INTEGER DEFAULT 0;
    held INTEGER;
    arrived INTEGER;
    task_state VARCHAR;
    full_scan BOOLEAN;
    routed VARIANT DEFAULT NULL;
    tasks RESULTSET;
    result VARIANT;
BEGIN
    started := CURRENT_TIMESTAMP();
    now_ts := CURRENT_TIMESTAMP()::TIMESTAMP_NTZ;
    act := UPPER(TRIM(action));
    IF (act NOT IN ('STATUS', 'ARM', 'TICK', 'INJECT', 'RELEASE_ALL', 'START', 'STOP')) THEN
        RAISE bad_action;
    END IF;

    IF (act = 'ARM') THEN
        cut := {{ sim.hold_back_from }};
        IF (cut IS NULL) THEN
            RAISE no_cut;
        END IF;
        ALTER TASK IF EXISTS {{ TASK }} SUSPEND;
        -- A clean live run: the workflow, then the chain, children first.
        BEGIN TRANSACTION;
        DELETE FROM {{ EV }}.ATTESTATION;
        DELETE FROM {{ EV }}.REVIEW;
        DELETE FROM {{ EV }}.ARTIFACT;
        DELETE FROM {{ EV }}.FINDING;
        DELETE FROM {{ EV }}.EVIDENCE_QUERY_LOG;
        DELETE FROM {{ EV }}.EVIDENCE;
        DELETE FROM {{ EV }}.CASE_SIGNAL;
        DELETE FROM {{ EV }}.CASE_RECORD;
        DELETE FROM {{ EV }}.SIGNAL;
        UPDATE {{ TXN }} SET held_back = FALSE, released_at = NULL WHERE held_back OR released_at IS NOT NULL;
        UPDATE {{ TXN }} SET held_back = TRUE WHERE txn_timestamp >= :cut;
        COMMIT;
    ELSEIF (act = 'TICK') THEN
        next_ts := (SELECT MIN(txn_timestamp) FROM {{ TXN }} WHERE held_back);
        IF (next_ts IS NOT NULL) THEN
            window_end := DATEADD('second', {{ sim.step_seconds }}, :next_ts);
            -- Cut early at a whole timestamp so one busy step cannot release a huge
            -- batch, and the two legs of a pair (one timestamp, M36) stay together.
            batch_end := (
                SELECT MAX(txn_timestamp) FROM (
                    SELECT txn_timestamp FROM {{ TXN }} WHERE held_back
                    ORDER BY txn_timestamp LIMIT {{ sim.batch_legs }}
                )
            );
            IF (DATEADD('second', 1, :batch_end) < :window_end) THEN
                window_end := DATEADD('second', 1, :batch_end);
            END IF;
            UPDATE {{ TXN }} SET held_back = FALSE, released_at = :now_ts
            WHERE held_back AND txn_timestamp < :window_end;
            released := SQLROWCOUNT;
        END IF;
    ELSEIF (act = 'INJECT') THEN
        IF (party_id IS NULL OR LENGTH(TRIM(party_id)) = 0) THEN
            RAISE no_party;
        END IF;
        UPDATE {{ TXN }} SET held_back = FALSE, released_at = :now_ts
        WHERE held_back
          AND account_id IN (SELECT account_id FROM {{ fq.raw }}.ID_MAP_ACCOUNT WHERE party_id = TRIM(:party_id));
        released := SQLROWCOUNT;
    ELSEIF (act = 'RELEASE_ALL') THEN
        UPDATE {{ TXN }} SET held_back = FALSE, released_at = :now_ts WHERE held_back;
        released := SQLROWCOUNT;
    ELSEIF (act = 'START') THEN
        ALTER TASK {{ TASK }} RESUME;
    ELSEIF (act = 'STOP') THEN
        ALTER TASK {{ TASK }} SUSPEND;
    END IF;

    -- Anything that changed what has arrived is refreshed and routed now, so the
    -- app shows the new legs and cases without waiting for a target lag.
    IF (act IN ('ARM', 'TICK', 'INJECT', 'RELEASE_ALL') AND (act = 'ARM' OR released > 0)) THEN
        ALTER DYNAMIC TABLE {{ leaves }} REFRESH;
        -- ARM routes with a full scan: the chain was just emptied, so the baseline
        -- is every signal the arrived legs raise.
        full_scan := (act = 'ARM');
        CALL {{ ROUTE }}(:full_scan) INTO :routed;
    END IF;

    held := (SELECT COUNT_IF(held_back) FROM {{ TXN }});
    arrived := (SELECT COUNT_IF(NOT held_back) FROM {{ TXN }});
    next_ts := (SELECT MIN(txn_timestamp) FROM {{ TXN }} WHERE held_back);
    -- Read SHOW's own result set: RESULT_SCAN(LAST_QUERY_ID()) here came back
    -- NULL on the hosted app ("Statement NULL not found") and took the Overview
    -- page down. The task's state is a display detail, so it never fails a call.
    BEGIN
        tasks := (SHOW TASKS LIKE '{{ T.sim_task }}' IN SCHEMA {{ EV }});
        LET c CURSOR FOR tasks;
        FOR t IN c DO
            task_state := t."state";
        END FOR;
    EXCEPTION
        WHEN OTHER THEN
            task_state := 'unknown';
    END;
    -- The task stops itself once the ledger has fully arrived.
    IF (held = 0 AND task_state = 'started') THEN
        ALTER TASK {{ TASK }} SUSPEND;
        task_state := 'suspended';
    END IF;

    result := OBJECT_CONSTRUCT_KEEP_NULL(
        'action', act,
        'released', IFF(released > 0, released, 0),
        'held_back', held,
        'arrived', arrived,
        'next_booking', next_ts,
        'task', task_state,
        'routed', routed
    );
    IF (act <> 'STATUS') THEN
        INSERT INTO {{ fq.audit }}.AGENT_ACTION_LOG (
            log_id, session_id, turn_number, agent_mode, invoked_by, snowflake_user, user_role,
            tools_invoked, tool_arguments, model_version, prompt_version, latency_ms,
            rows_touched, outcome
        )
        SELECT 'LOG-' || UUID_STRING(), CURRENT_SESSION()::VARCHAR,
               (SELECT COUNT(*) + 1 FROM {{ fq.audit }}.AGENT_ACTION_LOG WHERE session_id = CURRENT_SESSION()::VARCHAR),
               'OPERATOR', 'USER', CURRENT_USER(), CURRENT_ROLE(),
               ARRAY_CONSTRUCT('{{ T.sim_control }}'),
               OBJECT_CONSTRUCT('action', :act, 'party_id', :party_id, 'via', :via, 'result', :result),
               'n/a', 'n/a', DATEDIFF('millisecond', :started, CURRENT_TIMESTAMP()),
               ABS(:released), 'SUCCESS';
    END IF;
    RETURN result;
EXCEPTION
    WHEN OTHER THEN
        ROLLBACK;
        RAISE;
END;
$$;

-- --------------------------------------------------------------------------
-- The tick task. Created suspended; START / STOP resume and suspend it. One
-- tick a minute releases sim_step of booking time, so the dev ledger's held
-- days arrive over a few minutes on screen.
-- --------------------------------------------------------------------------
ALTER TASK IF EXISTS {{ TASK }} SUSPEND;

CREATE OR ALTER TASK {{ TASK }}
    WAREHOUSE = {{ wh.pipeline }}
    SCHEDULE = '1 MINUTE'
    COMMENT = 'Live simulator: one SIM_CONTROL TICK a minute while started.'
AS
EXECUTE IMMEDIATE $$
BEGIN
    LET counts VARIANT;
    CALL {{ EV }}.{{ T.sim_control }}('TICK', NULL, 'TASK') INTO :counts;
    LET counts_json VARCHAR := TO_JSON(:counts);
    CALL SYSTEM$SET_RETURN_VALUE(:counts_json);
END;
$$;
