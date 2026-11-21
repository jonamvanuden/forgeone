"""Programmatic checks: (trace, case) -> CheckResult. Exact, free and impossible to fool.

A check marked `hard` is a gate: one failure rejects a version, whatever its average.
Register new checks with @check(name, hard=...); suites refer to them by name.
"""
from __future__ import annotations

from typing import Callable

from ..scoring import CheckResult
from ..suite import EvalCase
from ..trace import AgentTrace

CheckFn = Callable[[AgentTrace, EvalCase], CheckResult]
CHECKS: dict[str, tuple[CheckFn, bool]] = {}


def check(name: str, hard: bool = False):
    def deco(fn: CheckFn) -> CheckFn:
        CHECKS[name] = (fn, hard)
        return fn
    return deco


def run_checks(trace: AgentTrace, case: EvalCase, names: list[str] | None = None) -> list[CheckResult]:
    results = []
    for name in names if names is not None else case.checks:
        fn, hard = CHECKS[name]
        r = fn(trace, case)
        r.name, r.hard = name, hard
        results.append(r)
    return results


# register built-in checks
from . import citations, honesty, process  # noqa: E402,F401
