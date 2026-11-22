"""Writes events into Postgres. Idempotent: every row carries a unique event_id."""
from __future__ import annotations

from typing import Any

CORE_KEYS = {"ts", "level", "event", "run_id", "node", "iteration", "event_id"}


def _jsonb(obj: Any):
    from psycopg.types.json import Jsonb

    return None if obj is None else Jsonb(obj)


def _int(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------- writer
def _write_one(cur, ev: dict, blobs: dict | None) -> None:
    blobs = blobs or {}
    run_id, ts, kind = ev["run_id"], ev["ts"], ev["event"]
    node, it, eid = ev.get("node"), _int(ev.get("iteration")), ev["event_id"]
    extra = {k: v for k, v in ev.items() if k not in CORE_KEYS}

    cur.execute("INSERT INTO runs (run_id, started_at) VALUES (%s, %s) ON CONFLICT (run_id) DO NOTHING",
                (run_id, ts))
    cur.execute(
        """INSERT INTO events (event_id, ts, run_id, level, event, node, iteration, fields)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
        (eid, ts, run_id, ev["level"], kind, node, it, _jsonb(extra)),
    )

    if kind == "run.start":
        cur.execute(
            """UPDATE runs SET name = %s, goal = %s, model = %s, started_at = %s, meta = %s
               WHERE run_id = %s""",
            (extra.get("name"), blobs.get("goal") or extra.get("goal"), extra.get("model"),
             ts, _jsonb(extra), run_id),
        )
    elif kind == "run.end":
        cur.execute("UPDATE runs SET status = %s, ended_at = %s, duration_ms = %s WHERE run_id = %s",
                    (extra.get("status"), ts, _int(extra.get("duration_ms")), run_id))
    elif kind == "run.error":
        cur.execute("UPDATE runs SET error = %s WHERE run_id = %s", (extra.get("error"), run_id))
    elif kind in ("llm.response", "llm.error"):
        cur.execute(
            """INSERT INTO llm_calls (event_id, ts, run_id, node, iteration, model, status,
                   latency_ms, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   stop_reason, error, prompt, response)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (event_id) DO NOTHING""",
            (eid, ts, run_id, node, it, extra.get("model"),
             "ok" if kind == "llm.response" else "error",
             _int(extra.get("latency_ms")), _int(extra.get("input_tokens")),
             _int(extra.get("output_tokens")), _int(extra.get("cache_read_tokens")) or 0,
             _int(extra.get("cache_write_tokens")) or 0, extra.get("stop_reason"), extra.get("error"),
             _jsonb(blobs.get("prompt")), _jsonb(blobs.get("response"))),
        )
    elif kind == "decision":
        details = {k: v for k, v in extra.items() if k not in ("decision", "reason")}
        cur.execute(
            """INSERT INTO decisions (event_id, ts, run_id, node, iteration, decision, reason, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
            (eid, ts, run_id, node, it, extra.get("decision"), extra.get("reason"), _jsonb(details)),
        )
    elif kind == "artifact":
        cur.execute(
            """INSERT INTO artifacts (event_id, ts, run_id, node, iteration, kind, name, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""",
            (eid, ts, run_id, node, it, extra.get("kind"), extra.get("name"),
             _jsonb(blobs.get("body"))),
        )


def write_batch(conn, batch: list[tuple[dict, dict | None]]) -> None:
    """Write events in one transaction, then refresh per-run totals (idempotent)."""
    run_ids = sorted({ev["run_id"] for ev, _ in batch})
    with conn.cursor() as cur:
        for ev, blobs in batch:
            _write_one(cur, ev, blobs)
        for rid in run_ids:
            cur.execute(
                """UPDATE runs SET
                     llm_calls           = (SELECT count(*) FROM llm_calls WHERE run_id = %s),
                     total_input_tokens  = (SELECT coalesce(sum(input_tokens), 0)  FROM llm_calls WHERE run_id = %s),
                     total_output_tokens = (SELECT coalesce(sum(output_tokens), 0) FROM llm_calls WHERE run_id = %s)
                   WHERE run_id = %s""",
                (rid, rid, rid, rid),
            )
    conn.commit()
