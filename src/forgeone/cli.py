"""The `forgeone` command.

    forgeone build [--agent voyagerone] [--goal path.md]
    forgeone db migrate | status | backfill
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

    run_build(a.agent, a.goal)


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


def _eval(a):
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
    b.add_argument("--goal", default=None, help="goal file (default: agents/<agent>/goal.md)")
    b.set_defaults(fn=_build)

    d = sub.add_parser("db", help="event store: migrate, status, backfill")
    d.add_argument("action", choices=["migrate", "status", "backfill"])
    d.set_defaults(fn=_db)

    e = sub.add_parser("eval", help="eval suites and checks")
    e.add_argument("action", choices=["validate", "checks"])
    e.add_argument("--agent", default="voyagerone")
    e.set_defaults(fn=_eval)

    c = sub.add_parser("control-room", help="run the dashboard")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8501)
    c.set_defaults(fn=_control_room)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
