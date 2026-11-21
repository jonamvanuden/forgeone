"""Suite runner (Day 2): run every case `repeats` times against one agent version.

Planned flow, per case and repeat:
    runtime.run(spec, case.task) -> events -> AgentTrace
    checks.run_checks(trace, case) + judges.judge(trace, case) -> CaseResult
    results -> scoring.scorecard -> gate.decide(challenger, champion)
Everything is logged with forgeone.observability and stored in eval_runs / eval_results.
"""
