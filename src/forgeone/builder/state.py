"""The builder's shared state. Nodes return only the keys they change."""
from typing import TypedDict


class ForgeState(TypedDict, total=False):
    goal: str
    plan: str
    spec: dict
    # reserved for the feedback loop (Day 2+): test results, verdicts, iteration counter
    iteration: int
