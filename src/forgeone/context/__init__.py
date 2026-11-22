"""Context management: what each model call sees, and a record of it (the context manifest).

- pack.py:    assemble a prompt's context by named sections, with cuts and a manifest
- history.py: keep the agent's conversation short (older tool results become one line)
              and mark the stable prefix for prompt caching
- budget.py:  token budgets per kind of call, capped by the model's context window
- policy.py:  named context policies (lean, default, rich) for goals and eval variants
"""
from .budget import budget_for
from .history import HistoryPolicy, compact_history, with_cache
from .pack import ContextPack, estimate_tokens
from .policy import PRESETS, ContextPolicy, resolve

__all__ = ["PRESETS", "ContextPack", "ContextPolicy", "HistoryPolicy", "budget_for", "compact_history",
           "estimate_tokens", "resolve", "with_cache"]
