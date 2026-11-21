-- Variants on eval runs, richer per-run eval results, labels on any run, and the knowledge view.
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS variant_name TEXT;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS variant      JSONB NOT NULL DEFAULT '{}';
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS promoted     BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS cases        INTEGER;

ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS model              TEXT;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS stop_reason        TEXT;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS tool_calls         INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS tool_errors        INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS recovery_attempts  INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS recovery_successes INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS llm_calls          INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS llm_retries        INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS tokens_in          INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS tokens_out         INTEGER;
ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS tools_used         JSONB NOT NULL DEFAULT '{}';

-- A verdict on any run: from a human (forgeone label) or from the automatic checks.
CREATE TABLE IF NOT EXISTS run_labels (
    id         BIGSERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    label      TEXT NOT NULL CHECK (label IN ('good', 'bad')),
    source     TEXT NOT NULL DEFAULT 'human' CHECK (source IN ('human', 'checks')),
    note       TEXT,
    details    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_labels_run ON run_labels (run_id, created_at);

-- The knowledge base: which variant works for which kind of task, measured.
CREATE OR REPLACE VIEW v_knowledge AS
WITH per_case AS (
    SELECT r.agent, r.suite_version, r.variant_name, er.category, er.case_id, er.eval_run_id,
           bool_and(er.passed)                    AS passed_all,
           avg(er.passed::int)                    AS pass_rate,
           avg(er.cost_usd)                       AS cost_usd,
           avg(er.duration_ms)                    AS duration_ms,
           avg(er.tool_calls)                     AS tool_calls,
           sum(er.tool_errors)                    AS tool_errors,
           sum(er.recovery_attempts)              AS recovery_attempts,
           sum(er.recovery_successes)             AS recovery_successes,
           sum(cardinality(er.hard_failures))     AS hard_failures,
           count(*)                               AS runs
    FROM eval_results er JOIN eval_runs r USING (eval_run_id)
    GROUP BY 1, 2, 3, 4, 5, 6
)
SELECT agent, suite_version, variant_name, category,
       count(DISTINCT eval_run_id)      AS eval_runs,
       count(DISTINCT case_id)          AS cases,
       sum(runs)                        AS runs,
       avg(passed_all::int)             AS pass_k,
       avg(pass_rate)                   AS pass_rate,
       avg(cost_usd)                    AS mean_cost_usd,
       avg(duration_ms)                 AS mean_duration_ms,
       avg(tool_calls)                  AS mean_tool_calls,
       sum(tool_errors)                 AS tool_errors,
       sum(recovery_attempts)           AS recovery_attempts,
       sum(recovery_successes)          AS recovery_successes,
       sum(hard_failures)               AS hard_failures
FROM per_case
GROUP BY 1, 2, 3, 4;
