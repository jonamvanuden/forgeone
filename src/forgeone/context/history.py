"""Keep an agent's conversation short.

Without this, every step re-sends every earlier tool result: a 12-step run sends the same
pages again and again (about 150k input tokens, mostly repeats). The policy keeps the most
recent tool results in full and replaces older long ones by one line that says what it was
and how to get it back. The full text stays in the AgentTrace (page_text), so the quote and
citation checks still see everything the agent read.

Prompt caching: the tool definitions and the system prompt are the same on every step, so they
are marked for Anthropic's prompt cache (reads cost 10% of the input price).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .pack import estimate_tokens

HINTS = {
    "open_page": "Open the page again if you need its exact text.",
    "click": "Open the page again if you need its exact text.",
    "web_search": "Search again if you need the results.",
    "find_on_page": "Run find_on_page again on that page if you need the passages.",
}


@dataclass
class HistoryPolicy:
    keep_full: int = 3              # the most recent tool results stay in full
    shorten_above: int = 1200       # older results longer than this (characters) become one line
    cache: bool = True              # mark tools + system prompt for prompt caching (Claude models)

    def as_dict(self) -> dict:
        return asdict(self)


def stub(tool: str, args: dict, chars: int) -> str:
    what = ", ".join(f"{k}={str(v)[:120]}" for k, v in args.items())
    return f"[Shortened to save tokens: the result of {tool}({what}) had {chars} characters. {HINTS.get(tool, '')}]".strip()


def compact_history(messages: list, results: list[dict], policy: HistoryPolicy) -> dict:
    """results: one entry per tool result in `messages`, oldest first:
    {"index", "tool", "args", "chars", "shortened"}. Changes `messages` in place."""
    from langchain_core.messages import ToolMessage

    shortened, saved = 0, 0
    for r in results[:-policy.keep_full] if policy.keep_full else results:
        if r["shortened"] or r["chars"] <= policy.shorten_above:
            continue
        old = messages[r["index"]]
        text = stub(r["tool"], r["args"], r["chars"])
        messages[r["index"]] = ToolMessage(text, tool_call_id=old.tool_call_id)
        r["shortened"], r["stub_chars"] = True, len(text)
        shortened += 1
        saved += r["chars"] - len(text)
    return {"shortened": shortened, "chars_saved": saved}


def messages_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        c = m.content
        total += estimate_tokens(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
        for tc in getattr(m, "tool_calls", None) or []:
            total += estimate_tokens(json.dumps(tc.get("args") or {}, ensure_ascii=False))
    return total


def with_cache(text: str, model: str, policy: HistoryPolicy):
    """System prompt content, marked as the end of the cacheable prefix (tools + system)."""
    if policy.cache and model.startswith("claude"):
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
    return text
