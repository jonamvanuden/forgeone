"""Starting work from the Control Room: a new goal, a build, or continuing a goal.

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


def run_goal(goal_id: str) -> dict:
    from ..work.approvals import normalize_id

    gid = normalize_id(goal_id or "")
    if not re.fullmatch(r"G-[0-9a-f]{6}", gid):
        raise ActionError(404, "Goal not found")
    if _running_for(gid):
        raise ActionError(409, "This goal is already running.")
    from ..work import PgStore

    try:
        g = PgStore().load(gid).goal
    except Exception:  # noqa: BLE001
        raise ActionError(404, "Goal not found") from None
    if g.status in ("done", "cancelled", "failed", "running"):
        hint = " Replan it first: forgeone goal replan." if g.status == "failed" else ""
        raise ActionError(409, f"This goal is {g.status}.{hint}")
    if g.status == "paused" and g.budget_usd is not None and g.cost_usd >= g.budget_usd:
        raise ActionError(409, "The budget is used up: raise it first with forgeone goal budget.")
    return _start("run", ["goal", "run", gid], gid, g.title)
