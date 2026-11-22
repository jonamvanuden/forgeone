"""Context policies: named settings for how much context calls get, to compare like models.

    lean     half the budgets, no memory, agent keeps 1 recent result in full
    default  the standard budgets, all earlier feedback and lessons, agent keeps 3 results
    rich     1.5x budgets, all memory, agent keeps 6 results in full

A goal has one (`forgeone goal new --context lean`), an eval variant can set one
(`context: lean` or a dict with overrides). The policy name is stored in every manifest, so
the knowledge views can compare pass rate and cost per policy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .budget import budget_for
from .history import HistoryPolicy


@dataclass(frozen=True)
class ContextPolicy:
    name: str = "default"
    budget_scale: float = 1.0          # multiplies every token budget
    attempt_history: bool = True       # feedback of every earlier attempt, not just the last
    lessons: bool = True               # lessons from earlier goals (memory)
    max_lessons: int = 3
    keep_full: int = 3                 # agent: recent tool results kept in full
    shorten_above: int = 1200          # agent: older results longer than this become one line
    cache: bool = True                 # agent: prompt caching on Claude models

    def history(self, model: str) -> HistoryPolicy:
        return HistoryPolicy(keep_full=self.keep_full, shorten_above=self.shorten_above, cache=self.cache,
                             max_tokens=budget_for("agent", model, self.budget_scale))

    def as_dict(self) -> dict:
        return asdict(self)


PRESETS = {
    "lean": ContextPolicy("lean", budget_scale=0.5, attempt_history=False, lessons=False, keep_full=1, shorten_above=600),
    "default": ContextPolicy("default"),
    "rich": ContextPolicy("rich", budget_scale=1.5, max_lessons=5, keep_full=6, shorten_above=3000),
}


def resolve(spec: str | dict | ContextPolicy | None) -> ContextPolicy:
    """'lean' | {'base': 'lean', 'keep_full': 2, 'name': 'lean-2'} | None (default)."""
    if spec is None or spec == "":
        return PRESETS["default"]
    if isinstance(spec, ContextPolicy):
        return spec
    if isinstance(spec, str):
        if spec not in PRESETS:
            raise ValueError(f"unknown context policy {spec!r} (known: {', '.join(PRESETS)})")
        return PRESETS[spec]
    spec = dict(spec)
    base = resolve(spec.pop("base", spec.pop("preset", "default")))
    name = spec.pop("name", None) or (base.name if not spec else f"{base.name}+custom")
    unknown = set(spec) - set(ContextPolicy.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown context settings: {', '.join(sorted(unknown))}")
    return replace(base, name=name, **spec)
