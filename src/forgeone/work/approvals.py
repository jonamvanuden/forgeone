"""Human decisions: approve or reject a task or milestone that waits for you.

The decision is recorded on the pending Action (who, when, why). Nothing runs here: the goal
continues the next time `forgeone goal run` is started.
"""
from __future__ import annotations

from .engine import finish_task, sync_containers
from .model import Action, GoalTree, now
from .store import WorkStore


class NotWaiting(ValueError):
    pass


def normalize_id(record_id: str) -> str:
    record_id = record_id.strip()
    return record_id[:1].upper() + record_id[1:].lower() if record_id else record_id


def pending_approvals(tree: GoalTree) -> list[Action]:
    return [a for a in tree.actions if a.kind == "approve" and a.status == "pending"]


def decide(store: WorkStore, record_id: str, approved: bool, note: str | None = None, who: str = "you") -> str:
    record_id = normalize_id(record_id)
    found = store.find(record_id)
    if not found:
        raise KeyError(f"{record_id} not found")
    kind, goal_id = found
    tree = store.load(goal_id)
    pending = pending_approvals(tree)
    if kind == "goal":
        if len(pending) != 1:
            raise NotWaiting(f"{record_id} has {len(pending)} approvals waiting: name the task or milestone id")
        a = pending[0]
    elif kind == "action":
        a = next((x for x in pending if x.id == record_id), None)
    elif kind == "task":
        a = next((x for x in pending if x.task_id == record_id), None)
    else:
        a = next((x for x in pending if x.milestone_id == record_id and x.task_id is None), None)
    if a is None:
        raise NotWaiting(f"{record_id} is not waiting for an approval")
    if not approved and not (note or "").strip():
        raise ValueError("a rejection needs a note: what must change?")

    a.status = "passed" if approved else "failed"
    a.actor, a.actor_type = who, "human"
    a.reason = note or ("approved" if approved else None)
    a.feedback = None if approved else note
    a.failure_reason = None if approved else "rejected"
    a.confidence = 1.0
    a.finished_at = now()
    changed = [a]
    g = tree.goal

    if a.task_id:
        t = tree.task(a.task_id)
        if approved:
            finish_task(tree, t, "done")
            msg = f"{t.code} {t.title}: approved"
        else:
            t.failure_reason, t.failure_detail, t.feedback = "rejected", f"rejected by {who}: {note}", note
            if t.attempts >= t.max_attempts:
                finish_task(tree, t, "failed")
                msg = f"{t.code} {t.title}: rejected, and no attempts left (failed)"
            else:
                t.status = "pending"
                msg = f"{t.code} {t.title}: rejected; it runs again with your note ({t.max_attempts - t.attempts} attempts left)"
        changed.append(t)
        changed += sync_containers(tree, t.milestone_id)
    else:
        m = tree.milestone(a.milestone_id)
        if approved:
            m.status, m.note, m.finished_at = "done", None, now()
            msg = f"M{m.seq} {m.title}: approved"
        else:
            m.status, m.note, m.finished_at = "failed", f"rejected by {who}: {note}", now()
            msg = f"M{m.seq} {m.title}: rejected. Replan with: forgeone goal replan {g.id} --reason \"...\""
        changed.append(m)

    if g.status == "waiting" and not [x for x in pending if x.id != a.id]:
        g.status = "paused"
        g.note = (f"milestone rejected: replan with forgeone goal replan {g.id} --reason \"...\""
                  if not approved and not a.task_id else f"ready to continue: forgeone goal run {g.id}")
        changed.append(g)
    store.save(*changed)
    return msg
