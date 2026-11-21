"""Read-only queries for the ForgeOne Control Room."""
from __future__ import annotations

import os

# A goal is identified by the hash of its full text; title = first meaningful line.
GOAL_KEY = "md5(coalesce(r.goal, r.name, 'untitled'))"

OVERVIEW = """
SELECT count(*)                                         AS builds,
       count(*) FILTER (WHERE status = 'ok')            AS passed,
       count(*) FILTER (WHERE status = 'error')         AS failed,
       count(*) FILTER (WHERE status = 'running')       AS running,
       coalesce(sum(total_input_tokens + total_output_tokens), 0) AS tokens,
       count(DISTINCT md5(coalesce(goal, name, 'untitled')))      AS goals,
       (SELECT count(*) FROM events WHERE level IN ('ERROR','CRITICAL')
          AND ts > now() - interval '24 hours')         AS errors_24h
FROM runs
"""

GOALS = f"""
WITH g AS (
  SELECT {GOAL_KEY} AS key, r.*
  FROM runs r
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
WHERE {GOAL_KEY} = %(key)s
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

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=5) as conn:
        return conn.execute(sql, params or {}).fetchall()


def one(sql: str, params: dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
