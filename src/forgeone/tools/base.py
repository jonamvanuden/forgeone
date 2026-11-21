"""Tool contract: what a tool is, what it returns, and the context it runs in."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


class Browser(Protocol):
    """What web tools need from a browser. PlaywrightBrowser implements it; tests use a fake."""

    def search(self, query: str, max_results: int = 8) -> list[dict]: ...
    def open(self, url: str) -> dict: ...          # {"url", "title", "text", "links": [{"text","url"}]}
    def click(self, text: str) -> dict: ...
    def find(self, query: str) -> list[str]: ...
    def close(self) -> None: ...


@dataclass
class ToolContext:
    browser: Browser | None = None
    reports_dir: Path | None = None
    max_page_chars: int = 8000          # what the model sees of a page
    report: str | None = None           # set by write_report
    report_path: str | None = None


@dataclass
class ToolResult:
    content: str                         # text returned to the model
    ok: bool = True
    error: str | None = None
    page_text: str | None = None         # full page text, recorded for evals (quote checks)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict                     # JSON schema of the arguments
    fn: Callable[[ToolContext, dict], ToolResult]
    deterministic: bool = True           # no LLM inside: cheap, repeatable

    def schema(self) -> dict:
        """Anthropic tool format, accepted by ChatAnthropic.bind_tools."""
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}
