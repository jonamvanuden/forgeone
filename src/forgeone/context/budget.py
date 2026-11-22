"""Token budgets per kind of call, capped by what the model can take.

A budget is what the input of one call may cost at most: a target, not the model's limit. It is
the smaller of the target for the purpose (scaled by the context policy) and 60% of the model's
context window (the rest stays free for the answer and for estimation error). Fixed instructions
count against it; ContextPack.fit trims the rest by priority.
"""
from __future__ import annotations

TARGETS: dict[str, int] = {           # tokens of input per call
    "plan_milestones": 12_000, "plan_tasks": 12_000, "split_task": 10_000,
    "execute": 24_000, "verify": 16_000, "edge_test": 12_000, "approve": 12_000, "approve_milestone": 12_000,
    "builder.design": 16_000, "builder.spec": 20_000,
    "agent": 40_000,                  # the agent's whole conversation, per model call
}
WINDOW_SHARE = 0.6


def context_window(model: str | None) -> int:
    if not model:
        return 200_000
    try:
        from ..llm.registry import model_info

        return model_info(model).context_window
    except Exception:  # noqa: BLE001  (unknown model: assume a small window rather than fail)
        return 32_768


def budget_for(purpose: str, model: str | None, scale: float = 1.0) -> int:
    target = int(TARGETS.get(purpose, 16_000) * scale)
    return max(1_000, min(target, int(context_window(model) * WINDOW_SHARE)))
