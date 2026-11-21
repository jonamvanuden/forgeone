"""Tool implementations: what generated agents can actually do.

Every tool declared in forgeone.spec.tools (the allowlist) is implemented here, and nothing
else. Tools are deterministic code: no LLM inside, so they are cheap and repeatable.
"""
from __future__ import annotations

from ..spec.tools import ToolName
from .base import Browser, ToolContext, ToolDef, ToolResult
from .report import TOOLS as _REPORT
from .web import TOOLS as _WEB

REGISTRY: dict[str, ToolDef] = {t.name: t for t in [*_WEB, *_REPORT]}


def toolset(names: list[str]) -> list[ToolDef]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise KeyError(f"no implementation for tools {unknown}")
    return [REGISTRY[n] for n in names]


def _check_allowlist() -> None:
    declared = set(ToolName.__args__)
    if declared != set(REGISTRY):
        raise RuntimeError(f"allowlist and implementations differ: {sorted(declared ^ set(REGISTRY))}")


_check_allowlist()

__all__ = ["REGISTRY", "toolset", "Browser", "ToolContext", "ToolDef", "ToolResult"]
