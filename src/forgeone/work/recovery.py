"""Picking up a goal where it stopped: retry one task, or recover from an interrupted run.

Everything that is done stays done (with its output and cost). Only the failed task, the tasks
that were blocked by it, its milestone and the goal are reset, so the next run continues there.
"""
from __future__ import annotations

from .engine import sync_containers
from datetime import timedelta

from .model import GoalTree, Task, now
from .store import WorkStore


class CannotRetry(ValueError):
    pass


def find_task(tree: GoalTree, ref: str) -> Task:
    """By id (T-xxxxxx) or by code in the current plan ("2.3")."""
    ref = ref.strip()
    live = [t for t in tree.tasks if t.status != "superseded"]
    t = next((t for t in live if t.id == ref), None) or next((t for t in live if t.code == ref), None)
    if t is None:
        raise CannotRetry(f"no task {ref} in the current plan of {tree.goal.id}")
    return t


def _reset(t: Task, note: str | None, extra_attempts: int) -> None:
    t.status, t.finished_at = "pending", None
    t.max_attempts = t.attempts + extra_attempts          # fresh attempts; earlier ones stay in the log
    t.feedback = (f"You retried this task with a note: {note}" if note
                  else t.feedback or "The previous attempts failed; take a different approach.")
    t.failure_reason = t.failure_detail = None


def retry_task(store: WorkStore, goal_id: str, ref: str, note: str | None = None, who: str = "you") -> str:
    tree = store.load(goal_id)
    g = tree.goal
    if g.status == "cancelled" or (g.status == "running" and not interrupted(tree)):
        raise CannotRetry(f"{g.id} is {g.status}")
    t = find_task(tree, ref)
    targets = [t] if t.stages else [k for k in tree.children(t.id) if k.status in ("failed", "blocked")]
    if t.status not in ("failed", "blocked") or not targets:
        raise CannotRetry(f"{t.code} is {t.status}: only a failed or blocked task can be retried")
    changed: list = []
    for x in targets:
        _reset(x, note, 3 if x.risk != "high" else 2)
        changed.append(x)
    # unblock everything that was blocked because of them (also in later milestones)
    for x in tree.tasks:
        if x.status == "blocked" and x.failure_reason == "blocked_dependency" and x not in changed:
            x.status, x.failure_reason, x.failure_detail, x.finished_at = "pending", None, None, None
            changed.append(x)
    for m in tree.active_milestones():
        if m.status == "failed" and any(x.milestone_id == m.id for x in changed):
            m.status, m.note, m.finished_at = "pending", None, None
            changed.append(m)
    changed += sync_containers(tree)
    g.status, g.finished_at = "paused", None
    g.note = f"{t.code} {t.title} reset by {who}" + (f" ({note})" if note else "") + ": continue to run it again"
    changed.append(g)
    store.save(*changed)
    return g.note


def recover_interrupted(tree: GoalTree) -> list:
    """A run that died (restart, crash, kill) leaves tasks on 'running'; nothing would pick them
    up again. Called at the start of every run, which owns the goal while it runs."""
    changed = []
    for t in tree.tasks:
        if t.status == "running" and t.stages:
            t.status = "pending"
            t.max_attempts = max(t.max_attempts, t.attempts + 1)   # the interrupted attempt does not count
            t.feedback = t.feedback or "The previous run was interrupted while this task was running."
            changed.append(t)
    return changed


STALE_AFTER = timedelta(minutes=15)


def interrupted(tree: GoalTree) -> bool:
    """'running', but nothing has moved for a while: the process that ran it is gone.
    (A run started on the command line is not a dashboard job, so we go by activity.)"""
    if tree.goal.status != "running":
        return False
    stamps = [tree.goal.updated_at] + [t.updated_at for t in tree.tasks] + \
             [a.finished_at or a.started_at for a in tree.actions]
    return now() - max(x for x in stamps if x is not None) > STALE_AFTER
