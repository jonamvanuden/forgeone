"""Run lifecycle and node tracing."""
from __future__ import annotations

import functools
import logging
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from .context import iteration_var, node_var, run_dir_var, run_id_var
from .events import ConsoleFormatter, JsonFormatter, log_event, logger


@contextmanager
def start_run(name: str, runs_root: str | Path | None = None, level: int = logging.INFO, **fields: Any):
    """One context per graph run. Creates runs/<run_id>/ and correlates all events.
    If DATABASE_URL is set, events are mirrored to Postgres as well."""
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(runs_root) if runs_root is not None else settings().runs_dir
    run_dir = run_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fh = logging.FileHandler(run_dir / "events.jsonl", encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    fh.setLevel(logging.DEBUG)                       # file gets everything
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(ConsoleFormatter())
    ch.setLevel(level)                               # terminal gets the summary
    logger.addHandler(fh)
    logger.addHandler(ch)
    from ..store import make_sink  # Postgres mirror, only if DATABASE_URL is set

    sink = make_sink()
    if sink:
        logger.addHandler(sink)

    tokens = [run_id_var.set(run_id), run_dir_var.set(run_dir)]
    t0 = time.perf_counter()
    log_event("run.start", name=name, run_dir=str(run_dir), db=bool(sink), **fields)
    status = "error"
    try:
        yield run_id
        status = "ok"
    except BaseException as e:
        log_event("run.error", logging.ERROR, error=f"{type(e).__name__}: {e}",
                  traceback=traceback.format_exc())
        raise
    finally:
        log_event("run.end", status=status, duration_ms=round((time.perf_counter() - t0) * 1000))
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        for var, tok in zip((run_id_var, run_dir_var), tokens):
            var.reset(tok)


def _summarize(value: Any) -> Any:
    """Small, safe preview of a state value for node.start/node.end events."""
    if isinstance(value, str):
        return {"type": "str", "chars": len(value), "preview": value[:120]}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "len": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(value)[:20]}
    return value


def traced_node(name: str):
    """Decorator for LangGraph nodes: start/end/error, duration, what changed."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(state, *args, **kwargs):
            tok_node = node_var.set(name)
            tok_it = iteration_var.set(state.get("iteration") if isinstance(state, dict) else None)
            t0 = time.perf_counter()
            log_event("node.start", logging.DEBUG,
                      input={k: _summarize(v) for k, v in (state or {}).items()})
            try:
                update = fn(state, *args, **kwargs)
            except Exception as e:
                log_event("node.error", logging.ERROR,
                          error=f"{type(e).__name__}: {e}",
                          error_type=type(e).__name__,
                          traceback=traceback.format_exc(),
                          duration_ms=round((time.perf_counter() - t0) * 1000))
                raise
            else:
                log_event("node.end",
                          duration_ms=round((time.perf_counter() - t0) * 1000),
                          output={k: _summarize(v) for k, v in (update or {}).items()})
                return update
            finally:
                node_var.reset(tok_node)
                iteration_var.reset(tok_it)

        return inner

    return wrap
