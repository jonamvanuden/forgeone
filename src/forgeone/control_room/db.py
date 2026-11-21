"""Read-only queries for the ForgeOne Control Room."""
from __future__ import annotations

from ..config import settings

# A goal is identified by the hash of its full text; title = first meaningful line.
GOAL_KEY = "md5(coalesce(r.goal, r.name, 'untitled'))"
# Builds are runs of the builder (name 'forgeone.build'); agent runs ('agent.run') are shown on the
# Quality page instead, so build and goal queries below filter on the run name.

OVERVIEW = """
SELECT count(*)                                         AS builds,
       count(*) FILTER (WHERE status = 'ok')            AS passed,
       count(*) FILTER (WHERE status = 'error')         AS failed,
       count(*) FILTER (WHERE status = 'running')       AS running,
       coalesce(sum(total_input_tokens + total_output_tokens), 0) AS tokens,
       count(DISTINCT md5(coalesce(goal, name, 'untitled')))      AS goals,
       (SELECT count(*) FROM events WHERE level IN ('ERROR','CRITICAL')
          AND ts > now() - interval '24 hours')         AS errors_24h
FROM runs WHERE coalesce(name, 'forgeone.build') = 'forgeone.build'
"""

GOALS = f"""
WITH g AS (
  SELECT {GOAL_KEY} AS key, r.*
  FROM runs r WHERE coalesce(r.name, 'forgeone.build') = 'forgeone.build'
)
SELECT key,
       (array_agg(goal ORDER BY started_at DESC))[1]           AS goal,
       (array_agg(name ORDER BY started_at DESC))[1]           AS name,
       count(*)                                                AS builds,
       count(*) FILTER (WHERE status = 'ok')                   AS passed,
       count(*) FILTER (WHERE status = 'error')                AS failed,
       count(*) FILTER (WHERE status = 'running')              AS running,
       max(started_at)                                         AS last_build,
       min(started_at)                                         AS first_build,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS median_ms,
       coalesce(sum(total_input_tokens + total_output_tokens), 0) AS tokens,
       (SELECT json_agg(s ORDER BY s.started_at) FROM (
            SELECT run_id, status, duration_ms, started_at FROM g g2
            WHERE g2.key = g.key ORDER BY started_at DESC LIMIT 60) s) AS strip
FROM g
GROUP BY key
ORDER BY max(started_at) DESC
"""

GOAL_RUNS = f"""
SELECT r.run_id, r.status, r.started_at, r.ended_at, r.duration_ms, r.llm_calls,
       r.total_input_tokens, r.total_output_tokens, r.model, r.error,
       (SELECT max(iteration) FROM events e WHERE e.run_id = r.run_id) AS iterations
FROM runs r
WHERE {GOAL_KEY} = %(key)s AND coalesce(r.name, 'forgeone.build') = 'forgeone.build'
ORDER BY r.started_at DESC
LIMIT 500
"""

GOAL_SPECS = f"""
SELECT a.id, a.run_id, a.ts, a.name, a.body
FROM artifacts a JOIN runs r USING (run_id)
WHERE {GOAL_KEY} = %(key)s AND a.kind = 'spec'
ORDER BY a.ts
"""

RUN = f"SELECT r.*, {GOAL_KEY} AS goal_key FROM runs r WHERE r.run_id = %(run_id)s"

RUN_NODES = """
SELECT node, iteration, event,
       ts - make_interval(secs => coalesce((fields->>'duration_ms')::numeric, 0) / 1000.0) AS started,
       ts AS ended,
       (fields->>'duration_ms')::int AS duration_ms,
       fields->>'error' AS error
FROM events
WHERE run_id = %(run_id)s AND event IN ('node.end', 'node.error')
ORDER BY ts, id
"""

RUN_CALLS = """
SELECT id, ts, node, iteration, model, status, latency_ms, input_tokens, output_tokens,
       stop_reason, error
FROM llm_calls WHERE run_id = %(run_id)s ORDER BY ts, id
"""

RUN_DECISIONS = """
SELECT ts, node, iteration, decision, reason, details
FROM decisions WHERE run_id = %(run_id)s ORDER BY ts, id
"""

RUN_ARTIFACTS = """
SELECT id, ts, node, iteration, kind, name, body
FROM artifacts WHERE run_id = %(run_id)s ORDER BY ts, id
"""

