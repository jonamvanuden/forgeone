"""AgentTrace: everything one agent run did, in a form checks and judges can read.

The runtime (Day 2) builds these from its logged events, so evaluation never depends on
how the agent was implemented, only on what it observably did.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_URL = re.compile(r"https?://[^\s)\]>\"'`]+")


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    result_preview: str = ""
    page_text: str | None = Field(default=None, description="Full text for open_page, for quote checks.")
    duration_ms: int | None = None
    after_error: bool = Field(default=False, description="First call after a failed one: a recovery attempt.")


class AgentTrace(BaseModel):
    case_id: str
    run_id: str | None = None
    task: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    report: str | None = Field(default=None, description="Markdown passed to write_report.")
    final_answer: str = ""
    agent: str | None = None
    variant: str | None = None
    model: str | None = None
    steps_limit: int | None = None
    hit_step_limit: bool = False
    hit_cost_limit: bool = False
    stop_reason: str | None = Field(default=None, description="finished | step_limit | cost_limit | error")
    llm_calls: int = 0
    llm_retries: int = 0
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    duration_ms: int | None = None

    @property
    def tool_errors(self) -> int:
        return sum(not c.ok for c in self.tool_calls)

    @property
    def recoveries(self) -> tuple[int, int]:
        """(attempts, successes): how often the agent tried again after a failed tool call."""
        attempts = [c for c in self.tool_calls if c.after_error]
        return len(attempts), sum(c.ok for c in attempts)

    def calls(self, name: str) -> list[ToolCall]:
        return [c for c in self.tool_calls if c.name == name]

    def opened_urls(self) -> set[str]:
        """URLs the agent actually read: successful open_page calls."""
        return {_norm(str(c.args.get("url", ""))) for c in self.calls("open_page") if c.ok and c.args.get("url")}

    def cited_urls(self) -> set[str]:
        return {_norm(u) for u in _URL.findall(self.report or "")}


def _norm(url: str) -> str:
    """Comparable form: no fragment, no trailing slash or punctuation."""
    url = url.split("#", 1)[0].rstrip(".,;:")
    return url[:-1] if url.endswith("/") else url
