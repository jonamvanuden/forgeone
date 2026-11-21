"""Evaluation: how ForgeOne tells a better agent from a worse one.

This is the product's core. The pieces, in the order a result flows through them:

    suite.py     the fixed eval suite (owned by you, never by the builder)
    trace.py     what one agent run did: tool calls, report, cost
    checks/      programmatic checks on a trace: exact, free, impossible to fool
    judges/      model judges with rubrics, for what code cannot check
    scoring.py   per-case results -> scorecard (per category, pass^k, cost)
    gate.py      champion vs challenger: hard gates + no regression -> promote or reject
    runner.py    runs a suite against an agent version (Day 2)

See docs/day-01 chapter 12 for the reasoning behind every rule here.
"""
from .gate import GateDecision, decide
from .scoring import CaseResult, CheckResult, Scorecard, scorecard
from .suite import EvalCase, EvalSuite, load_suite
from .trace import AgentTrace, ToolCall

__all__ = [
    "EvalCase", "EvalSuite", "load_suite", "AgentTrace", "ToolCall",
    "CheckResult", "CaseResult", "Scorecard", "scorecard", "GateDecision", "decide",
]
