"""The `forgeone` command.

    forgeone build [--agent voyagerone] [--publish branch|main|none] [--goal path.md]
    forgeone agent list | status <name> | init <name> --path DIR [--remote URL]
    forgeone db migrate | status | backfill
    forgeone run "task" [--agent voyagerone] [--variant NAME] [--spec FILE]
    forgeone label RUN_ID good|bad [--note TEXT]
    forgeone eval run [--agent voyagerone] [--variant NAME ...] [--case ID ...] [--repeats N]
    forgeone eval report [--agent voyagerone]
    forgeone eval validate [--agent voyagerone]
    forgeone eval checks
    forgeone control-room [--port 8501]
"""
from __future__ import annotations

import argparse
import sys

from .config import settings


def _build(a):
    from .builder import run_build

    run_build(a.agent, a.goal, publish=a.publish)


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


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="forgeone", description="ForgeOne: build, evaluate and improve agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build an agent from its goal")
    b.add_argument("--agent", default="voyagerone")
    b.add_argument("--goal", default=None, help="goal file (default: goal.md in the agent's repo)")
    b.add_argument("--publish", default="branch", choices=["branch", "main", "none"],
                   help="branch: push forgeone/<run-id> (default), main: commit to main, none: don't touch the repo")
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

    c = sub.add_parser("control-room", help="run the dashboard")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8501)
    c.set_defaults(fn=_control_room)

    a = p.parse_args(argv)
    from .workspace import WorkspaceError

    try:
        a.fn(a)
    except (WorkspaceError, KeyError, FileExistsError, FileNotFoundError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        raise SystemExit(f"forgeone: {msg}")


if __name__ == "__main__":
    main(sys.argv[1:])
