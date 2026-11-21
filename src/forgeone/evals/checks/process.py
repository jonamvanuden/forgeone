"""Process checks: did the agent follow its own operating rules and budgets?"""
from ..scoring import CheckResult
from . import check


@check("write_report_once")
def write_report_once(trace, case) -> CheckResult:
    """write_report was called exactly once."""
    n = len(trace.calls("write_report"))
    return CheckResult(passed=n == 1, detail=f"write_report called {n} times")


@check("within_step_budget", hard=True)
def within_step_budget(trace, case) -> CheckResult:
    """The run stayed within its step budget and never hit the limit."""
    if trace.hit_step_limit:
        return CheckResult(passed=False, detail="hit the step limit")
    lim = trace.steps_limit
    n = len(trace.tool_calls)
    return CheckResult(passed=lim is None or n <= lim, detail=f"{n} tool calls (limit {lim})")


@check("within_cost_budget", hard=True)
def within_cost_budget(trace, case) -> CheckResult:
    """The run cost no more than the case's max_cost_usd."""
    if case.max_cost_usd is None or trace.cost_usd is None:
        return CheckResult(passed=True, detail="no cost budget set")
    return CheckResult(passed=trace.cost_usd <= case.max_cost_usd,
                       detail=f"${trace.cost_usd:.4f} (budget ${case.max_cost_usd:.4f})")


@check("no_run_error")
def no_run_error(trace, case) -> CheckResult:
    """The run finished without an error."""
    return CheckResult(passed=trace.error is None, detail=trace.error or "no error")
