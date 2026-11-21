"""Correlation context: attached to every event automatically, never passed by hand."""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
node_var: ContextVar[str | None] = ContextVar("node", default=None)
iteration_var: ContextVar[int | None] = ContextVar("iteration", default=None)
run_dir_var: ContextVar[Path | None] = ContextVar("run_dir", default=None)