PREVIOUS_ARTIFACT = """
SELECT run_id, ts, body FROM artifacts
WHERE kind = %(kind)s AND name IS NOT DISTINCT FROM %(name)s AND ts < %(ts)s
ORDER BY ts DESC LIMIT 1
"""

RUN_EVENTS = """
SELECT ts, level, event, node, iteration, fields
FROM events WHERE run_id = %(run_id)s ORDER BY ts, id
"""

CALL = "SELECT * FROM llm_calls WHERE id = %(id)s"

ERRORS = """
SELECT e.ts, e.run_id, e.node, e.event,
       e.fields->>'error' AS error,
       coalesce(e.fields->>'error_type', split_part(e.fields->>'error', ':', 1)) AS error_type,
       e.fields->>'traceback' AS traceback
FROM events e
WHERE e.level IN ('ERROR', 'CRITICAL')
ORDER BY e.ts DESC LIMIT 200
"""


def query(sql: str, params: dict | None = None) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings().database_url, row_factory=dict_row, connect_timeout=5) as conn:
        return conn.execute(sql, params or {}).fetchall()


def one(sql: str, params: dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


# ------------------------------------------------------------------ overview v2
WEEK_KPIS = """
SELECT
  count(*) FILTER (WHERE started_at > now() - interval '7 days')                         AS builds_7d,
  count(*) FILTER (WHERE started_at <= now() - interval '7 days'
                     AND started_at > now() - interval '14 days')                        AS builds_prev,
  count(*) FILTER (WHERE status = 'ok' AND started_at > now() - interval '7 days')       AS passed_7d,
  count(*) FILTER (WHERE status IN ('ok','error') AND started_at > now() - interval '7 days') AS finished_7d,
  count(*) FILTER (WHERE status = 'ok' AND started_at <= now() - interval '7 days'
                     AND started_at > now() - interval '14 days')                        AS passed_prev,
  count(*) FILTER (WHERE status IN ('ok','error') AND started_at <= now() - interval '7 days'
                     AND started_at > now() - interval '14 days')                        AS finished_prev,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)
      FILTER (WHERE status = 'ok' AND started_at > now() - interval '7 days')            AS median_ms_7d,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)
      FILTER (WHERE status = 'ok' AND started_at <= now() - interval '7 days'
                AND started_at > now() - interval '14 days')                             AS median_ms_prev,
  count(*) FILTER (WHERE status = 'running')                                             AS running,
  max(started_at)                                                                        AS last_build
FROM runs WHERE coalesce(name, 'forgeone.build') = 'forgeone.build'
"""

# tokens grouped so cost can be priced per model (Haiku is priced higher above 100k input)
USAGE_BY_DAY = """
SELECT date_trunc('day', ts)::date AS day, model, (input_tokens > 100000) AS long_prompt,
       count(*) AS calls, coalesce(sum(input_tokens), 0) AS tin, coalesce(sum(output_tokens), 0) AS tout
FROM llm_calls
WHERE ts > now() - interval '14 days'
GROUP BY 1, 2, 3
"""

USAGE_BY_MODEL = """
SELECT model, (input_tokens > 100000) AS long_prompt, count(*) AS calls,
       count(*) FILTER (WHERE status = 'error') AS errors,
       coalesce(sum(input_tokens), 0) AS tin, coalesce(sum(output_tokens), 0) AS tout,
       round(avg(latency_ms)) AS avg_latency_ms
FROM llm_calls
WHERE ts > now() - interval '30 days'
GROUP BY 1, 2
"""

BUILDS_BY_DAY = """
SELECT d::date AS day,
       count(r.run_id) FILTER (WHERE r.status = 'ok')      AS passed,
       count(r.run_id) FILTER (WHERE r.status = 'error')   AS failed,
       count(r.run_id) FILTER (WHERE r.status = 'running') AS running
FROM generate_series(date_trunc('day', now()) - interval '13 days', date_trunc('day', now()), interval '1 day') d
LEFT JOIN runs r ON date_trunc('day', r.started_at) = d AND coalesce(r.name, 'forgeone.build') = 'forgeone.build'
GROUP BY d ORDER BY d
"""

STEP_STATS = """
SELECT node,
       count(*)                                                    AS runs,
       round(avg((fields->>'duration_ms')::numeric))               AS avg_ms,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY (fields->>'duration_ms')::numeric)) AS p95_ms,
       count(*) FILTER (WHERE event = 'node.error')                AS failed
FROM events
WHERE event IN ('node.end', 'node.error') AND ts > now() - interval '30 days' AND node IS NOT NULL
GROUP BY node ORDER BY avg_ms DESC NULLS LAST
"""

RECENT_RUNS = f"""
SELECT r.run_id, r.status, r.started_at, r.ended_at, r.duration_ms, r.error, r.goal, r.name,
       {GOAL_KEY} AS goal_key
FROM runs r WHERE coalesce(r.name, 'forgeone.build') = 'forgeone.build' ORDER BY r.started_at DESC LIMIT 12
"""

RECENT_SPECS = """
SELECT a.id, a.run_id, a.ts, a.name, a.body FROM artifacts a
WHERE a.kind = 'spec' AND a.ts > now() - interval '30 days'
ORDER BY a.name, a.ts
"""

COST_BY_RUN = """
SELECT run_id, model, (input_tokens > 100000) AS long_prompt,
       coalesce(sum(input_tokens), 0) AS tin, coalesce(sum(output_tokens), 0) AS tout
FROM llm_calls WHERE run_id = ANY(%(run_ids)s)
GROUP BY 1, 2, 3
"""

COST_BY_GOAL = f"""
SELECT {GOAL_KEY} AS key, c.model, (c.input_tokens > 100000) AS long_prompt,
       coalesce(sum(c.input_tokens), 0) AS tin, coalesce(sum(c.output_tokens), 0) AS tout
FROM llm_calls c JOIN runs r USING (run_id)
WHERE coalesce(r.name, 'forgeone.build') = 'forgeone.build'
GROUP BY 1, 2, 3
"""


# ------------------------------------------------------------------ quality (evals + labels)
KNOWLEDGE = """
SELECT k.* FROM v_knowledge k
JOIN (SELECT DISTINCT ON (agent) agent, suite_version FROM eval_runs ORDER BY agent, started_at DESC) latest
  USING (agent, suite_version)
ORDER BY k.agent, k.category, k.variant_name
"""

EVAL_RUNS = """
SELECT eval_run_id, agent, variant_name, model, agent_version, suite_version, repeats, cases, status,
       started_at, ended_at, promoted, gate, scorecard
FROM eval_runs ORDER BY started_at DESC LIMIT 50
"""

AGENT_RUNS = """
SELECT r.run_id, r.started_at, r.status, r.duration_ms, r.llm_calls, r.meta->>'agent' AS agent,
       r.meta->>'variant' AS variant, r.meta->>'case_id' AS case_id, r.meta->>'mode' AS mode,
       r.meta->>'task' AS task, r.meta->>'eval_run_id' AS eval_run_id,
       (SELECT l.label FROM run_labels l WHERE l.run_id = r.run_id ORDER BY (l.source = 'human') DESC, l.created_at DESC LIMIT 1) AS label,
       (SELECT (e.fields->>'cost_usd')::float FROM events e WHERE e.run_id = r.run_id AND e.event = 'agent.end' LIMIT 1) AS runtime_cost,
       (SELECT l.source FROM run_labels l WHERE l.run_id = r.run_id ORDER BY (l.source = 'human') DESC, l.created_at DESC LIMIT 1) AS label_source
FROM runs r WHERE r.name = 'agent.run'
ORDER BY r.started_at DESC LIMIT 50
"""

AGENT_RUN_COSTS = """
SELECT run_id, model, (input_tokens > 100000) AS long_prompt,
       coalesce(sum(input_tokens), 0) AS tin, coalesce(sum(output_tokens), 0) AS tout
FROM llm_calls WHERE run_id = ANY(%(run_ids)s) GROUP BY 1, 2, 3
"""

RUN_LABELS = """
SELECT label, source, note, created_at FROM run_labels WHERE run_id = %(run_id)s ORDER BY created_at DESC
"""

RUN_TOOL_CALLS = """
SELECT ts, event, fields->>'tool' AS tool, fields->'args' AS args, (fields->>'ok')::boolean AS ok,
       fields->>'error' AS error, (fields->>'duration_ms')::int AS duration_ms,
       (fields->>'recovery_attempt')::boolean AS recovery_attempt
FROM events WHERE run_id = %(run_id)s AND event IN ('tool.call', 'tool.error') ORDER BY ts, id
"""
