"""Turn the planner model's drafts into milestone and task records. Pure functions:
no model calls, no database, so the conversion rules are easy to test."""
from __future__ import annotations

from .model import TASK_TYPES, Goal, Milestone, Task
from .policy import MAX_MILESTONES, MAX_TASKS, as_container, prepare
from .schemas import MilestoneDraft, TaskDraft


def bullets(items: list[str], empty: str = "- none") -> str:
    return "\n".join(f"- {x}" for x in items) if items else empty


def numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)) if items else "(none)"


def task_types_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in TASK_TYPES.items())


def workers_text(goal: Goal, agent_tools: list[str] | None) -> str:
    if goal.agent and agent_tools:
        return (f"- The agent {goal.agent}, with these tools: {', '.join(agent_tools)}. It does research, read "
                f"and extract tasks.\n- Plain model calls (no web access) for every other task type.")
    return "- Plain model calls only. Nobody has web access: plan nothing that needs live web data."


def milestones_from(drafts: list[MilestoneDraft], goal: Goal, version: int, start_seq: int) -> list[Milestone]:
    out = []
    for i, d in enumerate(drafts[:MAX_MILESTONES]):
        out.append(Milestone(goal_id=goal.id, plan_version=version, seq=start_seq + i, title=d.title.strip(),
                             description=d.description.strip(), done_when=[x for x in d.done_when if x.strip()][:4]))
    if not out:
        raise ValueError("the planner returned no milestones")
    return out


def tasks_from(drafts: list[TaskDraft], goal: Goal, milestone: Milestone, has_agent: bool,
               parent: Task | None = None) -> list[Task]:
    """Positions in depends_on become task ids; only earlier tasks count, the rest is dropped."""
    limit = MAX_TASKS if parent is None else 5
    out: list[Task] = []
    for i, d in enumerate(drafts[:limit], 1):
        deps = [out[j - 1].id for j in sorted(set(d.depends_on)) if 1 <= j < i]
        if parent is not None:
            deps = list(dict.fromkeys(parent.depends_on + deps))   # a subtask also waits for what its parent waited for
        t = Task(goal_id=goal.id, milestone_id=milestone.id, parent_id=parent.id if parent else None,
                 plan_version=milestone.plan_version, seq=i,
                 code=f"{parent.code}.{i}" if parent else f"{milestone.seq}.{i}",
                 depth=(parent.depth + 1) if parent else 1,
                 title=d.title.strip(), description=d.description.strip(), type=d.type,
                 difficulty=min(d.difficulty, 4) if parent else d.difficulty, risk=d.risk, reversible=d.reversible,
                 acceptance_criteria=[c for c in d.acceptance_criteria if c.strip()][:5],
                 depends_on=deps, input=d.input, expected_output=d.expected_output,
                 est_steps=max(1, d.est_steps), est_minutes=max(0.1, d.est_minutes))
        out.append(prepare(t, goal, has_agent))
    if not out:
        raise ValueError(f"the planner returned no tasks for {milestone.title!r}")
    return out


def make_container(task: Task) -> Task:
    return as_container(task)
