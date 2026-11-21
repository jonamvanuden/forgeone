"""ForgeOne event store: Postgres sink + backfill.

The JSONL files stay the source of truth. This module mirrors every event into
Postgres for dashboards, without ever slowing down or crashing a run:
  - events are queued and written by a background thread in batches
  - if Postgres is unreachable, the sink disables itself and the run continues
  - every write is idempotent (event_id UNIQUE), so `backfill` can replay JSONL

CLI:
  python -m forgeone.store init        # apply db/schema.sql
  python -m forgeone.store backfill    # load runs/*/events.jsonl into Postgres
  python -m forgeone.store status      # row counts + latest runs
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path
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
                   latency_ms, input_tokens, output_tokens, stop_reason, error, prompt, response)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (event_id) DO NOTHING""",
            (eid, ts, run_id, node, it, extra.get("model"),
             "ok" if kind == "llm.response" else "error",
             _int(extra.get("latency_ms")), _int(extra.get("input_tokens")),
             _int(extra.get("output_tokens")), extra.get("stop_reason"), extra.get("error"),
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


# --------------------------------------------------------------------- sink
class PostgresSink(logging.Handler):
    """logging.Handler that mirrors events to Postgres from a background thread."""

    MAX_FAILURES = 3

    def __init__(self, dsn: str, batch_size: int = 100, flush_interval: float = 0.5):
        super().__init__(level=logging.DEBUG)
        self.dsn, self.batch_size, self.flush_interval = dsn, batch_size, flush_interval
        self.q: queue.Queue = queue.Queue(maxsize=20_000)
        self.stats = {"written": 0, "failed": 0, "dropped": 0}
        self.disabled_reason: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="forgeone-pg-sink", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if self.disabled_reason:
            return
        from .observability import build_event, redact_obj  # built in the CALLER thread: keeps context

        try:
            self.q.put_nowait((build_event(record), redact_obj(getattr(record, "blobs", None))))
        except queue.Full:
            self.stats["dropped"] += 1

    def _drain(self) -> list:
        try:
            batch = [self.q.get(timeout=self.flush_interval)]
        except queue.Empty:
            return []
        while len(batch) < self.batch_size:
            try:
                batch.append(self.q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _loop(self) -> None:
        import psycopg

        conn, failures = None, 0
        while not (self._stop.is_set() and self.q.empty()):
            batch = self._drain()
            if not batch:
                continue
            if self.disabled_reason:
                self.stats["failed"] += len(batch)
                continue
            try:
                if conn is None or conn.closed:
                    conn = psycopg.connect(self.dsn, connect_timeout=3)
                write_batch(conn, batch)
                self.stats["written"] += len(batch)
                failures = 0
            except Exception as e:  # never propagate into the agent
                self.stats["failed"] += len(batch)
                failures += 1
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = None
                if failures >= self.MAX_FAILURES:
                    self.disabled_reason = f"{type(e).__name__}: {e}"
                    print(f"[forgeone] Postgres sink disabled ({self.disabled_reason}). "
                          "Events are still in JSONL; run `python -m forgeone.store backfill` later.",
                          file=sys.stderr)
        if conn is not None:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)
        if self.stats["failed"] or self.stats["dropped"]:
            print(f"[forgeone] Postgres sink: {self.stats}", file=sys.stderr)
        super().close()


def make_sink() -> PostgresSink | None:
    """Sink if DATABASE_URL is set and psycopg is installed; otherwise None (JSONL only)."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("[forgeone] DATABASE_URL set but psycopg missing: pip install 'psycopg[binary]'",
              file=sys.stderr)
        return None
    return PostgresSink(dsn)


# ----------------------------------------------------------------- backfill
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def events_from_run_dir(run_dir: Path) -> list[tuple[dict, dict | None]]:
    out = []
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        ev = json.loads(line)
        # older events had no event_id: derive a stable one from the line itself
        ev.setdefault("event_id", hashlib.sha1(f"{ev.get('run_id')}|{line}".encode()).hexdigest())
        blobs = None
        if ev["event"] in ("llm.response", "llm.error"):
            p, r = ev.get("prompt_payload"), ev.get("payload")
            blobs = {"prompt": _read_json(run_dir / p) if p else None,
                     "response": _read_json(run_dir / r) if r and ev["event"] == "llm.response" else None}
        elif ev["event"] == "artifact.saved" and str(ev.get("path", "")).endswith(".yaml"):
            # step-2 runs logged only a path; turn it into a proper artifact row
            import yaml

            spec_path = Path(ev["path"])
            if not spec_path.is_absolute():
                spec_path = run_dir.parent.parent / spec_path
            if spec_path.exists():
                art = {**ev, "event": "artifact", "kind": "spec",
                       "event_id": ev["event_id"] + ":artifact"}
                body = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
                art["name"] = (body or {}).get("name")
                out.append((ev, None))
                out.append((art, {"body": body}))
                continue
        out.append((ev, blobs))
    return out


def backfill(runs_root: str | Path = "runs") -> None:
    import psycopg

    dirs = sorted(d for d in Path(runs_root).iterdir() if (d / "events.jsonl").exists())
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for d in dirs:
            batch = events_from_run_dir(d)
            write_batch(conn, batch)
            print(f"  {d.name}: {len(batch)} events")
    print(f"backfilled {len(dirs)} runs (duplicates skipped)")


def init_schema(schema: str | Path = Path(__file__).resolve().parent.parent / "db" / "schema.sql") -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(Path(schema).read_text(encoding="utf-8"))
        conn.commit()
    print(f"schema applied: {schema}")


def status() -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for t in ("runs", "events", "llm_calls", "decisions", "artifacts"):
            print(f"{t:<10} {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
        print("\nlatest runs:")
        for r in conn.execute(
            """SELECT run_id, status, duration_ms, llm_calls, total_input_tokens, total_output_tokens
               FROM runs ORDER BY started_at DESC LIMIT 5"""
        ):
            print("  ", r)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"init": init_schema, "backfill": backfill, "status": status}[cmd]()