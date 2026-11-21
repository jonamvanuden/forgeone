"""ForgeOne Control Room: JSON API + single-page UI.

Run from the ForgeOne root:
    uvicorn dashboard.server:app --host 127.0.0.1 --port 8501
"""
from __future__ import annotations

import datetime as dt
import decimal
import difflib
import json
from pathlib import Path

import yaml
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db

load_dotenv()
STATIC = Path(__file__).parent / "static"

# USD per million tokens: (input, output). Keep in sync with the Claude pricing page.
# Haiku 5.5 is billed at a higher rate when a single prompt exceeds 100k input tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-5-5:long": (0.50, 2.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def price(model: str | None, long_prompt: bool, tin, tout) -> float | None:
    """Estimated USD cost for a group of calls; None if the model's price is unknown."""
    if not model:
        return None
    key = f"{model}:long" if long_prompt and f"{model}:long" in PRICES else model
    if key not in PRICES:
        return None
    pin, pout = PRICES[key]
    return (float(tin or 0) * pin + float(tout or 0) * pout) / 1_000_000


def sum_costs(rows, key_field: str | None = None) -> dict:
    """Aggregate priced rows into {key: {"cost": float, "unpriced": bool}}."""
    out: dict = {}
    for r in rows:
        k = r[key_field] if key_field else None
        c = price(r["model"], r["long_prompt"], r["tin"], r["tout"])
        slot = out.setdefault(k, {"cost": 0.0, "unpriced": False})
        if c is None:
            slot["unpriced"] = True
        else:
            slot["cost"] += c
    return out


def _default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


class JSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, default=_default, ensure_ascii=False).encode()


