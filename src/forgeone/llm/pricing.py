"""USD list prices per million tokens: (input, output). Keep in sync with the Claude pricing page.

Haiku 5.5 is billed at a higher rate when one prompt exceeds 100k input tokens.
Estimates ignore caching and batch discounts, so real bills can be slightly lower.
"""
from __future__ import annotations

import re

LONG_PROMPT_TOKENS = 100_000

PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-5-5:long": (0.50, 2.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


_DATE_SUFFIX = re.compile(r"-(\d{8}|latest)$")


def resolve_model(model: str | None) -> str | None:
    """Map what the API reports to a PRICES key: 'anthropic:claude-sonnet-5-5-20260928'
    -> 'claude-sonnet-5-5'. Returns None if no price is known."""
    if not model:
        return None
    m = model.strip().lower().split(":", 1)[-1] if model.lower().startswith(("anthropic:", "claude:")) else model.strip().lower()
    m = m.split("/")[-1]          # e.g. 'anthropic/claude-...' from gateways
    if m in PRICES:
        return m
    m = _DATE_SUFFIX.sub("", m)
    return m if m in PRICES else None


def price(model: str | None, long_prompt: bool, tokens_in, tokens_out) -> float | None:
    """Estimated USD cost for a group of calls; None if the model's price is unknown
    (callers must surface that, never treat it as free)."""
    base = resolve_model(model)
    if base is None:
        return None
    key = f"{base}:long" if long_prompt and f"{base}:long" in PRICES else base
    pin, pout = PRICES[key]
    return (float(tokens_in or 0) * pin + float(tokens_out or 0) * pout) / 1_000_000


def call_cost(model: str | None, tokens_in, tokens_out) -> float | None:
    """Cost of a single call (decides the long-prompt rate from its own input size)."""
    return price(model, (tokens_in or 0) > LONG_PROMPT_TOKENS, tokens_in, tokens_out)
