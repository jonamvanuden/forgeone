"""Promotion gate: may a challenger version replace the champion?

Rules (in order; the first that fails decides):
  1. Hard gates: the challenger has zero hard-check failures. Never averaged away.
  2. No category regression beyond `max_category_drop`.
  3. Overall improvement of at least `min_gain` on pass_all_repeats (or equal quality, lower cost).
Comparisons are only valid on the same suite version and the same repeats.
"""
from __future__ import annotations

from pydantic import BaseModel

from .scoring import Scorecard


class GateDecision(BaseModel):
    promote: bool
    reason: str
    regressions: dict[str, float] = {}


def decide(challenger: Scorecard, champion: Scorecard | None,
           max_category_drop: float = 0.10, min_gain: float = 0.0,
           cost_tolerance: float = 0.0) -> GateDecision:
    if challenger.hard_failures:
        return GateDecision(promote=False, reason=f"hard gate failed: {challenger.hard_failures[:3]}")
    if champion is None:
        return GateDecision(promote=True, reason="no champion yet; first version that passes all hard gates")

    regressions = {
        cat: round(champion.by_category[cat] - score, 3)
        for cat, score in challenger.by_category.items()
        if cat in champion.by_category and champion.by_category[cat] - score > max_category_drop
    }
    if regressions:
        return GateDecision(promote=False, reason="category regression", regressions=regressions)

    gain = challenger.pass_all_repeats - champion.pass_all_repeats
    if gain > min_gain:
        return GateDecision(promote=True, reason=f"pass^k improved by {gain:+.1%}")
    cheaper = (challenger.mean_cost_usd is not None and champion.mean_cost_usd is not None
               and challenger.mean_cost_usd < champion.mean_cost_usd * (1 - cost_tolerance))
    if gain >= 0 and cheaper:
        return GateDecision(promote=True, reason="same quality, lower cost")
    return GateDecision(promote=False, reason=f"no improvement (pass^k {gain:+.1%})")
