"""ContextPack: assemble what one model call sees, section by section, fit it into a token
budget, and record a manifest.

Every piece of context goes in through `add` (one text) or `join` (several sources, e.g. the
outputs of all dependencies), with a name, its source (a task id, an attempt), an optional
character limit and a priority. `fit(budget)` then makes it fit:

    1. parts with the lowest priority are shortened first (halved, down to a minimum),
    2. then left out, with a one-line note in the prompt saying what is missing,
    3. required parts are never left out; if still too big, the cuttable ones among them
       (the output under review) are shortened as the last resort.

The manifest records per section its tokens, whether it was cut or left out and how much was
lost, the budget, and what was excluded by rule (the executor's reasoning for a reviewer, the
eval suite for the builder), so it says not only what went in but what did not, and why.

Token counts are estimates (characters / 4, rounded up). The number billed is stored next to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4
MIN_PART_CHARS = 400            # a shortened part keeps at least this much
MIN_REQUIRED_CHARS = 2000       # a cuttable required part (the output under review) keeps at least this

# Priorities: lower is shortened and dropped first.
P_LESSONS, P_HISTORY, P_DEPS, P_EVIDENCE, P_REVIEWS, P_NORMAL = 1, 2, 3, 4, 5, 6


def estimate_tokens(text: str | None) -> int:
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} more characters cut]"


@dataclass
class _Part:
    heading: str | None
    source: str | None
    text: str
    limit: int | None
    omitted: str | None = None          # why it was left out (budget)

    def room(self) -> int:
        """Characters of text this part may show now (its limit, or all of it)."""
        return min(self.limit, len(self.text)) if self.limit else len(self.text)

    def render(self) -> str:
        if self.omitted:
            return ""
        return cut(self.text, self.limit) if self.limit else self.text


@dataclass
class _Item:
    key: str
    name: str
    parts: list[_Part]
    required: bool
    cuttable: bool
    priority: int
    sep: str = "\n\n"
    empty: str = ""
    prefix: str = ""                   # put before the value when it is not empty (a heading)


@dataclass
class ContextPack:
    purpose: str                               # e.g. "execute", "verify", "plan_tasks", "builder.spec"
    model: str | None = None
    policy: str | None = None                  # the context policy name (lean, default, rich)
    items: list[_Item] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    budget: int | None = None
    reserved: int = 0                          # tokens of the fixed instructions, counted against the budget

    # ---------------------------------------------------------------- building
    def add(self, key: str, text, *, name: str | None = None, source: str | None = None, limit: int | None = None,
            required: bool = False, cuttable: bool = False, priority: int = P_NORMAL, empty: str = "") -> str:
        self.items.append(_Item(key, name or key, [_Part(None, source, "" if text is None else str(text), limit)],
                                required, cuttable, priority, empty=empty))
        return self.values[key]

    def join(self, key: str, parts: list[tuple[str | None, str | None, str]], *, limit_each: int | None = None,
             sep: str = "\n\n", empty: str = "none", name: str | None = None, priority: int = P_DEPS,
             prefix: str = "") -> str:
        """Several sources in one prompt value: parts = [(heading, source id, text), ...]."""
        self.items.append(_Item(key, name or key, [_Part(h, s, t or "", limit_each) for h, s, t in parts],
                                False, False, priority, sep=sep, empty=empty, prefix=prefix))
        return self.values[key]

    def fixed(self, key: str, text: str) -> str:
        """A value that belongs to the instructions (lists, catalogs): required, never cut."""
        self.items.append(_Item(key, key, [_Part(None, None, text, None)], True, False, 99))
        return text

    def exclude(self, what: str, rule: str) -> None:
        self.excluded.append({"what": what, "rule": rule})

    # ---------------------------------------------------------------- rendering
    def _value(self, it: _Item) -> str:
        texts, dropped = [], []
        for p in it.parts:
            if p.omitted:
                dropped.append(p)
                continue
            body = p.render()
            texts.append(f"{p.heading}\n{body}" if p.heading else body)
        if dropped:
            what = ", ".join(p.heading.lstrip("# ").strip() if p.heading else (p.source or it.name) for p in dropped)
            texts.append(f"[{len(dropped)} {it.name} left out to fit the context budget: {what}]")
        out = it.sep.join(t for t in texts if t)
        return (it.prefix + out) if out else it.empty

    @property
    def values(self) -> dict[str, str]:
        return {it.key: self._value(it) for it in self.items}

    def tokens(self) -> int:
        return self.reserved + sum(estimate_tokens(v) for v in self.values.values())

    # ---------------------------------------------------------------- budget
    def fit(self, budget: int | None, reserved: int = 0) -> "ContextPack":
        """Shorten, then leave out, low-priority parts until the estimate fits `budget` tokens."""
        self.budget, self.reserved = budget, reserved
        if not budget or self.tokens() <= budget:
            return self
        soft = sorted((it for it in self.items if not it.required), key=lambda it: it.priority)
        # 1. shorten: halve the biggest parts of the lowest priority first, down to MIN_PART_CHARS
        for it in soft:
            while self.tokens() > budget:
                big = max((p for p in it.parts if not p.omitted and p.room() > MIN_PART_CHARS),
                          key=lambda p: p.room(), default=None)
                if big is None:
                    break
                big.limit = max(MIN_PART_CHARS, big.room() // 2)
            if self.tokens() <= budget:
                return self
        # 2. leave out, oldest/lowest priority first
        for it in soft:
            for p in it.parts:
                if self.tokens() <= budget:
                    return self
                if not p.omitted and p.text:
                    p.omitted = "over the context budget"
        # 3. last resort: shorten cuttable required parts (the output under review)
        for it in (i for i in self.items if i.required and i.cuttable):
            for p in it.parts:
                while self.tokens() > budget and p.room() > MIN_REQUIRED_CHARS:
                    p.limit = max(MIN_REQUIRED_CHARS, p.room() // 2)
        return self

    # ---------------------------------------------------------------- manifest
    def manifest(self, instructions: str = "", prompt_version: str | None = None) -> dict:
        ins = estimate_tokens(instructions)
        merged: dict[tuple, dict] = {}                 # parts with the same name and source: one section
        for it in self.items:
            for p in it.parts:
                out = p.render()
                status = ("omitted" if p.omitted else "cut" if out != p.text else "included" if p.text else "empty")
                key = (it.name, p.source)
                s = merged.setdefault(key, {"name": it.name, "source": p.source, "tokens": 0, "original_tokens": 0,
                                            "status": status, "required": it.required, "priority": it.priority})
                if s["tokens"] or s["original_tokens"]:
                    order = ["empty", "included", "cut", "omitted"]
                    s["status"] = max(s["status"], status, key=order.index)
                s["tokens"] += estimate_tokens(out)
                s["original_tokens"] += estimate_tokens(p.text)
        secs = ([{"name": "instructions", "source": prompt_version, "tokens": ins, "status": "included",
                  "original_tokens": ins, "required": True, "priority": 99}] if instructions else []) + list(merged.values())
        return {
            "purpose": self.purpose, "model": self.model, "policy": self.policy, "budget": self.budget,
            "tokens_est": sum(s["tokens"] for s in secs),
            "cut": sum(1 for s in secs if s["status"] == "cut"),
            "omitted": sum(1 for s in secs if s["status"] == "omitted"),
            "tokens_lost": sum(s["original_tokens"] - s["tokens"] for s in secs if s["status"] in ("cut", "omitted")),
            "sections": secs, "excluded": self.excluded,
        }
