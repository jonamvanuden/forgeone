"""Run an agent: one ad-hoc task (`forgeone run`) or a whole eval suite per variant (`forgeone eval run`).

Every agent run is a normal observability run (name "agent.run"), so its full trace shows up
in the Control Room. Eval runs add a verdict per run (checks), a scorecard per variant, and a
gate decision against the current champion. Results land in eval_runs / eval_results and the
v_knowledge view: which variant works for which kind of task, measured.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from ..config import settings
from ..observability import LLMCallLogger, log_artifact, start_run
from ..spec import AgentSpec
from ..store import evals as store
from ..workspace import AgentRepo
from .checks import run_checks
from .gate import decide
from .scoring import CaseResult, Scorecard, scorecard
from .suite import EvalCase, load_suite
from .trace import AgentTrace
from .variants import Variant, load_variants

# Checks that need no reference answer: applied to every real (ad-hoc) run as an automatic verdict.
GENERIC_CHECKS = ["cited_urls_were_opened", "quotes_found_on_pages", "has_sources",
                  "write_report_once", "within_step_budget", "no_run_error"]


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(yaml.safe_dump(spec, sort_keys=True).encode()).hexdigest()[:12]


def load_spec(agent: str, spec_path: str | Path | None = None) -> tuple[AgentSpec, str]:
    """The agent version under test: agent/spec.yaml on the agent repo's main, or an explicit file."""
    path = Path(spec_path) if spec_path else AgentRepo.for_agent(agent).spec_path
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Build the agent and merge the forgeone/<run-id> branch "
                                f"first, or pass --spec runs/<run-id>/spec.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AgentSpec.model_validate(raw), spec_hash(raw)


def default_browser():
    from ..tools.browser import PlaywrightBrowser

    return PlaywrightBrowser()


def _run_one(spec: AgentSpec, agent: str, task: str, variant: Variant, case_id: str, browser,
             llm_factory: Callable | None, run_fields: dict) -> AgentTrace:
    from ..runtime import RunConfig, run_agent

    policy = variant.context_policy
    cfg = RunConfig(model=variant.resolved_model, variant=variant.name, max_steps=variant.max_steps,
                    max_cost_usd=variant.max_cost_usd, tools=list(variant.tools) if variant.tools else None,
                    prompt_suffix=variant.prompt_suffix, history=policy.history(variant.resolved_model))
    with start_run("agent.run", agent=agent, task=task, case_id=case_id, variant=variant.name,
                   model=cfg.model, context_policy=policy.name, **run_fields) as run_id:
        trace = run_agent(spec, task, cfg, browser=browser, case_id=case_id, agent=agent,
                          reports_dir=settings().runs_dir / run_id,
                          llm=llm_factory(cfg.model) if llm_factory else None, callbacks=[LLMCallLogger()])
        trace.run_id = run_id
        if trace.report is not None:
            log_artifact("report", case_id, {"markdown": trace.report})
    return trace


def _checks_for(trace: AgentTrace, case: EvalCase) -> CaseResult:
    checks = run_checks(trace, case)
    return CaseResult(case_id=case.id, category=case.category, checks=checks,
                      cost_usd=trace.cost_usd, duration_ms=trace.duration_ms)


# ------------------------------------------------------------------ ad-hoc runs
def run_task(agent: str, task: str, variant: Variant | None = None, spec_path=None, browser=None,
             llm_factory=None) -> tuple[AgentTrace, CaseResult]:
    """`forgeone run`: one real task, plus an automatic verdict from the generic checks."""
    spec, version = load_spec(agent, spec_path)
    variant = variant or Variant(name="default")
    own_browser = browser is None
    browser = browser or default_browser()
    try:
        trace = _run_one(spec, agent, task, variant, "adhoc", browser, llm_factory,
                         {"agent_version": version, "mode": "adhoc"})
    finally:
        if own_browser:
            browser.close()
    case = EvalCase(id="adhoc", category="factual", task=task, checks=GENERIC_CHECKS)
    result = _checks_for(trace, case)
    if store.enabled():
        failed = [c.name for c in result.checks if not c.passed]
        store.add_label(trace.run_id, "good" if result.passed else "bad", source="checks",
                        note="all generic checks passed" if result.passed else f"failed: {', '.join(failed)}",
                        details={"checks": [c.model_dump() for c in result.checks]})
    return trace, result


