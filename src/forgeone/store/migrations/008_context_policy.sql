-- 008: the context policy of a goal (lean | default | rich), see context/policy.py.
ALTER TABLE goals ADD COLUMN IF NOT EXISTS context_policy TEXT NOT NULL DEFAULT 'default';

-- Context per policy: does more (or less) context pay off?
CREATE OR REPLACE VIEW v_context_policy AS
SELECT coalesce(a.context->>'policy', 'default') AS policy, a.kind AS stage, coalesce(a.model, a.actor) AS model,
       count(*)                                                     AS calls,
       avg((a.context->>'tokens_est')::float)                       AS tokens_est,
       avg(nullif(a.tokens_in, 0))                                  AS tokens_billed,
       avg(a.cost_usd)                                              AS mean_cost_usd,
       avg(CASE WHEN a.status = 'passed' THEN 1.0 ELSE 0.0 END)     AS pass_rate,
       avg(CASE WHEN coalesce((a.context->>'omitted')::int, 0) > 0 THEN 1.0 ELSE 0.0 END) AS omitted_rate
FROM actions a
WHERE a.context <> '{}'::jsonb AND a.status IN ('passed', 'failed')
GROUP BY 1, 2, 3;