def goal_title(goal: str | None, fallback: str | None = None) -> str:
    for line in (goal or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return fallback or "Untitled goal"


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    # multi-line strings (system prompts) as readable literal blocks
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def to_yaml(body) -> str:
    return yaml.dump(body, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def spec_changes(old: dict | None, new: dict | None) -> list[str]:
    """Top-level fields that changed between two specs."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    keys = list(dict.fromkeys([*new.keys(), *old.keys()]))
    return [k for k in keys if old.get(k) != new.get(k)]


# ------------------------------------------------------------------ API
def api_overview(request: Request):
    return JSON(db.one(db.OVERVIEW))


def _goals_with_cost() -> list[dict]:
    goals = db.query(db.GOALS)
    costs = sum_costs(db.query(db.COST_BY_GOAL), "key")
    for g in goals:
        g["title"] = goal_title(g["goal"], g["name"])
        g["strip"] = g["strip"] or []
        g["cost"] = costs.get(g["key"], {}).get("cost", 0.0)
    return goals


def api_goals(request: Request):
    return JSON(_goals_with_cost())


def api_goal(request: Request):
    key = request.path_params["key"]
    goals = [g for g in _goals_with_cost() if g["key"] == key]
    if not goals:
        raise HTTPException(404, "Goal not found")
    g = goals[0]
    g["title"] = goal_title(g["goal"], g["name"])
    g["runs"] = db.query(db.GOAL_RUNS, {"key": key})
    run_costs = sum_costs(db.query(db.COST_BY_RUN, {"run_ids": [r["run_id"] for r in g["runs"]]}), "run_id")
    for r in g["runs"]:
        r["cost"] = run_costs.get(r["run_id"], {}).get("cost", 0.0)
    # a "version" = a distinct spec; consecutive identical specs are the same version
    specs, prev = [], None
    for row in db.query(db.GOAL_SPECS, {"key": key}):
        body = row["body"] or {}
        if specs and body == prev:
            specs[-1]["builds"] += 1
            specs[-1]["last_ts"] = row["ts"]
            specs[-1]["last_run"] = row["run_id"]
            continue
        specs.append({"version": len(specs) + 1, "run_id": row["run_id"], "ts": row["ts"],
                      "last_run": row["run_id"], "last_ts": row["ts"], "builds": 1, "name": row["name"],
                      "changed": spec_changes(prev, body) if prev is not None else [],
                      "max_steps": body.get("max_steps"), "tools": body.get("tools")})
        prev = body
    g["specs"] = list(reversed(specs))
    return JSON(g)


def api_run(request: Request):
    run_id = request.path_params["run_id"]
    run = db.one(db.RUN, {"run_id": run_id})
    if not run:
        raise HTTPException(404, "Build not found")
    run["title"] = goal_title(run["goal"], run["name"])
    run["nodes"] = db.query(db.RUN_NODES, {"run_id": run_id})
    run["calls"] = db.query(db.RUN_CALLS, {"run_id": run_id})
    for c in run["calls"]:
        c["cost"] = price(c["model"], (c["input_tokens"] or 0) > 100_000, c["input_tokens"], c["output_tokens"])
    run["cost"] = sum(c["cost"] or 0 for c in run["calls"])
    run["decisions"] = db.query(db.RUN_DECISIONS, {"run_id": run_id})
    arts = []
    for a in db.query(db.RUN_ARTIFACTS, {"run_id": run_id}):
        prev = db.one(db.PREVIOUS_ARTIFACT, {"kind": a["kind"], "name": a["name"], "ts": a["ts"]})
        cur_y = to_yaml(a["body"])
        diff = None
        if prev:
            diff = "".join(difflib.unified_diff(
                to_yaml(prev["body"]).splitlines(True), cur_y.splitlines(True),
                fromfile=f"build {prev['run_id']}", tofile=f"build {run_id}", n=2))
        arts.append({**a, "yaml": cur_y, "diff": diff,
                     "previous_run": prev["run_id"] if prev else None,
                     "changed": spec_changes(prev["body"], a["body"]) if prev else []})
    run["artifacts"] = arts
    run["events"] = db.query(db.RUN_EVENTS, {"run_id": run_id})
    return JSON(run)


def _delta(now, prev):
    """Relative change in percent, or None when there is no baseline."""
    now, prev = float(now or 0), float(prev or 0)
    return None if prev == 0 else round((now - prev) / prev * 100)


def api_home(request: Request):
    k = db.one(db.WEEK_KPIS)
    days = {d["day"].isoformat() if hasattr(d["day"], "isoformat") else str(d["day"]): d
            for d in db.query(db.BUILDS_BY_DAY)}
    for d in days.values():
        d["cost"] = 0.0
    spend_7d = spend_prev = 0.0
    today = dt.date.today()
    for u in db.query(db.USAGE_BY_DAY):
        c = price(u["model"], u["long_prompt"], u["tin"], u["tout"]) or 0.0
        day = u["day"] if isinstance(u["day"], dt.date) else dt.date.fromisoformat(str(u["day"])[:10])
        if day.isoformat() in days:
            days[day.isoformat()]["cost"] += c
        age = (today - day).days
        if age < 7:
            spend_7d += c
        elif age < 14:
            spend_prev += c

    models: dict = {}
    for u in db.query(db.USAGE_BY_MODEL):
        m = models.setdefault(u["model"] or "unknown", {"model": u["model"] or "unknown", "calls": 0, "errors": 0,
                                                        "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                                                        "priced": True, "avg_latency_ms": u["avg_latency_ms"]})
        m["calls"] += u["calls"]; m["errors"] += u["errors"]
        m["tokens_in"] += u["tin"]; m["tokens_out"] += u["tout"]
        c = price(u["model"], u["long_prompt"], u["tin"], u["tout"])
        if c is None:
            m["priced"] = False
        else:
            m["cost"] += c

    # activity feed: recent builds + spec versions that changed something
    feed = []
    for r in db.query(db.RECENT_RUNS):
        title = goal_title(r["goal"], r["name"])
        feed.append({"kind": "build." + (r["status"] or "unknown"), "ts": r["ended_at"] or r["started_at"],
                     "run_id": r["run_id"], "goal_key": r["goal_key"], "title": title,
                     "duration_ms": r["duration_ms"], "error": r["error"]})
    prev_by_name: dict = {}
    for a in db.query(db.RECENT_SPECS):
        prev = prev_by_name.get(a["name"])
        if prev is not None and prev != a["body"]:
            feed.append({"kind": "spec.changed", "ts": a["ts"], "run_id": a["run_id"], "name": a["name"],
                         "changed": spec_changes(prev, a["body"])})
        prev_by_name[a["name"]] = a["body"]
    feed.sort(key=lambda f: str(f["ts"]), reverse=True)

    finished_7d, finished_prev = k["finished_7d"] or 0, k["finished_prev"] or 0
    rate = (k["passed_7d"] / finished_7d * 100) if finished_7d else None
    rate_prev = (k["passed_prev"] / finished_prev * 100) if finished_prev else None
    goals = _goals_with_cost()
    return JSON({
        "kpis": {
            "builds_7d": k["builds_7d"], "builds_delta": _delta(k["builds_7d"], k["builds_prev"]),
            "success_rate": rate, "success_delta": None if rate is None or rate_prev is None else round(rate - rate_prev),
            "spend_7d": spend_7d, "spend_delta": _delta(spend_7d, spend_prev),
            "median_ms": k["median_ms_7d"], "median_delta": _delta(k["median_ms_7d"], k["median_ms_prev"]),
            "running": k["running"], "last_build": k["last_build"],
        },
        "days": list(days.values()),
        "models": sorted(models.values(), key=lambda m: m["cost"], reverse=True),
        "steps": db.query(db.STEP_STATS),
        "feed": feed[:14],
        "goals": goals,
        "prices": {m: {"input": p[0], "output": p[1]} for m, p in PRICES.items()},
    })


def api_call(request: Request):
    call = db.one(db.CALL, {"id": int(request.path_params["call_id"])})
    if not call:
        raise HTTPException(404, "Call not found")
    call["cost"] = price(call["model"], (call["input_tokens"] or 0) > 100_000,
                         call["input_tokens"], call["output_tokens"])
    return JSON(call)


def api_errors(request: Request):
    """One entry per failure: a failed model call, its step and its build share one error.
    Keep the most useful record (the one with a traceback) and count the duplicates."""
    rank = {"node.error": 0, "llm.error": 1, "run.error": 2}
    merged: dict[tuple, dict] = {}
    for e in db.query(db.ERRORS):
        k = (e["run_id"], e["error"])
        cur = merged.get(k)
        if cur is None:
            merged[k] = {**e, "related": 1}
        else:
            cur["related"] += 1
            if rank.get(e["event"], 9) < rank.get(cur["event"], 9):
                merged[k] = {**e, "related": cur["related"]}
    return JSON(sorted(merged.values(), key=lambda e: e["ts"], reverse=True))


def index(request: Request):
    return FileResponse(STATIC / "index.html")


async def on_error(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSON({"error": exc.detail}, status_code=exc.status_code)
    return JSON({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


routes = [
    Route("/api/overview", api_overview),
    Route("/api/home", api_home),
    Route("/api/goals", api_goals),
    Route("/api/goals/{key}", api_goal),
    Route("/api/runs/{run_id}", api_run),
    Route("/api/calls/{call_id:int}", api_call),
    Route("/api/errors", api_errors),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
    Route("/", index),
]


async def security_headers(request, call_next):
    resp: Response = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app = Starlette(routes=routes, exception_handlers={Exception: on_error, HTTPException: on_error})
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