# ------------------------------------------------------------------ eval suites
@dataclass
class VariantOutcome:
    eval_run_id: str
    variant: Variant
    scorecard: Scorecard | None
    gate: dict | None
    promoted: bool
    results: list[CaseResult] = field(default_factory=list)


def run_suite(agent: str, variant_names: list[str] | None = None, repeats: int | None = None,
              case_ids: list[str] | None = None, spec_path=None, browser_factory: Callable | None = None,
              llm_factory: Callable | None = None, progress: Callable[[str], None] = print) -> list[VariantOutcome]:
    repo = AgentRepo.for_agent(agent)
    spec, version = load_spec(agent, spec_path)
    suite = load_suite(path=repo.suite_path)
    variants = load_variants(repo.path / "evals" / "variants.yaml")
    if variant_names:
        known = {v.name: v for v in variants}
        missing = [n for n in variant_names if n not in known]
        if missing:
            raise KeyError(f"unknown variants {missing}; defined: {sorted(known)}")
        variants = [known[n] for n in variant_names]
    cases = [c for c in suite.cases if not case_ids or c.id in case_ids]
    if not cases:
        raise KeyError(f"no matching cases: {case_ids}")
    k = repeats or suite.repeats
    outcomes = []
    for variant in variants:
        eval_run_id = f"eval-{datetime.now():%Y%m%d-%H%M%S}-{variant.name}-{uuid.uuid4().hex[:4]}"
        store.create_eval_run(eval_run_id, agent, version, suite.version,
                              {**variant.model_dump(), "model": variant.resolved_model,
                               "fingerprint": variant.fingerprint},
                              k, len(cases), {"case_ids": [c.id for c in cases]})
        progress(f"\n== {eval_run_id}: {len(cases)} cases x {k} repeats, model {variant.resolved_model}")
        browser = (browser_factory or default_browser)()
        results: list[CaseResult] = []
        status = "error"
        try:
            for case in cases:
                for r in range(k):
                    trace = _run_one(spec, agent, case.task, variant, case.id, browser, llm_factory,
                                     {"eval_run_id": eval_run_id, "repeat": r, "agent_version": version,
                                      "suite_version": suite.version, "mode": "eval"})
                    res = _checks_for(trace, case)
                    res.repeat = r
                    results.append(res)
                    store.add_eval_result(eval_run_id, res, trace)
                    mark = "PASS" if res.passed else "FAIL " + ",".join(c.name for c in res.checks if not c.passed)
                    progress(f"  {case.id:<34} #{r + 1}  {mark}  ${trace.cost_usd or 0:.4f}  {trace.stop_reason}")
            status = "ok"
        finally:
            browser.close()
            sc = scorecard(results) if results else None
            gate, promoted = None, False
            if sc is not None and status == "ok":
                champ = store.champion(agent, suite.version)
                decision = decide(sc, Scorecard.model_validate(champ[1]) if champ else None)
                gate, promoted = decision.model_dump(), decision.promote
                if champ:
                    gate["champion"] = champ[0]
            store.finish_eval_run(eval_run_id, status, sc.model_dump() if sc else None, gate, promoted)
            _write_summary(eval_run_id, agent, variant, suite.version, version, sc, gate, results)
        outcomes.append(VariantOutcome(eval_run_id, variant, sc, gate, promoted, results))
        if sc:
            progress(f"  -> pass^{k} {sc.pass_all_repeats:.0%}, pass rate {sc.pass_rate:.0%}, "
                     f"cost/run ${sc.mean_cost_usd or 0:.4f}, hard failures {len(sc.hard_failures)}"
                     f"\n  -> gate: {'PROMOTED' if promoted else 'not promoted'} ({gate['reason'] if gate else '-'})")
    return outcomes


def _write_summary(eval_run_id, agent, variant, suite_version, agent_version, sc, gate, results) -> Path:
    out = settings().runs_dir / "evals" / f"{eval_run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "eval_run_id": eval_run_id, "agent": agent, "variant": variant.model_dump(),
        "suite_version": suite_version, "agent_version": agent_version,
        "scorecard": sc.model_dump() if sc else None, "gate": gate,
        "results": [r.model_dump() for r in results],
    }, indent=1, default=str))
    return out
