"""Event construction and the logging API every other module uses.

Event types:
  run.start / run.end / run.error
  node.start / node.end / node.error
  llm.request / llm.response / llm.error   (model, tokens, latency, payload ref)
  decision                                 why ForgeOne chose something
  artifact                                 something a run produced (spec, report, verdict)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .context import iteration_var, node_var, run_dir_var, run_id_var
from .redaction import redact, redact_obj

logger = logging.getLogger("forgeone")


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
