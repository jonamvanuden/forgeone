"""USD list prices per million tokens: (input, output). Keep in sync with the Claude pricing page.

Haiku 5.5 is billed at a higher rate when one prompt exceeds 100k input tokens.
Prompt caching is priced in: cache reads cost 10% of the input price, cache writes 125%.
Batch discounts are ignored, so real bills can be slightly lower.
"""
from __future__ import annotations

import re

LONG_PROMPT_TOKENS = 100_000
CACHE_READ_FACTOR = 0.10
CACHE_WRITE_FACTOR = 1.25

PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-5-5:long": (0.50, 2.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


_DATE_SUFFIX = re.compile(r"-(\d{8}|latest)$")


def _registered(model: str) -> tuple[float, float] | None:
    """Prices of models from models.yaml (e.g. Qwen3 on RunPod), if they declare any."""
    try:
        from .registry import registry

        info = registry().get(model)
    except Exception:  # noqa: BLE001  (a broken models.yaml must not break cost figures)
        return None
    if info and info.price_in is not None:
        return info.price_in, info.price_out or 0.0
    return None


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


def price(model: str | None, long_prompt: bool, tokens_in, tokens_out, cache_read=0, cache_write=0) -> float | None:
    """Estimated USD cost for a group of calls; None if the model's price is unknown
    (callers must surface that, never treat it as free). tokens_in includes cached tokens,
    as the API reports it; cache_read and cache_write say how many of them were cached."""
    base = resolve_model(model)
    if base is None:
        reg = _registered(str(model)) if model else None
        if reg is None:
            return None
        pin, pout = reg
    else:
        key = f"{base}:long" if long_prompt and f"{base}:long" in PRICES else base
        pin, pout = PRICES[key]
    cr, cw = float(cache_read or 0), float(cache_write or 0)
    plain = max(0.0, float(tokens_in or 0) - cr - cw)
    return (plain * pin + cr * pin * CACHE_READ_FACTOR + cw * pin * CACHE_WRITE_FACTOR
            + float(tokens_out or 0) * pout) / 1_000_000


def call_cost(model: str | None, tokens_in, tokens_out, cache_read=0, cache_write=0) -> float | None:
    """Cost of a single call (decides the long-prompt rate from its own input size)."""
    return price(model, (tokens_in or 0) > LONG_PROMPT_TOKENS, tokens_in, tokens_out, cache_read, cache_write)


def usage_of(message) -> tuple[int, int, int, int]:
    """(input, output, cache_read, cache_write) tokens from a LangChain message's usage_metadata."""
    u = getattr(message, "usage_metadata", None) or {}
    d = u.get("input_token_details") or {}
    return (int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
            int(d.get("cache_read") or 0), int(d.get("cache_creation") or 0))
