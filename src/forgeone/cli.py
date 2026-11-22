"""The `forgeone` command.

    forgeone build [--agent voyagerone] [--publish branch|main|none] [--goal path.md] [--budget USD] [--legacy]
    forgeone agent list | status <name> | init <name> --path DIR [--remote URL]
    forgeone db migrate | status | backfill
    forgeone run "task" [--agent voyagerone] [--variant NAME] [--spec FILE]
    forgeone label RUN_ID good|bad [--note TEXT]
    forgeone eval run [--agent voyagerone] [--variant NAME ...] [--case ID ...] [--repeats N]
    forgeone eval report [--agent voyagerone]
    forgeone eval validate [--agent voyagerone]
    forgeone eval checks
    forgeone goal new "title" [--description TEXT | --file FILE] [--agent NAME] [--criteria TEXT ...]
                              [--constraint TEXT ...] [--budget USD] [--autonomy supervised|autonomous] [--run]
    forgeone goal list | show ID | run ID [--max-tasks N] | replan ID --reason TEXT | budget ID USD | cancel ID
    forgeone approve ID [--note TEXT]        (a task, a milestone, or a goal with one approval waiting)
    forgeone reject ID --note TEXT
    forgeone support [--days 7|30|90|all] [--builds]
    forgeone models list | check NAME
    forgeone control-room [--port 8501]
"""
from __future__ import annotations

import argparse
import sys

from .config import settings


def _build(a):
    if a.legacy:
        from .builder import run_build_legacy

        run_build_legacy(a.agent, a.goal, publish=a.publish)
        return
    from .builder import run_build

    res = run_build(a.agent, a.goal, publish=a.publish, budget_usd=a.budget)
    _print_tree(res["tree"])
    if res["published"]:
        print(res["published"])
    if res["spec_path"]:
        print(f"spec: {res['spec_path']}")
    if res["status"] != "done":
        raise SystemExit(f"forgeone: build {res['status']}: {res['tree'].goal.note or ''}")


def _agent(a):
    from .workspace import AgentRepo, load_registry, scaffold
    from .workspace import git as g

    if a.action in ("status", "init") and not a.name:
        raise SystemExit(f"usage: forgeone agent {a.action} <name>")

    if a.action == "list":
        reg = load_registry()
        if not reg:
            print("no agents registered in agents.yaml")
        for e in reg.values():
            state = "missing" if not e.path.exists() else ("repo" if g.is_repo(e.path) else "not a git repo")
            print(f"{e.name:<14} {e.path}  [{state}]  {e.remote or ''}")
    elif a.action == "status":
        r = AgentRepo.for_agent(a.name)
        print(f"{r.name}: {r.path}")
        if not g.is_repo(r.path):
            print("  not cloned yet")
            return
        print(f"  branch {g.current_branch(r.path)}, remote {g.remote_url(r.path) or 'none'}")
        print(f"  goal.md: {'ok' if r.goal_path.exists() else 'MISSING'}, "
              f"evals/suite.yaml: {'ok' if r.suite_path.exists() else 'MISSING'}, "
              f"agent/spec.yaml: {'ok' if r.spec_path.exists() else 'not built yet'}")
        d = g.dirty(r.path)
        print(f"  working tree: {'clean' if not d else f'{len(d)} uncommitted changes'}")
    elif a.action == "init":
        if not a.path:
            raise SystemExit("--path is required")
        p = scaffold(a.name, a.path, description=a.description or "", remote=a.remote)
        print(f"created {p}. Add it to agents.yaml:\n  {a.name}:\n    path: {a.path}\n"
              f"    remote: {a.remote or '<git remote url>'}\n    branch: main")


