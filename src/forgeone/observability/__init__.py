"""Observability: structured, correlated, replayable logs for every run.

Every event is one JSON object in runs/<run_id>/events.jsonl (the source of truth),
mirrored to Postgres when DATABASE_URL is set (see forgeone.store).
"""
from .context import iteration_var, node_var, run_dir_var, run_id_var
from .events import (
    ConsoleFormatter, JsonFormatter, build_event, log_artifact, log_decision, log_event,
    logger, write_payload,
)
from .llm_logger import LLMCallLogger
from .redaction import redact, redact_obj
from .tracing import start_run, traced_node

__all__ = [
    "run_id_var", "node_var", "iteration_var", "run_dir_var",
    "ConsoleFormatter", "JsonFormatter", "build_event", "logger",
    "log_event", "log_decision", "log_artifact", "write_payload",
    "LLMCallLogger", "redact", "redact_obj", "start_run", "traced_node",
]
