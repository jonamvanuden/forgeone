"""PostgresSink: mirrors log events to Postgres without ever slowing down or crashing a run.

- events are queued and written by a background thread in batches
- if Postgres is unreachable 3 times in a row, the sink disables itself
- JSONL stays the source of truth; `forgeone db backfill` replays it later
"""
from __future__ import annotations

import logging
import queue
import sys
import threading

from ..config import settings
from .writer import write_batch


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
        from ..observability import build_event, redact_obj  # built in the CALLER thread: keeps context

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
                          "Events are still in JSONL; run `forgeone db backfill` later.",
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
    dsn = settings().database_url
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("[forgeone] DATABASE_URL set but psycopg missing: pip install 'psycopg[binary]'",
              file=sys.stderr)
        return None
    return PostgresSink(dsn)
