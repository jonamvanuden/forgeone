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


def api_goals(request: Request):
    goals = db.query(db.GOALS)
    for g in goals:
        g["title"] = goal_title(g["goal"], g["name"])
        g["strip"] = g["strip"] or []
    return JSON(goals)


def api_goal(request: Request):
    key = request.path_params["key"]
    goals = [g for g in db.query(db.GOALS) if g["key"] == key]
    if not goals:
        raise HTTPException(404, "Goal not found")
    g = goals[0]
    g["title"] = goal_title(g["goal"], g["name"])
    g["runs"] = db.query(db.GOAL_RUNS, {"key": key})
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


def api_call(request: Request):
    call = db.one(db.CALL, {"id": int(request.path_params["call_id"])})
    if not call:
        raise HTTPException(404, "Call not found")
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
