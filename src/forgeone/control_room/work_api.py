"""Control Room API for the manager view: goals -> milestones -> tasks -> actions. Read-only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from starlette.exceptions import HTTPException
from starlette.requests import Request

from ..config import settings
from ..work.model import FAILURE_REASONS, GoalTree, Task
from ..work.store import NotFound, PgStore
from ..work.support import analyze
from ..work.views import current_milestone, progress, stage_states, waiting_on_you

KINDS = ["plan", "execute", "verify", "edge_test", "approve"]


def _store() -> PgStore:
    return PgStore(settings().database_url)


def _trees() -> list[GoalTree] | None:
    """All goals with their trees; None when the tables do not exist yet."""
    try:
        store = _store()
        return [store.load(g.id) for g in store.goals()]
    except Exception as e:  # noqa: BLE001
        if "does not exist" in str(e) or "relation" in str(e):
            return None
        raise


def _task_row(tree: GoalTree, t: Task) -> dict:
    return {**t.model_dump(include={"id", "code", "title", "type", "difficulty", "actual_difficulty", "risk",
                                    "reversible", "status", "attempts", "max_attempts", "cost_usd", "est_cost_usd",
                                    "duration_ms", "depth", "parent_id", "executor", "model", "approver",
                                    "failure_reason", "failure_detail", "depends_on"}),
            "stages": stage_states(tree, t), "container": not t.stages}


def _card(tree: GoalTree) -> dict:
    g = tree.goal
    cur = current_milestone(tree)
    ms = tree.active_milestones()
    return {**g.model_dump(include={"id", "title", "status", "note", "agent", "agent_type", "autonomy", "budget_usd",
                                    "cost_usd", "plan_version", "created_at", "updated_at", "finished_at"}),
            "progress": progress(tree),
            "milestones": [{"id": m.id, "seq": m.seq, "title": m.title, "status": m.status, **progress(tree, m.id)}
                           for m in ms],
            "current": ({"seq": cur.seq, "title": cur.title, "status": cur.status,
                         "index": ms.index(cur) + 1} if cur else None),
            "waiting": len(waiting_on_you(tree))}


def api_work(request: Request):
    trees = _trees()
    if trees is None:
        return {"migrated": False}
    since7 = datetime.now(timezone.utc) - timedelta(days=7)
    since30 = datetime.now(timezone.utc) - timedelta(days=30)
    leaves = [t for tr in trees for t in tr.leaf_tasks()]
    done7 = [t for t in leaves if t.status == "done" and t.finished_at and t.finished_at >= since7]
    closed30 = [t for t in leaves if t.status in ("done", "failed") and t.finished_at and t.finished_at >= since30]
    first_try = [t for t in closed30 if t.status == "done" and t.attempts == 1]
    inbox = [w for tr in trees for w in waiting_on_you(tr)]
    attention = []
    for tr in trees:
        g = tr.goal
        if g.status in ("paused", "failed"):
            attention.append({"kind": g.status, "goal_id": g.id, "goal": g.title, "title": g.title,
                              "note": g.note, "since": g.updated_at})
    return {
        "migrated": True,
        "kpis": {
            "active": sum(1 for tr in trees if tr.goal.status in ("planned", "running", "waiting", "paused")),
            "needs_you": len(inbox) + len(attention),
            "done_7d": len(done7),
            "first_try_rate": (len(first_try) / len(closed30)) if closed30 else None,
            "closed_30d": len(closed30),
            "spend_7d": round(sum(a.cost_usd for tr in trees for a in tr.actions if a.started_at >= since7), 6),
        },
        "inbox": sorted(inbox, key=lambda w: w["since"]),
        "attention": attention,
        "goals": [_card(tr) for tr in trees],
    }


def api_work_goal(request: Request):
    from ..work.recovery import interrupted

    try:
        tree = _store().load(request.path_params["goal_id"])
    except NotFound:
        raise HTTPException(404, "goal not found")
    g = tree.goal
    milestones = []
    for m in tree.active_milestones():
        tasks = tree.tasks_of(m.id)
        appr = [a for a in tree.actions if a.milestone_id == m.id and a.task_id is None and a.kind == "approve"]
        milestones.append({**m.model_dump(), **{"progress": progress(tree, m.id)},
                           "tasks": [_task_row(tree, t) for t in tasks],
                           "approval": appr[-1].model_dump(include={"id", "status", "actor", "actor_type", "reason"})
                           if appr else None})
    old = [m for m in tree.milestones if m.status == "superseded"]
    plans = sorted((a for a in tree.actions if a.kind == "plan" and a.input.get("level") == "goal"),
                   key=lambda a: a.started_at)
    spend = {k: round(sum(a.cost_usd for a in tree.actions if a.kind == k), 6) for k in KINDS}
    fails: dict[str, int] = {}
    for a in tree.actions:
        if a.status == "failed" and a.failure_reason:
            fails[a.failure_reason] = fails.get(a.failure_reason, 0) + 1
    leaves = tree.leaf_tasks()
    closed = [t for t in leaves if t.status in ("done", "failed")]
    return {
        "goal": g.model_dump(), "progress": progress(tree), "milestones": milestones,
        "interrupted": interrupted(tree),
        "superseded": [{"seq": m.seq, "title": m.title, "plan_version": m.plan_version, "note": m.note} for m in old],
        "plans": [{"version": a.input.get("plan_version"), "reason": a.input.get("reason"), "reasoning": a.reason,
                   "at": a.started_at, "cost_usd": a.cost_usd} for a in plans],
        "waiting": waiting_on_you(tree),
        "spend": spend,
        "failures": [{"reason": k, "n": v, "means": FAILURE_REASONS.get(k, "")} for k, v in
                     sorted(fails.items(), key=lambda kv: -kv[1])],
        "stats": {
            "actions": len(tree.actions),
            "first_try": sum(1 for t in closed if t.status == "done" and t.attempts == 1),
            "closed": len(closed),
            "attempts": sum(t.attempts for t in leaves),
            "est_cost": round(sum(t.est_cost_usd or 0 for t in leaves), 6),
        },
    }


def api_work_task(request: Request):
    store = _store()
    tid = request.path_params["task_id"]
    found = store.find(tid)
    if not found or found[0] != "task":
        raise HTTPException(404, "task not found")
    tree = store.load(found[1])
    t = tree.task(tid)
    m = tree.milestone(t.milestone_id)
    brief = lambda x: {"id": x.id, "code": x.code, "title": x.title, "status": x.status}  # noqa: E731
    acts = tree.actions_of(t.id)
    verdicts = [a for a in acts if a.kind == "verify" and a.criteria]
    waiting = next((a for a in acts if a.kind == "approve" and a.status == "pending"), None)
    return {
        "task": t.model_dump(),
        "goal": tree.goal.model_dump(include={"id", "title", "status", "agent", "autonomy"}),
        "milestone": {"id": m.id, "seq": m.seq, "title": m.title, "status": m.status},
        "parent": brief(tree.task(t.parent_id)) if t.parent_id else None,
        "children": [_task_row(tree, k) for k in sorted(tree.children(t.id), key=lambda k: k.seq)],
        "depends_on": [brief(d) for d in (tree.task(i) for i in t.depends_on) if d],
        "dependents": [brief(x) for x in tree.tasks if t.id in x.depends_on and x.status != "superseded"],
        "stages": stage_states(tree, t),
        "actions": [a.model_dump() for a in acts],
        "last_verdict": verdicts[-1].criteria if verdicts else None,
        "waiting": waiting.model_dump(include={"id", "started_at", "input"}) if waiting else None,
    }


def api_support(request: Request) -> dict:
    trees = _trees()
    if trees is None:
        return {"migrated": False}
    raw = request.query_params.get("days", "30")
    days = None if raw == "all" else int(raw) if raw in ("7", "30", "90") else 30
    scope = "builds" if request.query_params.get("scope") == "builds" else "all"
    return {"migrated": True, **analyze(trees, days, scope)}
