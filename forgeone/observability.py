"""ForgeOne observability: structured, correlated, replayable logs.

Every line is one JSON event in runs/<run_id>/events.jsonl.
Full prompts/responses go to runs/<run_id>/payloads/ and are referenced by `payload`.

Event types:
  run.start / run.end / run.error
  node.start / node.end / node.error
  llm.request / llm.response / llm.error   (model, tokens, latency, payload ref)
  decision                                 why ForgeOne chose something
  artifact                                 something a run produced (spec, report, verdict)

If DATABASE_URL is set, every event is also mirrored to Postgres (see store.py).
"""
from __future__ import annotations

import functools
import json
import logging
import re
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

# --- correlation context: automatically attached to every event -------------
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
node_var: ContextVar[str | None] = ContextVar("node", default=None)
iteration_var: ContextVar[int | None] = ContextVar("iteration", default=None)
run_dir_var: ContextVar[Path | None] = ContextVar("run_dir", default=None)

logger = logging.getLogger("forgeone")

# --- secret redaction: nothing secret ever hits disk ---------------------------
_SECRETS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)([\"']?\s*[:=]\s*[\"']?)[^\s\"',]{6,}"),
]


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub(lambda m: (m.group(1) + m.group(2) if m.lastindex else "") + "[REDACTED]", text)
    return text


def redact_obj(obj: Any) -> Any:
    """Redact every string inside a structure BEFORE serializing (keeps JSON valid)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


# --- formatters -----------------------------------------------------------------
def build_event(record: logging.LogRecord) -> dict:
    """The canonical event dict. Must run in the thread that logged (reads context)."""
    event = {
        "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "event": record.getMessage(),
        "run_id": run_id_var.get(),
        "node": node_var.get(),
        "iteration": iteration_var.get(),
        **getattr(record, "fields", {}),
    }
    return redact_obj(json.loads(json.dumps(event, default=str)))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(build_event(record), ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Short human-readable line for the terminal."""

    KEYS = ("latency_ms", "duration_ms", "input_tokens", "output_tokens", "decision",
            "reason", "status", "error", "model")

    def format(self, record: logging.LogRecord) -> str:
        f = getattr(record, "fields", {})
        node = node_var.get()
        extra = " ".join(f"{k}={f[k]}" for k in self.KEYS if k in f)
        where = f"[{node}]" if node else ""
        return redact(f"{datetime.now():%H:%M:%S} {record.levelname:<5} {record.getMessage():<14} {where} {extra}".rstrip())


def log_event(event: str, level: int = logging.INFO, _blobs: dict | None = None, **fields: Any) -> None:
    """_blobs: large data (prompts, specs) that goes to Postgres, not into the JSONL line."""
    fields["event_id"] = uuid.uuid4().hex
    logger.log(level, event, extra={"fields": fields, "blobs": _blobs})


def log_decision(decision: str, reason: str, **fields: Any) -> None:
    """Log WHY ForgeOne did something. The most valuable event for debugging agents."""
    log_event("decision", decision=decision, reason=reason, **fields)


def log_artifact(kind: str, name: str | None, body: Any, path: str | None = None) -> None:
    """Record something a run produced (spec, report, verdict) so dashboards can diff versions."""
    log_event("artifact", kind=kind, name=name, path=path, _blobs={"body": body})


def write_payload(kind: str, data: Any) -> str | None:
    """Store a large payload (prompt/response) in its own file; return its relative path."""
    run_dir = run_dir_var.get()
    if run_dir is None:
        return None
    pid = f"{kind}-{uuid.uuid4().hex[:10]}"
    path = run_dir / "payloads" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(json.dumps(data, default=str))  # normalize objects to plain JSON types
    path.write_text(json.dumps(redact_obj(data), ensure_ascii=False, indent=1))
    return str(path.relative_to(run_dir))


# --- run lifecycle --------------------------------------------------------------
@contextmanager
def start_run(name: str, runs_root: str | Path = "runs", level: int = logging.INFO, **fields: Any):
    """One context per graph run. Creates runs/<run_id>/ and correlates all events."""
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(runs_root) / run_id
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
    from .store import make_sink  # Postgres mirror, only if DATABASE_URL is set

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


# --- every LLM call, automatically, via LangChain callbacks ----------------------
class LLMCallLogger(BaseCallbackHandler):
    """Pass in config={"callbacks": [LLMCallLogger()]}; logs every model call
    with model, latency, tokens and a reference to the full prompt/response."""

    def __init__(self):
        self._calls: dict[str, dict] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or (kwargs.get("metadata") or {}).get("ls_model_name")
        prompt = [{"role": m.type, "content": m.content} for batch in messages for m in batch]
        prompt_data = {"model": model, "params": params, "messages": prompt}
        ref = write_payload("prompt", prompt_data)
        self._calls[str(run_id)] = {"t0": time.perf_counter(), "model": model,
                                    "prompt_ref": ref, "prompt": prompt_data}
        log_event("llm.request", model=model, messages=len(prompt),
                  prompt_chars=sum(len(str(m["content"])) for m in prompt), payload=ref)

    def on_llm_end(self, response, *, run_id, **kwargs):
        call = self._calls.pop(str(run_id), {})
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        usage = (getattr(msg, "usage_metadata", None) or {}) if msg else {}
        out = {
            "content": getattr(msg, "content", getattr(gen, "text", None)),
            "tool_calls": getattr(msg, "tool_calls", None),
            "stop_reason": (getattr(msg, "response_metadata", {}) or {}).get("stop_reason"),
        }
        ref = write_payload("response", out)
        log_event("llm.response",
                  model=call.get("model"),
                  latency_ms=round((time.perf_counter() - call.get("t0", time.perf_counter())) * 1000),
                  input_tokens=usage.get("input_tokens"),
                  output_tokens=usage.get("output_tokens"),
                  stop_reason=out["stop_reason"],
                  prompt_payload=call.get("prompt_ref"), payload=ref,
                  _blobs={"prompt": call.get("prompt"), "response": out})

    def on_llm_error(self, error, *, run_id, **kwargs):
        call = self._calls.pop(str(run_id), {})
        log_event("llm.error", logging.ERROR, model=call.get("model"),
                  error=f"{type(error).__name__}: {error}", error_type=type(error).__name__,
                  latency_ms=round((time.perf_counter() - call.get("t0", time.perf_counter())) * 1000),
                  prompt_payload=call.get("prompt_ref"),
                  _blobs={"prompt": call.get("prompt")})