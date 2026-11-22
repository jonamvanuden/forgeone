"""ForgeOne Control Room: JSON API + single-page UI.

    forgeone control-room            (or)
    uvicorn forgeone.control_room.server:app --host 127.0.0.1 --port 8501
"""
from __future__ import annotations

import datetime as dt
import decimal
import difflib
import json
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..config import settings
from ..llm.pricing import PRICES, call_cost, price
from . import db, work_api

STATIC = Path(__file__).parent / "static"
DOCS = settings().docs_dir   # project documents (HTML + PDF), served at /docs


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
    ov = dict(db.one(db.OVERVIEW) or {})
    ov["needs_you"] = _safe(lambda: db.one(db.NEEDS_YOU)["n"], 0)   # 0 until migration 004 is applied
    return JSON(ov)


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
        c["cost"] = call_cost(c["model"], c["input_tokens"], c["output_tokens"])
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
        if a["kind"] == "report" and isinstance(a["body"], dict):   # reports are markdown, not YAML
            arts.append({**a, "text": a["body"].get("markdown", ""), "yaml": None, "diff": None,
                         "previous_run": None, "changed": []})
            continue
        arts.append({**a, "yaml": cur_y, "diff": diff,
                     "previous_run": prev["run_id"] if prev else None,
                     "changed": spec_changes(prev["body"], a["body"]) if prev else []})
    run["artifacts"] = arts
    run["events"] = db.query(db.RUN_EVENTS, {"run_id": run_id})
    name = run.get("name") or ""
    run["kind"] = "agent" if name == "agent.run" else "work" if name.startswith("goal.") else "build"
    if run["kind"] == "work":
        meta = run.get("meta") or {}
        run["title"] = meta.get("title") or "Goal"
        run["goal_id"] = meta.get("goal_id")
        run["actions"] = _safe(lambda: db.query(db.WORK_ACTIONS, {"run_id": run["run_id"]}), [])
        if not run.get("cost"):
            run["cost"] = round(sum(a["cost_usd"] or 0 for a in run["actions"]), 6)
    run["labels"] = _safe(lambda: db.query(db.RUN_LABELS, {"run_id": run_id}), [])
    run["tool_calls"] = db.query(db.RUN_TOOL_CALLS, {"run_id": run_id})
    if run["kind"] == "agent":
        meta = run.get("meta") or {}
        run["title"] = meta.get("task") or "Agent run"
        run["agent"], run["variant"], run["case_id"] = meta.get("agent"), meta.get("variant"), meta.get("case_id")
        end = next((e["fields"] for e in run["events"] if e["event"] == "agent.end"), None) or {}
        if not run.get("cost") and end.get("cost_usd"):
            run["cost"] = end["cost_usd"]
        if not run.get("llm_calls") and end.get("llm_calls"):
            run["llm_calls"] = end["llm_calls"]
            run["total_input_tokens"] = end.get("tokens_in", 0)
            run["total_output_tokens"] = end.get("tokens_out", 0)
        run["stop_reason"] = end.get("stop_reason")
    return JSON(run)


def _safe(fn, default):
    """For tables added by later migrations: an un-migrated database shows nothing instead of failing."""
    try:
        return fn()
    except Exception:
        return default


def api_quality(request: Request):
    knowledge = _safe(lambda: db.query(db.KNOWLEDGE), None)
    if knowledge is None:
        return JSON({"migrated": False, "knowledge": [], "eval_runs": [], "agent_runs": []})
    runs = db.query(db.AGENT_RUNS)
    costs = sum_costs(db.query(db.AGENT_RUN_COSTS, {"run_ids": [r["run_id"] for r in runs]}), "run_id")
    for r in runs:
        # llm_calls is the source of truth; the runtime's own tally covers runs without call logging
        r["cost"] = costs.get(r["run_id"], {}).get("cost") or r.pop("runtime_cost", None) or 0.0
    return JSON({"migrated": True, "knowledge": knowledge, "eval_runs": db.query(db.EVAL_RUNS), "agent_runs": runs})


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
    unpriced = {"calls": 0, "models": set()}
    today = dt.date.today()
    for u in db.query(db.USAGE_BY_DAY):
        c = price(u["model"], u["long_prompt"], u["tin"], u["tout"])
        if c is None:  # never count unknown prices as free: report them
            unpriced["calls"] += u["calls"]
            unpriced["models"].add(u["model"] or "unknown")
            c = 0.0
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
        "unpriced": {"calls": unpriced["calls"], "models": sorted(unpriced["models"])},
    })


def api_call(request: Request):
    call = db.one(db.CALL, {"id": int(request.path_params["call_id"])})
    if not call:
        raise HTTPException(404, "Call not found")
    call["cost"] = call_cost(call["model"], call["input_tokens"], call["output_tokens"])
    return JSON(call)


DOC_INTERNAL = {"index.json", "docs.css"}
DOC_KINDS = {".pdf": "PDF", ".docx": "Word", ".doc": "Word", ".md": "Markdown", ".txt": "Text",
             ".pptx": "PowerPoint", ".xlsx": "Excel", ".csv": "CSV", ".html": "Web page",
             ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".svg": "Image"}


def api_docs(request: Request):
    """The day documents from index.json, plus every other file in docs/ as a download."""
    index_file = DOCS / "index.json"
    days = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else []
    listed = {Path(d.get(k, "")).name for d in days for k in ("html", "pdf")}
    files = []
    if DOCS.exists():
        for p in sorted(DOCS.iterdir(), key=lambda p: p.name.lower()):
            if (p.is_file() and not p.name.startswith(".") and p.name not in DOC_INTERNAL
                    and p.name not in listed and p.suffix.lower() in DOC_KINDS):
                st = p.stat()
                files.append({"name": p.name, "title": p.stem.replace("-", " ").replace("_", " "),
                              "kind": DOC_KINDS[p.suffix.lower()], "bytes": st.st_size,
                              "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc),
                              "url": f"/docs/{p.name}"})
    return JSON({"days": days, "files": files})


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
    Route("/api/docs", api_docs),
    Route("/api/quality", api_quality),
    Route("/api/work", lambda r: JSON(work_api.api_work(r))),
    Route("/api/work/goals/{goal_id}", lambda r: JSON(work_api.api_work_goal(r))),
    Route("/api/work/tasks/{task_id}", lambda r: JSON(work_api.api_work_task(r))),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
    Mount("/docs", StaticFiles(directory=DOCS, check_dir=False), name="docs"),
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
