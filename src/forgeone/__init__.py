"""ForgeOne: an agent that builds, evaluates and improves agents.

Package map (see ARCHITECTURE.md):
    builder/        goal -> AgentSpec (later: test, judge, repair loop)
    spec/           contracts: AgentSpec and the tool allowlist
    runtime/        AgentSpec -> running agent                       (Day 2)
    tools/          tool implementations                             (Day 2)
    evals/          suites, checks, judges, scoring, promotion gate  (the core)
    registry/       versions, champion/challenger                    (later)
    safety/         limits on what agents may do
    memory/         what persists between runs                       (later)
    llm/            model access and pricing
    observability/  structured events for every run
    store/          Postgres mirror, migrations, backfill
    control_room/   the dashboard
"""
__version__ = "0.2.0"
