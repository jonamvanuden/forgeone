"""Starting and steering work from the Control Room: a new goal or build, continue, retry a task
from where it failed, approve or reject, raise the budget, replan, cancel.

The dashboard is public and read-only. These are its only write actions, and each needs the
action password: FORGEONE_ACTION_TOKEN in .env (at least 16 characters). Without it the buttons
are switched off. The browser sends it in the X-ForgeOne-Token header, so another site can't
trigger an action (no cookies, no CSRF).

Work runs as a separate `forgeone` process, exactly like on the command line, so the dashboard
stays responsive and a restart of the dashboard does not stop a running goal. Each job writes
runs/_jobs/<job>.json (what was started) and <job>.log (its output).

Limits: a budget is required and at most FORGEONE_MAX_GOAL_BUDGET (default $5); at most 2 jobs
at once; one job per goal; a build publishes to a branch or not at all, never to main.
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ..config import settings

MAX_JOBS = 2
MIN_TOKEN = 16
_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}
_failures: list[float] = []            # times of wrong passwords, for a simple lockout


class ActionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- settings and auth
def _token() -> str | None:
    settings()                          # loads .env
    t = os.environ.get("FORGEONE_ACTION_TOKEN", "").strip()
    return t if len(t) >= MIN_TOKEN else None


def max_budget() -> float:
    try:
        return float(os.environ.get("FORGEONE_MAX_GOAL_BUDGET", "5"))
    except ValueError:
        return 5.0


def enabled() -> bool:
    return _token() is not None


def check_token(given: str | None) -> None:
    token = _token()
    if token is None:
        raise ActionError(403, "Actions are switched off: set FORGEONE_ACTION_TOKEN (16+ characters) in .env "
                               "and restart the dashboard.")
    now = time.time()
    with _lock:
        _failures[:] = [t for t in _failures if now - t < 600]
        if len(_failures) >= 10:
            raise ActionError(429, "Too many wrong passwords. Try again in 10 minutes.")
        if not given or not hmac.compare_digest(given.encode(), token.encode()):
            _failures.append(now)
            raise ActionError(401, "Wrong action password.")


# ---------------------------------------------------------------- jobs
def jobs_dir() -> Path:
    d = settings().runs_dir / "_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _alive(job: dict) -> bool:
    p = _procs.get(job["id"])
    if p is not None:
        return p.poll() is None
    pid = job.get("pid")                # started before a dashboard restart
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    return not cmdline.exists() or b"forgeone" in cmdline.read_bytes()


def _status(job: dict) -> dict:
    job = dict(job)
    if _alive(job):
        job["state"] = "running"
    else:
        p = _procs.get(job["id"])
        code = p.returncode if p is not None else job.get("exit_code")
        job["exit_code"] = code
        job["state"] = "finished" if code == 0 else "stopped" if code is not None else "ended"
    return job


def list_jobs(limit: int = 20) -> list[dict]:
    out = []
    for f in sorted(jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            out.append(_status(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def job_log(job_id: str, tail: int = 200) -> dict:
    if not re.fullmatch(r"J-[0-9a-f]{8}", job_id or ""):
        raise ActionError(404, "Job not found")
    meta = jobs_dir() / f"{job_id}.json"
    if not meta.exists():
        raise ActionError(404, "Job not found")
    job = _status(json.loads(meta.read_text(encoding="utf-8")))
    log = jobs_dir() / f"{job_id}.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:] if log.exists() else []
    return {**job, "log": lines}


def _running_for(goal_id: str) -> dict | None:
    return next((j for j in list_jobs(50) if j["state"] == "running" and j.get("goal_id") == goal_id), None)


def _start(kind: str, args: list[str], goal_id: str | None, title: str, extra: dict | None = None) -> dict:
    with _lock:
        running = [j for j in list_jobs(50) if j["state"] == "running"]
        if len(running) >= MAX_JOBS:
            raise ActionError(409, f"{len(running)} jobs are already running. Wait until one finishes.")
        if goal_id and any(j.get("goal_id") == goal_id for j in running):
            raise ActionError(409, "This goal is already running.")
        job_id = f"J-{uuid.uuid4().hex[:8]}"
        log = jobs_dir() / f"{job_id}.log"
        cmd = [sys.executable, "-c", "from forgeone.cli import main; main()", *args]
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        with open(log, "wb") as fh:
            p = subprocess.Popen(cmd, cwd=settings().home, stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        _procs[job_id] = p
        job = {"id": job_id, "kind": kind, "goal_id": goal_id, "title": title, "pid": p.pid,
               "command": "forgeone " + " ".join(_quote(a) for a in args),
               "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), **(extra or {})}
        (jobs_dir() / f"{job_id}.json").write_text(json.dumps(job, indent=1), encoding="utf-8")
        threading.Thread(target=_reap, args=(job_id,), daemon=True).start()
    return _status(job)


def _reap(job_id: str) -> None:
    p = _procs.get(job_id)
    if p is None:
        return
    code = p.wait()
    meta = jobs_dir() / f"{job_id}.json"
    try:
        job = json.loads(meta.read_text(encoding="utf-8"))
        job["exit_code"] = code
        job["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        meta.write_text(json.dumps(job, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _quote(a: str) -> str:
    return a if re.fullmatch(r"[\w.:/@=+-]+", a) else "'" + a.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- validation
def _text(v, name: str, limit: int, required: bool = False) -> str:
    s = str(v or "").strip()
    if required and not s:
        raise ActionError(400, f"{name} is required.")
    if len(s) > limit:
        raise ActionError(400, f"{name} is too long (max {limit:,} characters).")
    return s


def _lines(v, name: str) -> list[str]:
    items = v if isinstance(v, list) else str(v or "").splitlines()
    items = [str(x).strip() for x in items if str(x).strip()]
    if len(items) > 20:
        raise ActionError(400, f"At most 20 {name}.")
    return [_text(x, name, 500) for x in items]


def _budget(v) -> float:
    try:
        b = float(v)
    except (TypeError, ValueError):
        raise ActionError(400, "Set a budget in USD.") from None
    cap = max_budget()
    if not 0 < b <= cap:
        raise ActionError(400, f"The budget must be more than $0 and at most ${cap:.2f} "
                               "(FORGEONE_MAX_GOAL_BUDGET).")
    return round(b, 2)


def agents() -> list[str]:
    from ..workspace import load_registry

    try:
        return sorted(load_registry())
    except Exception:  # noqa: BLE001
        return []


def _agent(v, required: bool) -> str | None:
    a = str(v or "").strip()
    if not a:
        if required:
            raise ActionError(400, "Choose an agent.")
        return None
    if a not in agents():
        raise ActionError(400, f"Unknown agent {a!r}: add it to agents.yaml first.")
    return a


# ---------------------------------------------------------------- actions
def new_goal(body: dict) -> dict:
    """body: kind goal|build, title, description, criteria, constraints, agent, budget,
    autonomy, context, publish, run."""
    from ..work.model import new_id

    kind = body.get("kind", "goal")
    budget = _budget(body.get("budget"))
    gid = new_id("G")
    if kind == "build":
        agent = _agent(body.get("agent"), required=True)
        publish = body.get("publish", "branch")
        if publish not in ("branch", "none"):
            raise ActionError(400, "From the dashboard a build publishes to a branch or not at all.")
        args = ["build", "--agent", agent, "--publish", publish, "--budget", str(budget), "--goal-id", gid]
        instructions = _text(body.get("description"), "Instructions", 20_000)
        extra = {"agent": agent, "publish": publish, "budget": budget}
        if instructions:                # otherwise goal.md from the agent's repository
            f = jobs_dir() / f"{gid}.goal.md"
            f.write_text(instructions + "\n", encoding="utf-8")
            args += ["--goal", str(f)]
        return _start("build", args, gid, f"Build {agent}", extra)
    if kind != "goal":
        raise ActionError(400, "kind must be goal or build.")
    title = _text(body.get("title"), "Title", 300, required=True)
    args = ["goal", "new", title, "--id", gid, "--budget", str(budget)]
    desc = _text(body.get("description"), "Description", 20_000)
    if desc:
        args += ["--description", desc]
    agent = _agent(body.get("agent"), required=False)
    if agent:
        args += ["--agent", agent]
    autonomy = body.get("autonomy", "supervised")
    if autonomy not in ("supervised", "autonomous"):
        raise ActionError(400, "autonomy must be supervised or autonomous.")
    context = body.get("context", "default")
    if context not in ("lean", "default", "rich"):
        raise ActionError(400, "context must be lean, default or rich.")
    args += ["--autonomy", autonomy, "--context", context]
    for c in _lines(body.get("criteria"), "success criteria"):
        args += ["--criteria", c]
    for c in _lines(body.get("constraints"), "constraints"):
        args += ["--constraint", c]
    if body.get("run", True):
        args.append("--run")
    return _start("goal", args, gid, title, {"budget": budget, "autonomy": autonomy, "context": context})


def _goal_id(goal_id: str) -> str:
    from ..work.approvals import normalize_id

    gid = normalize_id(goal_id or "")
    if not re.fullmatch(r"G-[0-9a-f]{6}", gid):
        raise ActionError(404, "Goal not found")
    return gid


def _store():
    from ..work import PgStore

    return PgStore()


def _load(gid: str):
    try:
        return _store().load(gid)
    except Exception:  # noqa: BLE001
        raise ActionError(404, "Goal not found") from None


def _idle(gid: str):
    """The goal, if nothing is working on it. A goal that says 'running' without a job and
    without any activity for 15 minutes was interrupted (restart, crash) and counts as idle."""
    from ..work.recovery import interrupted

    if _running_for(gid):
        raise ActionError(409, "This goal is running right now. Wait until the job finishes.")
    tree = _load(gid)
    if tree.goal.status == "running" and not interrupted(tree):
        raise ActionError(409, "This goal is running (maybe from the command line). Wait until it stops.")
    return tree.goal


def run_goal(goal_id: str) -> dict:
    gid = _goal_id(goal_id)
    g = _idle(gid)
    if g.status in ("done", "cancelled", "failed"):
        hint = " Retry the failed task or replan the goal first." if g.status == "failed" else ""
        raise ActionError(409, f"This goal is {g.status}.{hint}")
    if g.status == "paused" and g.budget_usd is not None and g.cost_usd >= g.budget_usd:
        raise ActionError(409, "The budget is used up: raise it first.")
    return _start("run", ["goal", "run", gid], gid, g.title)


def retry_task(goal_id: str, task: str, body: dict) -> dict:
    """Reset a failed task (and what it blocked), then continue the goal from there."""
    from ..work.recovery import CannotRetry, retry_task as reset

    gid = _goal_id(goal_id)
    g = _idle(gid)
    note = _text(body.get("note"), "Note", 2000) or None
    try:
        msg = reset(_store(), gid, _text(task, "Task", 40, required=True), note=note, who="you (dashboard)")
    except CannotRetry as e:
        raise ActionError(409, str(e)) from None
    if body.get("run", True) and not (g.budget_usd is not None and g.cost_usd >= g.budget_usd):
        return {"message": msg, "job": _start("run", ["goal", "run", gid], gid, g.title)}
    return {"message": msg}


def decide(record_id: str, body: dict) -> dict:
    """Approve or reject a task, a go-ahead or a milestone; then continue the goal."""
    from ..work.approvals import NotWaiting, decide as do_decide, normalize_id

    rid = normalize_id(record_id or "")
    if not re.fullmatch(r"[TMGA]-[0-9a-f]{6}", rid):
        raise ActionError(404, "Not found")
    approve = bool(body.get("approve"))
    note = _text(body.get("note"), "Note", 2000) or None
    if not approve and not note:
        raise ActionError(400, "Say what must change: a rejection needs a note.")
    found = _store().find(rid)
    if not found:
        raise ActionError(404, "Not found")
    gid = found[1]
    g = _idle(gid)
    try:
        msg = do_decide(_store(), rid, approve, note=note, who="you (dashboard)")
    except NotWaiting as e:
        raise ActionError(409, str(e)) from None
    out = {"message": msg}
    if body.get("run", True) and g.status not in ("cancelled", "done"):
        try:
            out["job"] = run_goal(gid)
        except ActionError as e:          # e.g. a rejected milestone made the goal fail: show why
            out["message"] += f" {e.message}"
    return out


def set_budget(goal_id: str, body: dict) -> dict:
    gid = _goal_id(goal_id)
    _idle(gid)
    usd = _budget(body.get("usd"))
    tree = _load(gid)
    g = tree.goal
    if usd <= g.cost_usd:
        raise ActionError(400, f"{g.id} already spent ${g.cost_usd:.2f}: the budget must be higher.")
    g.budget_usd = usd
    if g.status == "paused":
        g.note = f"budget raised to ${usd:.2f}: continue to run it"
    _store().save(g)
    return {"message": f"Budget set to ${usd:.2f}."}


def cancel(goal_id: str) -> dict:
    gid = _goal_id(goal_id)
    g = _idle(gid)
    if g.status in ("done", "cancelled"):
        raise ActionError(409, f"This goal is already {g.status}.")
    g.status, g.note = "cancelled", "cancelled by you (dashboard)"
    _store().save(g)
    return {"message": "Goal cancelled."}


def replan(goal_id: str, body: dict) -> dict:
    gid = _goal_id(goal_id)
    g = _idle(gid)
    if g.playbook:
        raise ActionError(409, f"This goal follows the fixed plan of playbook {g.playbook}: retry the failed task instead.")
    if g.status in ("done", "cancelled"):
        raise ActionError(409, f"This goal is {g.status}.")
    reason = _text(body.get("reason"), "Reason", 2000, required=True)
    return {"job": _start("replan", ["goal", "replan", gid, "--reason", reason], gid, f"Replan: {g.title}")}
