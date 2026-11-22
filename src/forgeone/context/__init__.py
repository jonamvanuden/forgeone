"""Context management: what each model call sees, and a record of it (the context manifest).

- pack.py:    assemble a prompt's context by named sections, with cuts and a manifest
- history.py: keep the agent's conversation short (older tool results become one line)
              and mark the stable prefix for prompt caching
"""
from .history import HistoryPolicy, compact_history, with_cache
from .pack import ContextPack, estimate_tokens

__all__ = ["ContextPack", "HistoryPolicy", "compact_history", "estimate_tokens", "with_cache"]
