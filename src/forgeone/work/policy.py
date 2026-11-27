"""Rules that turn a planned task into an executable one. Plain code, so they are testable
and every decision comes with a reason that is stored on the task.

Everything here is a first version meant to be replaced by measured numbers: once the
knowledge views have enough rows, `route()` should pick the cheapest model that is good
enough for this task type instead of using the difficulty threshold.
"""
from __future__ import annotations

import os

from ..config import settings
from ..llm.pricing import call_cost
from .model import Goal, Stage, Task

MAX_DEPTH = 2                 # task -> subtasks, no deeper
SPLIT_AT_DIFFICULTY = 5       # a task this hard is broken into subtasks first
MAX_MILESTONES = 10
MAX_TASKS = 8
MAX_REPLANS = 2               # automatic replans per goal (autonomous goals only)
TOOL_TYPES = {"research", "read", "extract"}   # need the agent's browser tools

# Average tokens for one execution step; used only for the cost estimate.
STEP_TOKENS_IN, STEP_TOKENS_OUT = 6000, 600


def _routing() -> dict:
    from ..llm.registry import routing

    try:
        return routing()
    except Exception:  # noqa: BLE001  (a broken models.yaml falls back to the env defaults)
        return {"types": {}}


def cheap_model() -> str:
    """models.yaml routing.cheap, else FORGEONE_AGENT_MODEL."""
    return _routing().get("cheap") or settings().agent_model


def strong_model() -> str:
    """models.yaml routing.strong, else FORGEONE_STRONG_MODEL."""
    return _routing().get("strong") or os.getenv("FORGEONE_STRONG_MODEL", "claude-sonnet-5-5")


def planner_model() -> str:
    return _routing().get("planner") or settings().builder_model


def judge_model() -> str:
    return _routing().get("judge") or os.getenv("FORGEONE_JUDGE_MODEL", settings().builder_model)


def type_model(task_type: str | None) -> str | None:
    """A model routed to this task type in models.yaml (routing.types), if any."""
    return _routing()["types"].get(task_type) if task_type else None


def stages_for(risk: str, reversible: bool = True) -> list[Stage]:
    """Low risk: do it and check it. Medium and high: also test edge cases and get the output approved.

    Irreversible work (publish, send, pay) is approved BEFORE it runs: approving afterwards would
    be too late. The planner is told to put the preparation (the draft) in a separate, reversible
    task, so the draft still gets verified and edge-tested first.
    """
    if not reversible:
        return ["approve", "execute", "verify"]
    if risk == "low":
        return ["execute", "verify"]
    return ["execute", "verify", "edge_test", "approve"]


def is_gate(stages: list[str]) -> bool:
    """True when approval comes before execution: a go-ahead, not a sign-off."""
    return "approve" in stages and "execute" in stages and stages.index("approve") < stages.index("execute")


def approver_for(task: Task, goal: Goal) -> str | None:
    """High risk or irreversible work always goes to a human; medium risk to an AI approver."""
    if "approve" not in task.stages:
        return None
    if task.risk == "high" or not task.reversible:
        return "human"
    return "ai"


def milestone_approver(goal: Goal, milestone=None) -> str:
    """human | ai | none. A playbook can set it per milestone; otherwise the goal's autonomy decides."""
    if milestone is not None and milestone.approval:
        return milestone.approval
    return "human" if goal.autonomy == "supervised" else "ai"


def route(task: Task, goal: Goal, has_agent: bool) -> tuple[str, str, str]:
    """-> (executor, model, reason)."""
    if task.type in TOOL_TYPES and has_agent:
        executor, why = "agent", f"{task.type} needs web tools, so {goal.agent} runs it"
    elif task.type in TOOL_TYPES:
        executor, why = "llm", f"{task.type} would need web tools but the goal has no agent: model answers from its own knowledge"
    else:
        executor, why = "llm", f"{task.type} needs no tools: one model call"
    if (m := type_model(task.type)):
        return executor, m, f"{why}; models.yaml routes {task.type} tasks to {m}"
    if task.difficulty >= 4:
        return executor, strong_model(), f"{why}; difficulty {task.difficulty} >= 4: stronger model"
    return executor, cheap_model(), f"{why}; difficulty {task.difficulty} <= 3: cheapest model"


def estimate_cost(model: str, steps: int | None) -> float | None:
    if not steps:
        return None
    c = call_cost(model, STEP_TOKENS_IN, STEP_TOKENS_OUT)
    return round(c * steps, 6) if c is not None else None


def prepare(task: Task, goal: Goal, has_agent: bool) -> Task:
    """Fill the routing and policy fields of a freshly planned task."""
    task.difficulty = max(1, min(5, int(task.difficulty)))
    task.stages = stages_for(task.risk, task.reversible)
    task.approver = approver_for(task, goal)
    task.executor, task.model, task.routing_reason = route(task, goal, has_agent)
    task.est_cost_usd = estimate_cost(task.model, task.est_steps)
    # per-attempt budget: three times the estimate, at least a few cents
    task.budget_usd = round(max(0.05, 3 * (task.est_cost_usd or 0.02)), 4)
    task.max_attempts = 3 if task.risk != "high" else 2
    if task.est_minutes:
        task.timeout_s = int(max(120, task.est_minutes * 60 * 4))
    return task


def as_container(task: Task) -> Task:
    """A task that was split into subtasks does no work itself; its status follows the children."""
    task.stages, task.approver, task.executor, task.model = [], None, "none", None
    task.routing_reason = "split into subtasks because it was rated difficulty 5"
    task.budget_usd = task.est_cost_usd = None
    return task


def actual_difficulty(task: Task, rejections: int) -> float:
    """What the task turned out to be, on the same 1..5 scale as the prediction.

    Starts from the prediction. Done on the first try, without rejections and within the cost
    estimate: one point easier than predicted. Every extra attempt adds 1.5, every rejection
    by a verifier or approver adds 1, a cost overrun adds up to 2. A failed task is a 5.
    First version; tune it once there is data to compare against.
    """
    if task.status == "failed":
        return 5.0
    over = bool(task.est_cost_usd) and task.cost_usd > task.est_cost_usd
    score = float(task.difficulty)
    if task.attempts <= 1 and rejections == 0 and not over:
        score -= 1
    score += 1.5 * max(0, task.attempts - 1) + 1.0 * rejections
    if over:
        score += min(2.0, task.cost_usd / task.est_cost_usd - 1)
    return round(max(1.0, min(5.0, score)), 1)
