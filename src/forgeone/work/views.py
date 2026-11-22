"""Read-only summaries of a goal tree, shared by the CLI and the Control Room."""
from __future__ import annotations

from .model import GoalTree, Task
from .policy import is_gate


def stage_states(tree: GoalTree, t: Task) -> list[dict]:
    """State of each stage in the task's latest attempt: passed | failed | pending | running | none."""
    every = tree.actions_of(t.id)
    gates = [a for a in every if a.input.get("gate")]
    acts = [a for a in every if a.attempt == t.attempts and not a.input.get("gate")]
    out = []
    for s in t.stages:
        mine = gates if s == "approve" and is_gate(t.stages) else [a for a in acts if a.kind == s]
        out.append({"stage": s, "state": mine[-1].status if mine else "none",
                    "gate": s == "approve" and is_gate(t.stages),
                    "actor": mine[-1].actor if mine else (t.approver if s == "approve" else None)})
    return out


def progress(tree: GoalTree, milestone_id: str | None = None) -> dict:
    tasks = [t for t in tree.leaf_tasks() if milestone_id is None or t.milestone_id == milestone_id]
    count = lambda *st: sum(1 for t in tasks if t.status in st)  # noqa: E731
    return {"tasks": len(tasks), "done": count("done", "skipped"), "failed": count("failed"),
            "blocked": count("blocked"), "waiting": count("waiting"), "running": count("running")}


def waiting_on_you(tree: GoalTree) -> list[dict]:
    out = []
    for a in tree.actions:
        if a.kind != "approve" or a.status != "pending":
            continue
        if a.task_id:
            t = tree.task(a.task_id)
            out.append({"kind": "gate" if a.input.get("gate") else "task", "id": t.id, "code": t.code,
                        "title": t.title, "action_id": a.id,
                        "since": a.started_at, "risk": t.risk, "goal_id": tree.goal.id, "goal": tree.goal.title})
        else:
            m = tree.milestone(a.milestone_id)
            out.append({"kind": "milestone", "id": m.id, "code": f"M{m.seq}", "title": m.title, "action_id": a.id,
                        "since": a.started_at, "goal_id": tree.goal.id, "goal": tree.goal.title})
    return out


def current_milestone(tree: GoalTree):
    return next((m for m in tree.active_milestones() if m.status != "done"), None)
