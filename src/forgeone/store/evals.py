"""Persistence for eval runs, eval results and run labels. No-op without DATABASE_URL."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..config import settings


def _conn():
    import psycopg

    return psycopg.connect(settings().database_url)


def _jsonb(obj: Any):
    from psycopg.types.json import Jsonb

    return Jsonb(json.loads(json.dumps(obj, default=str)))


def enabled() -> bool:
    return bool(settings().database_url)


def create_eval_run(eval_run_id: str, agent: str, agent_version: str, suite_version: str, variant: dict,
                    repeats: int, cases: int, meta: dict) -> None:
    if not enabled():
        return
    with _conn() as c:
        c.execute("""INSERT INTO eval_runs (eval_run_id, agent, agent_version, suite_version, model, repeats,
                       variant_name, variant, cases, meta)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                  (eval_run_id, agent, agent_version, suite_version, variant.get("model"), repeats,
                   variant.get("name"), _jsonb(variant), cases, _jsonb(meta)))


def add_eval_result(eval_run_id: str, result, trace) -> None:
    if not enabled():
        return
    attempts, successes = trace.recoveries
    with _conn() as c:
        c.execute("""INSERT INTO eval_results (eval_run_id, case_id, category, repeat, passed, hard_failures,
                       checks, judge_scores, failure_tags, cost_usd, duration_ms, trace_run_id, model, stop_reason,
                       tool_calls, tool_errors, recovery_attempts, recovery_successes, llm_calls, llm_retries,
                       tokens_in, tokens_out, tools_used)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                     ON CONFLICT (eval_run_id, case_id, repeat) DO NOTHING""",
                  (eval_run_id, result.case_id, result.category, result.repeat, result.passed,
                   result.hard_failures, _jsonb([c.model_dump() for c in result.checks]),
                   _jsonb(result.judge_scores), result.failure_tags, result.cost_usd, result.duration_ms,
                   trace.run_id, trace.model, trace.stop_reason, len(trace.tool_calls), trace.tool_errors,
                   attempts, successes, trace.llm_calls, trace.llm_retries, trace.tokens_in, trace.tokens_out,
                   _jsonb(dict(Counter(t.name for t in trace.tool_calls)))))


def finish_eval_run(eval_run_id: str, status: str, scorecard: dict | None, gate: dict | None, promoted: bool) -> None:
    if not enabled():
        return
    with _conn() as c:
        if promoted:  # one champion per agent + suite version
            c.execute("""UPDATE eval_runs SET promoted = false WHERE promoted AND (agent, suite_version) =
                         (SELECT agent, suite_version FROM eval_runs WHERE eval_run_id = %s)""", (eval_run_id,))
        c.execute("""UPDATE eval_runs SET status = %s, ended_at = now(), scorecard = %s, gate = %s, promoted = %s
                     WHERE eval_run_id = %s""",
                  (status, _jsonb(scorecard) if scorecard else None, _jsonb(gate) if gate else None,
                   promoted, eval_run_id))


def champion(agent: str, suite_version: str) -> tuple[str, dict] | None:
    """(eval_run_id, scorecard) of the current champion for this agent and suite version."""
    if not enabled():
        return None
    with _conn() as c:
        row = c.execute("""SELECT eval_run_id, scorecard FROM eval_runs
                           WHERE agent = %s AND suite_version = %s AND promoted AND scorecard IS NOT NULL
                           ORDER BY ended_at DESC LIMIT 1""", (agent, suite_version)).fetchone()
    return (row[0], row[1]) if row else None


def add_label(run_id: str, label: str, source: str = "human", note: str | None = None, details: dict | None = None) -> None:
    if label not in ("good", "bad"):
        raise ValueError("label must be 'good' or 'bad'")
    with _conn() as c:
        if not c.execute("SELECT 1 FROM runs WHERE run_id = %s", (run_id,)).fetchone():
            raise KeyError(f"run {run_id} not found")
        c.execute("INSERT INTO run_labels (run_id, label, source, note, details) VALUES (%s, %s, %s, %s, %s)",
                  (run_id, label, source, note, _jsonb(details or {})))


def knowledge(agent: str | None = None) -> list[dict]:
    """Rows of v_knowledge for the latest suite version of each agent."""
    from psycopg.rows import dict_row

    with _conn() as c:
        c.row_factory = dict_row
        return c.execute("""
            SELECT k.* FROM v_knowledge k
            JOIN (SELECT DISTINCT ON (agent) agent, suite_version FROM eval_runs
                  ORDER BY agent, started_at DESC) latest USING (agent, suite_version)
            WHERE %(agent)s::text IS NULL OR k.agent = %(agent)s
            ORDER BY k.agent, k.category, k.variant_name""", {"agent": agent}).fetchall()
