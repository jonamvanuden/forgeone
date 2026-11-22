-- 007: context management.
-- Prompt caching: how many input tokens were read from / written to the cache (priced differently).
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS cache_read_tokens  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER NOT NULL DEFAULT 0;
-- The context manifest of the model call behind an action: sections, estimated tokens, cuts,
-- what was excluded by rule; for agent runs also how the conversation was kept short.
ALTER TABLE actions ADD COLUMN IF NOT EXISTS context JSONB NOT NULL DEFAULT '{}';

-- How much context each kind of call gets, how often it is cut, and whether that hurts.
CREATE OR REPLACE VIEW v_context AS
SELECT a.kind AS stage, coalesce(a.model, a.actor) AS model,
       count(*)                                                        AS calls,
       avg((a.context->>'tokens_est')::float)                          AS tokens_est,
       avg(nullif(a.tokens_in, 0))                                     AS tokens_billed,
       avg(CASE WHEN (a.context->>'cut')::int > 0 THEN 1.0 ELSE 0.0 END) AS cut_rate,
       avg(CASE WHEN (a.context->>'cut')::int > 0 AND a.status = 'passed' THEN 1.0
                WHEN (a.context->>'cut')::int > 0 THEN 0.0 END)          AS pass_rate_when_cut,
       avg(CASE WHEN coalesce((a.context->>'cut')::int, 0) = 0 AND a.status = 'passed' THEN 1.0
                WHEN coalesce((a.context->>'cut')::int, 0) = 0 THEN 0.0 END) AS pass_rate_uncut
FROM actions a
WHERE a.context <> '{}'::jsonb AND a.status IN ('passed', 'failed')
GROUP BY 1, 2;