def _db(a):
    if a.action == "migrate":
        from .store.migrate import migrate

        migrate()
    elif a.action == "backfill":
        from .store.backfill import backfill

        backfill()
    else:
        import psycopg

        with psycopg.connect(settings().database_url) as conn:
            for t in ("runs", "events", "llm_calls", "decisions", "artifacts"):
                print(f"{t:<10} {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
            print("\nlatest runs:")
            for r in conn.execute(
                """SELECT run_id, status, duration_ms, llm_calls, total_input_tokens, total_output_tokens
                   FROM runs ORDER BY started_at DESC LIMIT 5"""
            ):
                print("  ", r)


def _run(a):
    from .evals.runner import run_task
    from .evals.variants import Variant, load_variants
    from .workspace import AgentRepo

    variant = None
    if a.variant:
        known = {v.name: v for v in load_variants(AgentRepo.for_agent(a.agent).path / "evals" / "variants.yaml")}
        if a.variant not in known:
            raise KeyError(f"unknown variant {a.variant}; defined: {sorted(known)}")
        variant = known[a.variant]
    trace, result = run_task(a.agent, a.task, variant=variant, spec_path=a.spec)
    att, ok = trace.recoveries
    print(f"\n{trace.final_answer.strip()}\n")
    print(f"run {trace.run_id}: {trace.stop_reason}, {len(trace.tool_calls)} tool calls "
          f"({trace.tool_errors} errors, {ok}/{att} recoveries), {trace.llm_calls} model calls, ${trace.cost_usd:.4f}")
    print("verdict:", "GOOD" if result.passed else "BAD", "".join(
        f"\n  {'ok ' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in result.checks))
    print(f"label it yourself: forgeone label {trace.run_id} good|bad --note '...'")


def _label(a):
    from .store import evals as store

    store.add_label(a.run_id, a.label, source="human", note=a.note)
    print(f"labelled {a.run_id}: {a.label}")


def _report(agent):
    from .store import evals as store

    rows = store.knowledge(agent)
    if not rows:
        print("no eval results yet: run `forgeone eval run` first")
        return
    variants = sorted({r["variant_name"] for r in rows})
    cats = sorted({r["category"] for r in rows})
    cell = {(r["category"], r["variant_name"]): r for r in rows}
    w = 22
    print(f"knowledge for {rows[0]['agent']} (suite {rows[0]['suite_version']}): pass^k / cost per run")
    print(f"{'category':<14}" + "".join(f"{v:<{w}}" for v in variants) + "best")
    for c in cats:
        line, best = f"{c:<14}", None
        for v in variants:
            r = cell.get((c, v))
            if not r:
                line += f"{'-':<{w}}"
                continue
            txt = f"{float(r['pass_k']):.0%} / ${float(r['mean_cost_usd'] or 0):.4f}"
            if r["hard_failures"]:
                txt += " !"
            line += f"{txt:<{w}}"
            key = (float(r["pass_k"]), -float(r["mean_cost_usd"] or 0))
            if not r["hard_failures"] and (best is None or key > best[0]):
                best = (key, v)
        print(line + (best[1] if best else "-"))
    print("\n! = hard-gate failures in that cell. best = highest pass^k, then lowest cost, without hard failures.")


def _eval(a):
    if a.action == "run":
        from .evals.runner import run_suite

        run_suite(a.agent, variant_names=a.variant or None, repeats=a.repeats, case_ids=a.case or None,
                  spec_path=a.spec)
        return
    if a.action == "report":
        _report(a.agent)
        return
    if a.action == "checks":
        from .evals.checks import CHECKS

        for name, (fn, hard) in sorted(CHECKS.items()):
            doc = ((fn.__doc__ or "").strip().splitlines() or [""])[0]
            print(f"{'HARD ' if hard else '     '}{name:<26} {doc}")
        return
    from .evals import load_suite

    s = load_suite(a.agent)
    print(f"suite OK: {s.agent}, {len(s.cases)} cases, {s.repeats} repeats, version {s.version}")
    for cat, cases in s.by_category().items():
        print(f"  {cat:<12} {len(cases)}")


def _control_room(a):
    import uvicorn

    uvicorn.run("forgeone.control_room.server:app", host=a.host, port=a.port, proxy_headers=True)


# ------------------------------------------------------------------ goals
LETTER = {"execute": "E", "verify": "V", "edge_test": "T", "approve": "A"}
MARK = {"passed": "✓", "failed": "✗", "pending": "?", "running": "…", "none": "·"}


def _work():
    from .work import Engine, PgStore

    store = PgStore()
    try:
        store.goals()
    except Exception as e:  # noqa: BLE001
        if "goals" in str(e) and "exist" in str(e):
            raise SystemExit("forgeone: the goals tables are missing. Run: forgeone db migrate")
        raise
    return store, Engine(store=store)


def _usd(v) -> str:
    return "–" if v is None else f"${v:.4f}" if v < 1 else f"${v:.2f}"


def _print_tree(tree, actions: bool = False) -> None:
    from .work.views import progress, stage_states, waiting_on_you

    g = tree.goal
    p = progress(tree)
    budget = f" of {_usd(g.budget_usd)}" if g.budget_usd is not None else ""
    print(f"\n{g.id}  {g.title}\n  {g.status.upper()}  plan v{g.plan_version}  {p['done']}/{p['tasks']} tasks done  "
          f"spent {_usd(g.cost_usd)}{budget}  {g.autonomy}  context {g.context_policy}{'  agent ' + g.agent if g.agent else ''}")
    if g.note:
        print(f"  note: {g.note}")
    for m in tree.active_milestones():
        mp = progress(tree, m.id)
        print(f"\n  M{m.seq}  {m.title}  [{m.status}]  {mp['done']}/{mp['tasks']} tasks  {_usd(m.cost_usd)}  {m.id}")
        for t in tree.tasks_of(m.id):
            pad = "  " * (t.depth - 1)
            stages = " ".join(f"{LETTER[s['stage']]}{MARK[s['state']]}" for s in stage_states(tree, t))
            diff = f"d{t.difficulty}" + (f"→{t.actual_difficulty:g}" if t.actual_difficulty else "")
            print(f"    {pad}{t.code:<7}{t.title[:44]:<45}{t.type:<9}{diff:<8}{t.risk:<7}{stages:<16}"
                  f"{t.status:<9}{_usd(t.cost_usd) if t.cost_usd else '':<9}{t.id}")
            if t.status in ("failed", "blocked") and t.failure_detail:
                print(f"    {pad}       ↳ {t.failure_reason}: {t.failure_detail[:110]}")
            if actions:
                for a in tree.actions_of(t.id):
                    print(f"    {pad}       {a.attempt}. {a.kind:<10}{a.status:<8}{a.actor[:28]:<29}{_usd(a.cost_usd):<9}"
                          f"{(a.reason or '')[:70]}")
    wait = waiting_on_you(tree)
    if wait:
        print("\n  Waiting for you:")
        for w in wait:
            print(f"    {w['code']} {w['title']}:  forgeone approve {w['id']}   or   forgeone reject {w['id']} --note \"...\"")
    print()


def _goal(a):
    from .work import Goal

    store, engine = _work()
    if a.action == "new":
        if not a.title:
            raise SystemExit("forgeone: give the goal a title: forgeone goal new \"...\"")
        desc = a.description or ""
        if a.file:
            from pathlib import Path

            desc = Path(a.file).read_text(encoding="utf-8")
        goal = Goal(title=a.title, description=desc, agent=a.agent, agent_type=a.type,
                    success_criteria=a.criteria or [], constraints=a.constraint or [], budget_usd=a.budget,
                    autonomy=a.autonomy, context_policy=a.context)
        print(f"planning {goal.id} ...")
        tree = engine.create(goal)
        _print_tree(tree)
        if a.run:
            _print_tree(engine.run(goal.id, max_tasks=a.max_tasks))
        else:
            print(f"Start it with: forgeone goal run {goal.id}")
        return
    if a.action == "list":
        goals = store.goals()
        if not goals:
            print("no goals yet: forgeone goal new \"...\"")
        for g in goals:
            from .work.views import progress

            p = progress(store.load(g.id))
            print(f"{g.id}  {g.status:<10}{p['done']:>3}/{p['tasks']:<4}{_usd(g.cost_usd):<10}{g.title[:70]}")
        return
    if not a.title:
        raise SystemExit(f"forgeone: which goal? forgeone goal {a.action} G-xxxxxx")
    from .work.approvals import normalize_id

    gid = normalize_id(a.title)
    if a.action == "show":
        _print_tree(store.load(gid), actions=a.actions)
    elif a.action == "run":
        _print_tree(engine.run(gid, max_tasks=a.max_tasks))
    elif a.action == "replan":
        if not a.reason:
            raise SystemExit("forgeone: say why: --reason \"...\"")
        _print_tree(engine.plan(gid, reason=a.reason))
    elif a.action in ("budget", "cancel"):
        tree = store.load(gid)
        g = tree.goal
        if a.action == "budget":
            if a.usd is None:
                raise SystemExit("forgeone: forgeone goal budget G-xxxxxx 2.50")
            g.budget_usd = a.usd
            if g.status == "paused":
                g.note = f"budget raised to ${a.usd:.2f}: continue with forgeone goal run {g.id}"
        else:
            g.status, g.note = "cancelled", "cancelled by you"
        store.save(g)
        print(f"{g.id}: {g.status}, budget {_usd(g.budget_usd)}")


def _support(a):
    from .work.support import analyze

    store, _ = _work()
    days = None if a.days == "all" else int(a.days)
    r = analyze([store.load(g.id) for g in store.goals()], days, "builds" if a.builds else "all")
    k = r["kpis"]
    pct = lambda v: "–" if v is None else f"{round(v * 100)}%"  # noqa: E731
    print(f"\nSupport, {'all time' if days is None else f'last {days} days'}, {'builds' if a.builds else 'all goals'}"
          f" ({r['goals']} goals)")
    print(f"  failed tasks {k['failed']} (+{k['replaced']} replaced)   retried {pct(k['retry_rate'])} of {k['closed']}"
          f"   harder than planned {k['harder']}   spent on failed attempts {_usd(k['wasted_usd'])} of {_usd(k['spent_usd'])}"
          f"   might not need AI {k['without_ai']}")
    if r["failed"]:
        print("\n  Failed and blocked")
        for x in r["failed"][:15]:
            print(f"    {x['code']:<7}{x['title'][:40]:<41}{(x['failure_reason'] or ''):<20}{x['id']}  {x['goal'][:30]}")
            if x["detail"]:
                print(f"           {x['detail'][:110]}")
    if r["what_goes_wrong"]:
        print("\n  What goes wrong")
        for x in r["what_goes_wrong"]:
            print(f"    {x['n']:>4}  {x['reason']:<20}{x['kind']:<16}{x['means']}")
    if r["calibration"]:
        print("\n  Planner: predicted -> actual difficulty")
        for c in r["calibration"]:
            print(f"    {c['type']:<10}{c['tasks']:>4} tasks  {c['predicted']} -> {c['actual']}  first try {pct(c['first_try'])}  {c['verdict']}")
    if r["hard"]:
        print("\n  Harder than planned")
        for x in r["hard"][:10]:
            print(f"    {x['code']:<7}{x['title'][:40]:<41}d{x['difficulty']}->{x['actual']}  tries {x['attempts']}  {(x['why'] or '')[:60]}")
    w = r["without_ai"]
    if w["flagged"] or w["repeated"] or w["idle_checks"]:
        print("\n  Might run without AI")
        for x in w["flagged"][:10]:
            print(f"    planner: {x['title'][:50]:<51}{x['runs']}x  {x['hint'][:60]}")
        for x in w["repeated"][:10]:
            print(f"    repeats: {x['title'][:50]:<51}{x['runs']} runs, always first try")
        for c in w["idle_checks"]:
            what = "AI approval of milestones" if c["type"] == "milestone" else f"{c['stage']} on {c['type']} tasks"
            print(f"    check:   {what}: {c['runs']} runs, caught nothing, {_usd(c['cost_usd'])}")
    if r["missed"]:
        print("\n  Missed by the AI checks (you rejected what they passed)")
        for x in r["missed"][:10]:
            print(f"    {x['code']:<7}{x['title'][:40]:<41}\"{(x['note'] or '')[:60]}\"")
    print()


def _models(a):
    import os
    import time

    from pydantic import BaseModel

    from .llm import chat_model
    from .llm.pricing import call_cost, usage_of
    from .llm.registry import model_info, models_file, registry

    if a.action == "list":
        print(f"models (built in + {models_file()}):")
        for name, m in registry().items():
            key = "" if m.provider == "anthropic" else ("  key set" if os.getenv(m.key_env) else f"  {m.key_env} NOT SET")
            price = (f"${m.price_in}/${m.price_out} per M" if m.price_in is not None else "list price")
            print(f"  {name:<22}{m.provider:<18}{m.provider_model:<26}{m.context_window:>8} ctx  {price}{key}")
        return
    if not a.name:
        raise SystemExit("forgeone: forgeone models check MODEL")
    info = model_info(a.name)
    llm = chat_model(a.name)
    print(f"checking {a.name} ({info.provider}, {info.provider_model}); a cold serverless worker can take minutes")

    def timed(label, fn):
        t0 = time.perf_counter()
        try:
            ok, detail, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail, msg = False, f"{type(e).__name__}: {e}", None
        tin, tout, _, _ = usage_of(msg) if msg is not None else (0, 0, 0, 0)
        cost = call_cost(a.name, tin, tout)
        print(f"  {'OK  ' if ok else 'FAIL'} {label:<18}{time.perf_counter() - t0:6.1f} s  {tin}+{tout} tokens"
              f"  {'' if cost is None else f'${cost:.5f}'}  {detail[:110]}")
        return ok

    def plain():
        m = llm.invoke("Reply with exactly the word: ready")
        return "ready" in str(m.content).lower(), repr(str(m.content)[:60]), m

    def tools():
        bound = llm.bind_tools([{"name": "web_search", "description": "search the web",
                                 "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                                                  "required": ["query"]}}])
        m = bound.invoke("Use the web_search tool to search for: WHO free sugars guideline")
        calls = getattr(m, "tool_calls", None) or []
        return bool(calls), (f"called {calls[0]['name']}({calls[0]['args']})" if calls else
                             "no tool call: set ENABLE_AUTO_TOOL_CHOICE=true and TOOL_CALL_PARSER=hermes"), m

    class Answer(BaseModel):
        city: str
        country: str

    def structured():
        out = llm.with_structured_output(Answer, method="json_schema", include_raw=True).invoke(
            "What is the capital of France? Give city and country.")
        p = out["parsed"]
        return p is not None, (p.model_dump_json() if p else f"did not parse: {out['parsing_error']}"), out["raw"]

    results = [timed("plain answer", plain), timed("tool call", tools), timed("structured output", structured)]
    print("all good: usable for agents, planning and checks" if all(results) else
          "not all checks passed: see above")
    if not all(results):
        raise SystemExit(1)


def _decide(a):
    from .work import decide

    store, _ = _work()
    print(decide(store, a.id, approved=a.cmd == "approve", note=a.note))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="forgeone", description="ForgeOne: build, evaluate and improve agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build an agent from its goal")
    b.add_argument("--agent", default="voyagerone")
    b.add_argument("--goal", default=None, help="goal file (default: goal.md in the agent's repo)")
    b.add_argument("--publish", default="branch", choices=["branch", "main", "none"],
                   help="branch: push forgeone/<run-id> (default), main: commit to main, none: don't touch the repo")
    b.add_argument("--budget", type=float, default=None, help="stop the build when it has cost this much (USD)")
    b.add_argument("--legacy", action="store_true", help="the original two-node LangGraph build, without tasks and checks")
    b.set_defaults(fn=_build)

    ag = sub.add_parser("agent", help="agent repositories: list, status, init")
    ag.add_argument("action", choices=["list", "status", "init"])
    ag.add_argument("name", nargs="?")
    ag.add_argument("--path")
    ag.add_argument("--remote")
    ag.add_argument("--description")
    ag.set_defaults(fn=_agent)

    d = sub.add_parser("db", help="event store: migrate, status, backfill")
    d.add_argument("action", choices=["migrate", "status", "backfill"])
    d.set_defaults(fn=_db)

    r = sub.add_parser("run", help="run an agent on one real task (with an automatic verdict)")
    r.add_argument("task")
    r.add_argument("--agent", default="voyagerone")
    r.add_argument("--variant", default=None, help="a variant from the agent's evals/variants.yaml")
    r.add_argument("--spec", default=None, help="spec file to run instead of agent/spec.yaml on main")
    r.set_defaults(fn=_run)

    lb = sub.add_parser("label", help="label a run good or bad")
    lb.add_argument("run_id")
    lb.add_argument("label", choices=["good", "bad"])
    lb.add_argument("--note", default=None)
    lb.set_defaults(fn=_label)

    e = sub.add_parser("eval", help="eval suites: run, report, validate, checks")
    e.add_argument("action", choices=["run", "report", "validate", "checks"])
    e.add_argument("--agent", default="voyagerone")
    e.add_argument("--variant", action="append", help="variant name (repeatable); default: all")
    e.add_argument("--case", action="append", help="case id (repeatable); default: all")
    e.add_argument("--repeats", type=int, default=None)
    e.add_argument("--spec", default=None)
    e.set_defaults(fn=_eval)

    go = sub.add_parser("goal", help="goals: new, list, show, run, replan, budget, cancel")
    go.add_argument("action", choices=["new", "list", "show", "run", "replan", "budget", "cancel"])
    go.add_argument("title", nargs="?", help="the goal title (new) or goal id (other actions)")
    go.add_argument("usd", nargs="?", type=float, help="new budget in USD (budget)")
    go.add_argument("--description", "-d", default=None)
    go.add_argument("--file", default=None, help="read the description from a file (e.g. a goal.md)")
    go.add_argument("--agent", default=None, help="registered agent that does the web tasks, e.g. voyagerone")
    go.add_argument("--type", default="generic", choices=["builder", "discovery", "content", "generic"])
    go.add_argument("--criteria", action="append", help="success criterion for the whole goal (repeatable)")
    go.add_argument("--constraint", action="append", help="constraint (repeatable)")
    go.add_argument("--budget", type=float, default=None, help="total budget in USD")
    go.add_argument("--autonomy", default="supervised", choices=["supervised", "autonomous"],
                    help="supervised: you approve every milestone; autonomous: an AI approver does, and it may replan")
    go.add_argument("--context", default="default", choices=["lean", "default", "rich"],
                    help="how much context model calls get: budgets, memory, agent history (context/policy.py)")
    go.add_argument("--run", action="store_true", help="start working right after planning")
    go.add_argument("--max-tasks", type=int, default=None, help="stop after this many tasks")
    go.add_argument("--reason", default=None, help="why you replan")
    go.add_argument("--actions", action="store_true", help="show: list every action")
    go.set_defaults(fn=_goal)

    mo = sub.add_parser("models", help="models: list, check NAME (e.g. a RunPod Qwen3 endpoint)")
    mo.add_argument("action", choices=["list", "check"])
    mo.add_argument("name", nargs="?")
    mo.set_defaults(fn=_models)

    su = sub.add_parser("support", help="what failed, what was harder than planned, what might not need AI")
    su.add_argument("--days", default="30", choices=["7", "30", "90", "all"])
    su.add_argument("--builds", action="store_true", help="only builds")
    su.set_defaults(fn=_support)

    for name, hlp in (("approve", "approve a task or milestone that waits for you"),
                      ("reject", "reject it, with a note on what must change")):
        ap = sub.add_parser(name, help=hlp)
        ap.add_argument("id")
        ap.add_argument("--note", default=None, required=name == "reject")
        ap.set_defaults(fn=_decide)

    c = sub.add_parser("control-room", help="run the dashboard")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8501)
    c.set_defaults(fn=_control_room)

    a = p.parse_args(argv)
    from .workspace import WorkspaceError

    try:
        a.fn(a)
    except ValueError as e:                     # NotWaiting, missing notes, no plan
        raise SystemExit(f"forgeone: {e}")
    except (WorkspaceError, KeyError, FileExistsError, FileNotFoundError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        raise SystemExit(f"forgeone: {msg}")


if __name__ == "__main__":
    main(sys.argv[1:])
