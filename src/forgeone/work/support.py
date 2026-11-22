"""Support: what went wrong, what was harder than planned, and what might not need AI.

Read-only analysis over goal trees, shared by `forgeone support` and the Control Room's
Support page. Every number here comes from the records the engine already writes (tasks and
actions); nothing is estimated by a model.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .model import FAILURE_REASONS, GoalTree, Task

INFRA = {"tool_error", "model_error", "timeout", "budget_exceeded"}   # the system failed, not the work
AI_CHECKS = ("verify", "edge_test", "approve")
MIN_CHECK_RUNS = 5          # an AI check needs this many runs before "never caught anything" means something
MIN_REPEATS = 3             # a task pattern needs this many runs before it counts as "repeats"


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", title.lower())).strip()


def _uses_ai(t: Task) -> bool:
    return t.executor in ("llm", "agent") or (t.executor == "step" and bool(t.model))


def _brief(tree: GoalTree, t: Task) -> dict:
    return {"id": t.id, "code": t.code, "title": t.title, "type": t.type, "goal_id": tree.goal.id,
            "goal": tree.goal.title, "agent_type": tree.goal.agent_type, "status": t.status}


def _wasted(tree: GoalTree, t: Task) -> float:
    """Money spent on attempts that did not become the accepted result."""
    acts = tree.actions_of(t.id)
    if t.status in ("failed", "blocked") or (t.status == "superseded" and t.failure_reason):
        return sum(a.cost_usd for a in acts)
    if t.status == "done":
        return sum(a.cost_usd for a in acts if a.attempt < t.attempts and not a.input.get("gate"))
    return 0.0


def _next_step(tree: GoalTree, t: Task) -> str:
    g = tree.goal
    if t.status == "superseded":
        return "replaced by a newer plan: nothing to do"
    if t.status == "blocked":
        return f"depends on a task that failed; fix that one, then: forgeone goal replan {g.id} --reason \"...\""
    if g.status in ("failed", "paused", "waiting"):
        return f"forgeone goal replan {g.id} --reason \"{(t.failure_reason or 'failed').replace('_', ' ')} in {t.code}\""
    return f"forgeone goal show {g.id} --actions"


def analyze(trees: list[GoalTree], days: int | None = 30, scope: str = "all", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days) if days else None
    if scope == "builds":
        trees = [tr for tr in trees if tr.goal.agent_type == "builder"]

    def recent(ts) -> bool:
        return since is None or (ts is not None and ts >= since)

    work = [(tr, t) for tr in trees for t in tr.tasks if t.stages]              # leaf tasks, all plan versions
    closed = [(tr, t) for tr, t in work if t.status in ("done", "failed") and recent(t.finished_at)]

    # ---- failed and blocked -------------------------------------------------
    failed = []
    for tr, t in work:
        is_failed = t.status in ("failed", "blocked") or (t.status == "superseded" and t.failure_reason and t.finished_at)
        if not is_failed or not recent(t.finished_at or t.updated_at):
            continue
        last = next((a for a in reversed(tr.actions_of(t.id)) if a.status == "failed"), None)
        failed.append({**_brief(tr, t), "replaced": t.status == "superseded", "failure_reason": t.failure_reason,
                       "means": FAILURE_REASONS.get(t.failure_reason or "", ""), "detail": t.failure_detail,
                       "last_feedback": last.feedback if last else None, "attempts": t.attempts,
                       "cost_usd": round(sum(a.cost_usd for a in tr.actions_of(t.id)), 6),
                       "when": t.finished_at or t.updated_at, "next": _next_step(tr, t)})
    failed.sort(key=lambda r: r["when"], reverse=True)

    # ---- what goes wrong ----------------------------------------------------
    reasons: dict[str, dict] = {}
    for tr in trees:
        for a in tr.actions:
            if a.status != "failed" or not a.failure_reason or not recent(a.started_at):
                continue
            r = reasons.setdefault(a.failure_reason, {"reason": a.failure_reason, "n": 0, "stages": defaultdict(int),
                                                      "means": FAILURE_REASONS.get(a.failure_reason, ""),
                                                      "kind": "infrastructure" if a.failure_reason in INFRA else "quality",
                                                      "example": None, "_ts": None})
            r["n"] += 1
            r["stages"][a.kind] += 1
            if r["_ts"] is None or a.started_at > r["_ts"]:
                t = tr.task(a.task_id) if a.task_id else None
                r["_ts"] = a.started_at
                r["example"] = {**(_brief(tr, t) if t else {"goal_id": tr.goal.id, "goal": tr.goal.title}),
                                "text": a.reason, "when": a.started_at}
    total = sum(r["n"] for r in reasons.values()) or 1
    what_goes_wrong = sorted(({**{k: v for k, v in r.items() if k != "_ts"}, "stages": dict(r["stages"]),
                               "share": r["n"] / total} for r in reasons.values()), key=lambda r: -r["n"])

    # ---- harder than planned ------------------------------------------------
    hard = []
    for tr, t in closed:
        if t.status != "done" or not _uses_ai(t):
            continue
        acts = tr.actions_of(t.id)
        rejections = sum(1 for a in acts if a.kind in AI_CHECKS and a.status == "failed")
        gap = (t.actual_difficulty or t.difficulty) - t.difficulty
        ratio = (t.cost_usd / t.est_cost_usd) if t.est_cost_usd else None
        if gap < 1 and t.attempts <= 1 and not (ratio and ratio >= 2):
            continue
        why = next((a for a in reversed(acts) if a.status == "failed"), None)
        hard.append({**_brief(tr, t), "difficulty": t.difficulty, "actual": t.actual_difficulty, "gap": round(gap, 1),
                     "attempts": t.attempts, "rejections": rejections, "cost_usd": t.cost_usd,
                     "est_cost_usd": t.est_cost_usd, "cost_ratio": round(ratio, 1) if ratio else None,
                     "why": (f"{why.kind}: {why.reason}" if why and why.reason else None), "when": t.finished_at})
    hard.sort(key=lambda r: (-r["gap"], -r["attempts"]))

    # ---- planner calibration per task type ----------------------------------
    by_type: dict[str, list[Task]] = defaultdict(list)
    for tr, t in closed:
        if _uses_ai(t):
            by_type[t.type].append(t)
    calibration = []
    for typ, ts in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        pred = sum(t.difficulty for t in ts) / len(ts)
        act = sum((t.actual_difficulty or t.difficulty) for t in ts) / len(ts)
        est = sum(t.est_cost_usd or 0 for t in ts)
        cost = sum(t.cost_usd for t in ts)
        verdict = ("harder than predicted" if act - pred >= 0.75 else
                   "easier than predicted" if act - pred <= -0.75 else "about as predicted")
        calibration.append({"type": typ, "tasks": len(ts), "predicted": round(pred, 1), "actual": round(act, 1),
                            "first_try": sum(1 for t in ts if t.status == "done" and t.attempts == 1) / len(ts),
                            "failed": sum(1 for t in ts if t.status == "failed"),
                            "est_cost_usd": round(est, 6), "cost_usd": round(cost, 6), "verdict": verdict})

    # ---- could run without AI -----------------------------------------------
    marked: dict[tuple, list] = defaultdict(list)            # the same kind of task in several goals: one row
    for tr, t in work:
        if t.automatable and _uses_ai(t) and recent(t.created_at) and t.status != "superseded":
            marked[(t.type, _norm(t.title))].append((tr, t))
    flagged = []
    for items in marked.values():
        tr0, t0 = items[-1]
        flagged.append({**_brief(tr0, t0), "hint": t0.automation_hint, "runs": len(items),
                        "goals": len({tr.goal.id for tr, _ in items}),
                        "cost_usd": round(sum(t.cost_usd for _, t in items), 6)})
    flagged.sort(key=lambda r: -r["cost_usd"])
    repeats: dict[tuple, list] = defaultdict(list)
    for tr, t in closed:
        if t.status == "done" and _uses_ai(t):
            repeats[(t.type, _norm(t.title))].append((tr, t))
    repeated = []
    for (typ, _), items in repeats.items():
        ts = [t for _, t in items]
        if len(ts) < MIN_REPEATS or any(t.attempts > 1 for t in ts):
            continue
        act = sum((t.actual_difficulty or t.difficulty) for t in ts) / len(ts)
        if act > 2.5:
            continue
        tr0, t0 = items[-1]
        repeated.append({**_brief(tr0, t0), "runs": len(ts), "goals": len({tr.goal.id for tr, _ in items}),
                         "actual": round(act, 1), "cost_usd": round(sum(t.cost_usd for t in ts), 6)})
    checks: dict[tuple, dict] = {}
    for tr in trees:
        for a in tr.actions:
            if a.kind not in AI_CHECKS or a.actor_type != "ai" or a.status not in ("passed", "failed") or not recent(a.started_at):
                continue
            t = tr.task(a.task_id) if a.task_id else None
            key = (t.type if t else "milestone", a.kind)
            c = checks.setdefault(key, {"type": key[0], "stage": a.kind, "runs": 0, "caught": 0, "cost_usd": 0.0})
            c["runs"] += 1
            c["caught"] += a.status == "failed"
            c["cost_usd"] += a.cost_usd
    idle_checks = sorted(({**c, "cost_usd": round(c["cost_usd"], 6)} for c in checks.values()
                          if c["runs"] >= MIN_CHECK_RUNS and c["caught"] == 0), key=lambda c: -c["cost_usd"])
    check_stats = sorted(({**c, "cost_usd": round(c["cost_usd"], 6), "catch_rate": c["caught"] / c["runs"]}
                          for c in checks.values()), key=lambda c: (-c["runs"]))

    # ---- what the AI checks missed: you rejected what they had passed --------
    missed = []
    for tr in trees:
        for a in tr.actions:
            if (a.kind != "approve" or a.actor_type != "human" or a.status != "failed" or a.input.get("gate")
                    or not a.task_id or not recent(a.started_at)):
                continue
            passed = [x.kind for x in tr.actions_of(a.task_id)
                      if x.attempt == a.attempt and x.kind in AI_CHECKS and x.actor_type == "ai" and x.status == "passed"]
            if passed:
                missed.append({**_brief(tr, tr.task(a.task_id)), "note": a.reason, "passed_by": passed,
                               "when": a.started_at})

    # ---- context: how much each kind of call gets, and whether cutting hurts ----
    ctx: dict[str, dict] = {}
    agent = {"runs": 0, "shortened": 0, "sent": 0, "without": 0, "cache_read": 0}
    for tr in trees:
        for a in tr.actions:
            c = a.context or {}
            if not c.get("sections") or a.status not in ("passed", "failed") or not recent(a.started_at):
                continue
            pol = c.get("policy") or "default"
            k = ctx.setdefault((pol, a.kind), {"policy": pol, "stage": a.kind, "calls": 0, "tokens_est": 0, "billed": 0,
                                               "billed_n": 0, "cut": 0, "cut_passed": 0, "uncut": 0, "uncut_passed": 0,
                                               "tokens_lost": 0, "cost": 0.0})
            k["cost"] += a.cost_usd
            k["calls"] += 1
            k["tokens_est"] += c.get("tokens_est", 0)
            if c.get("tokens_billed"):
                k["billed"] += c["tokens_billed"]
                k["billed_n"] += 1
            was_cut = (c.get("cut") or 0) + (c.get("omitted") or 0) > 0
            k["cut" if was_cut else "uncut"] += 1
            k["cut_passed" if was_cut else "uncut_passed"] += a.status == "passed"
            k["tokens_lost"] += c.get("tokens_lost", 0)
            h = c.get("agent")
            if h:
                agent["runs"] += 1
                agent["shortened"] += h.get("shortened_results", 0)
                agent["sent"] += h.get("sent_tokens_est", 0)
                agent["without"] += h.get("without_shortening_est", 0)
                agent["cache_read"] += h.get("cache_read_tokens", 0)
    context = [{"policy": k["policy"], "stage": k["stage"], "calls": k["calls"],
                "tokens_est": round(k["tokens_est"] / k["calls"]), "cost_per_call": round(k["cost"] / k["calls"], 6),
                "tokens_billed": round(k["billed"] / k["billed_n"]) if k["billed_n"] else None,
                "cut_rate": k["cut"] / k["calls"], "tokens_lost": k["tokens_lost"],
                "pass_when_cut": k["cut_passed"] / k["cut"] if k["cut"] else None,
                "pass_uncut": k["uncut_passed"] / k["uncut"] if k["uncut"] else None}
               for k in sorted(ctx.values(), key=lambda k: (k["stage"], k["policy"]))]

    wasted = round(sum(_wasted(tr, t) for tr, t in work if recent(t.finished_at or t.updated_at)), 6)
    spent = round(sum(a.cost_usd for tr in trees for a in tr.actions if recent(a.started_at)), 6)
    retried = sum(1 for _, t in closed if t.attempts > 1)
    return {
        "days": days, "scope": scope, "goals": len(trees),
        "kpis": {"failed": sum(1 for r in failed if not r["replaced"]), "replaced": sum(1 for r in failed if r["replaced"]),
                 "closed": len(closed), "retried": retried, "retry_rate": retried / len(closed) if closed else None,
                 "harder": len(hard), "wasted_usd": wasted, "spent_usd": spent,
                 "without_ai": len(flagged) + len(repeated) + len(idle_checks), "missed": len(missed)},
        "failed": failed[:50], "what_goes_wrong": what_goes_wrong, "hard": hard[:30], "calibration": calibration,
        "without_ai": {"flagged": flagged[:30], "repeated": repeated[:20], "idle_checks": idle_checks},
        "check_stats": check_stats, "missed": missed[:20], "context": context, "agent_history": agent,
    }
