"""The work engine: plans a goal and works through it.

    plan:  goal -> milestones (1 model call) -> tasks per milestone (1 call each)
           -> tasks rated difficulty 5 are split into subtasks (1 call each)
    run:   milestone by milestone, every task whose dependencies are done goes through its
           stages: execute -> verify -> edge_test -> approve. A failed stage sends its feedback
           into the next attempt. A human approval stops the run until `forgeone approve`.

Every model call, check and approval is an Action row, with its cost, model, prompt version
and verdict. The engine never decides silently: each decision has a reason on the record.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from ..context import PRESETS, ContextPack, resolve
from ..memory import find_lessons
from ..context.budget import budget_for
from ..context.pack import P_DEPS, P_EVIDENCE, P_HISTORY, P_LESSONS, P_REVIEWS, estimate_tokens
from ..observability import log_event, start_run
from ..observability.context import iteration_var, node_var, run_id_var
from .brain import Answer, Brain, prompt_version, template
from .executors import CHECK_FAILURE, AgentExecutor, ExecResult, LLMExecutor
from .model import FAILURE_REASONS, Action, Goal, GoalTree, Milestone, Task, now
from .planner import bullets, milestones_from, numbered, task_types_text, tasks_from, workers_text
from .playbooks import StepContext, StepFailed
from .playbooks import load as load_playbook
from .policy import (MAX_DEPTH, MAX_REPLANS, SPLIT_AT_DIFFICULTY, actual_difficulty, approver_for, as_container,
                     cheap_model, estimate_cost, is_gate, milestone_approver, type_model)
from .schemas import Approval, EdgeReport, MilestonePlan, TaskPlan, Verdict
from .store import WorkStore, default_store

OUTPUT_LIMIT = 30000      # characters of output a reviewer sees
DEP_LIMIT = 4000          # characters of each dependency's output an executor sees
MILESTONE_RESULT_LIMIT = 1500   # characters of each task result a milestone approver sees


FIX = {"cited_urls_were_opened": "Cite only pages you opened in this attempt.",
       "quotes_found_on_pages": "Quote text exactly as it appears on the page."}


class StepExecutor:
    """Runs a playbook step: a registered Python function."""
    name = "step"

    def __init__(self, engine: "Engine", tree: GoalTree):
        self.engine, self.tree = engine, tree

    def run(self, task: Task, context: dict) -> ExecResult:
        fn = load_playbook(self.tree.goal.playbook).steps[task.step]
        ctx = StepContext(goal=self.tree.goal, tree=self.tree, task=task, brain=self.engine.brain,
                          deps=[d for d in (self.tree.task(i) for i in task.depends_on) if d], run_id=self.engine.run_id)
        t0 = time.perf_counter()
        try:
            res = fn(ctx)
        except Exception as e:  # noqa: BLE001
            known = isinstance(e, StepFailed)
            return ExecResult(False, "", task.model, None, duration_ms=round((time.perf_counter() - t0) * 1000),
                              error=str(e) if known else f"{type(e).__name__}: {e}",
                              failure_reason=e.failure_reason if known else "tool_error")
        if not res.duration_ms:
            res.duration_ms = round((time.perf_counter() - t0) * 1000)
        return res


class BudgetExhausted(Exception):
    pass


class TaskLimit(Exception):
    pass


def _money(v: float) -> str:
    return f"${v:.2f}" if v >= 0.1 else f"${v:.4f}"


def _cut(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} more characters cut]"


def finish_task(tree: GoalTree, t: Task, status: str) -> None:
    """Close a task: roll up cost, tokens and time from its actions, and score how hard it really was."""
    acts = tree.actions_of(t.id)
    t.status = status
    t.finished_at = now()
    t.cost_usd = round(sum(a.cost_usd for a in acts), 6)
    t.tokens_in = sum(a.tokens_in for a in acts)
    t.tokens_out = sum(a.tokens_out for a in acts)
    t.duration_ms = sum(a.duration_ms for a in acts if a.actor_type != "human")   # working time, not waiting time
    rejections = sum(1 for a in acts if a.kind in ("verify", "edge_test", "approve") and a.status == "failed")
    t.actual_difficulty = actual_difficulty(t, rejections)
    if status == "done":
        t.failure_reason = t.failure_detail = t.feedback = None


def sync_containers(tree: GoalTree, milestone_id: str | None = None) -> list[Task]:
    """A task split into subtasks takes its status, cost and output from them."""
    changed = []
    for c in [t for t in tree.tasks if not t.stages and t.status != "superseded"
              and (milestone_id is None or t.milestone_id == milestone_id)]:
        kids = sorted(tree.children(c.id), key=lambda k: k.seq)
        if not kids:
            continue
        st = [k.status for k in kids]
        new = ("done" if all(s in ("done", "skipped") for s in st) else
               "failed" if any(s in ("failed", "blocked") for s in st) else
               "waiting" if "waiting" in st else
               "running" if any(s in ("running", "done") for s in st) else "pending")
        before = (c.status, c.cost_usd)
        c.status = new
        c.attempts = max(k.attempts for k in kids)
        c.cost_usd = round(sum(k.cost_usd for k in kids), 6)
        c.tokens_in, c.tokens_out = sum(k.tokens_in for k in kids), sum(k.tokens_out for k in kids)
        c.duration_ms = sum(k.duration_ms for k in kids)
        c.started_at = min((k.started_at for k in kids if k.started_at), default=None)
        if new in ("done", "failed"):
            c.finished_at = c.finished_at or now()
            c.output = kids[-1].output if new == "done" else None
            failed = next((k for k in kids if k.status in ("failed", "blocked")), None)
            c.failure_reason = failed.failure_reason if failed else None
            c.failure_detail = f"subtask {failed.code} {failed.status}" if failed else None
            if new == "done":
                done = [k.actual_difficulty or k.difficulty for k in kids]
                c.actual_difficulty = round(min(5.0, max(done) + 0.5 * (len(kids) - 1)), 1)
            else:
                c.actual_difficulty = 5.0
        if (c.status, c.cost_usd) != before:
            changed.append(c)
    return changed


class Engine:
    def __init__(self, store: WorkStore | None = None, brain: Brain | None = None,
                 agent_factory=None, human: str = "you"):
        """agent_factory(goal) -> AgentExecutor; tests pass one with a fake browser and model."""
        self.store = store or default_store()
        self.brain = brain or Brain()
        self.agent_factory = agent_factory
        self.human = human
        self.run_id: str | None = None
        self._agent: AgentExecutor | None = None
        self.tasks_run = 0
        self.max_tasks: int | None = None
        self.policy = PRESETS["default"]           # set from the goal in plan() and run()

    # ================================================================ plumbing
    @contextmanager
    def _session(self, name: str, goal: Goal):
        if run_id_var.get():                               # already inside a run (tests, nested use)
            self.run_id = run_id_var.get()
            yield
            return
        fields = dict(goal_id=goal.id, title=goal.title, agent=goal.agent)
        if goal.playbook:                                  # e.g. builds keep their own run name and fields
            pb = load_playbook(goal.playbook)
            name = pb.session_name or name
            fields.update(pb.session_fields(goal) if pb.session_fields else {})
        with start_run(name, **fields) as rid:
            self.run_id = rid
            yield

    @contextmanager
    def _node(self, name: str, attempt: int | None = None):
        """Labels every event and model call inside, and records the step for the run timeline."""
        a, b = node_var.set(name), iteration_var.set(attempt)
        t0 = time.perf_counter()
        log_event("node.start", logging.DEBUG)
        try:
            yield
        except Exception as e:
            log_event("node.error", logging.ERROR, error=f"{type(e).__name__}: {e}",
                      duration_ms=round((time.perf_counter() - t0) * 1000))
            raise
        else:
            log_event("node.end", duration_ms=round((time.perf_counter() - t0) * 1000))
        finally:
            node_var.reset(a)
            iteration_var.reset(b)

    @staticmethod
    def _label(stage: str, t: Task) -> str:
        return f"{stage} {t.step.rsplit('.', 1)[-1] if t.step else t.code}"

    def agent(self, goal: Goal) -> AgentExecutor | None:
        if not goal.agent or goal.playbook:
            return None
        if self._agent is None:
            self._agent = self.agent_factory(goal) if self.agent_factory else AgentExecutor(goal.agent)
        return self._agent

    def close(self):
        if self._agent is not None:
            self._agent.close()

    def _act(self, tree: GoalTree, kind: str, *, task: Task | None = None, milestone: Milestone | None = None,
             attempt: int = 1, actor_type: str = "ai", actor: str = "", input: dict | None = None) -> Action:
        a = Action(goal_id=tree.goal.id, milestone_id=(task.milestone_id if task else milestone.id if milestone else None),
                   task_id=task.id if task else None, kind=kind, attempt=attempt, actor_type=actor_type,
                   actor=actor, input=input or {}, run_id=self.run_id)
        tree.actions.append(a)
        self.store.save(a)
        return a

    def _finish(self, tree: GoalTree, a: Action, status: str, answer: Answer | None = None, **fields) -> Action:
        a.status = status
        a.finished_at = now()
        if answer is not None:
            a.model, a.prompt_version = answer.model, answer.prompt_version
            a.cost_usd, a.tokens_in, a.tokens_out, a.duration_ms = (answer.cost_usd, answer.tokens_in,
                                                                     answer.tokens_out, answer.duration_ms)
        for k, v in fields.items():
            setattr(a, k, v)
        if not a.duration_ms and a.status != "pending":
            a.duration_ms = round((a.finished_at - a.started_at).total_seconds() * 1000)
        tree.goal.cost_usd = round(tree.goal.cost_usd + a.cost_usd, 6)
        extra = []
        t = tree.task(a.task_id) if a.task_id else None
        if t is not None and a.status != "pending":        # running totals; finish_task recomputes them exactly
            t.cost_usd = round(t.cost_usd + a.cost_usd, 6)
            t.tokens_in, t.tokens_out = t.tokens_in + a.tokens_in, t.tokens_out + a.tokens_out
            t.duration_ms += a.duration_ms
            extra.append(t)
        self.store.save(a, tree.goal, *extra)
        log_event(f"work.{a.kind}", task=a.task_id, milestone=a.milestone_id, attempt=a.attempt, status=a.status,
                  actor=a.actor, cost_usd=a.cost_usd, failure_reason=a.failure_reason)
        return a

    @staticmethod
    def _pack(purpose: str, model: str | None, **values) -> ContextPack:
        """A pack from plain values: one manifest section per value."""
        p = ContextPack(purpose, model)
        for k, v in values.items():
            p.add(k, v)
        return p

    def _fit(self, pack: ContextPack, prompt: str | None, model: str | None) -> None:
        """Apply the token budget of this kind of call (scaled by the goal's context policy)."""
        pack.policy = self.policy.name
        pack.fit(budget_for(pack.purpose, model, self.policy.budget_scale),
                 reserved=estimate_tokens(template(prompt)) if prompt else 0)

    def _ask(self, tree: GoalTree, a: Action, schema, prompt: str, model: str, pack: ContextPack) -> Answer | None:
        """A model call for an action, with its context manifest; a failed call closes the action as
        failed (model_error)."""
        self._fit(pack, prompt, model)
        a.context = pack.manifest(template(prompt), prompt_version(prompt))
        try:
            ans = self.brain.ask(schema, prompt, model, **pack.values)
            a.context["tokens_billed"] = ans.tokens_in
            return ans
        except Exception as e:  # noqa: BLE001
            self._finish(tree, a, "failed", reason=f"{type(e).__name__}: {e}", failure_reason="model_error",
                         model=model, feedback="The check itself failed; try again.")
            return None

    def _check_budget(self, tree: GoalTree) -> None:
        g = tree.goal
        if g.budget_usd is not None and g.cost_usd >= g.budget_usd:
            raise BudgetExhausted(f"budget of {_money(g.budget_usd)} used up ({_money(g.cost_usd)} spent). "
                                  f"Raise it with: forgeone goal budget {g.id} <usd>")

    # ================================================================ planning
    def create(self, goal: Goal) -> GoalTree:
        """Save a new goal and plan it. Checks the agent first, before any model call."""
        self.agent(goal)                                  # fails early if the agent has no merged spec
        self.store.save(goal)
        return self.plan(goal.id)

    def plan(self, goal_id: str, reason: str | None = None) -> GoalTree:
        tree = self.store.load(goal_id)
        self.policy = resolve(tree.goal.context_policy)
        with self._session("goal.plan", tree.goal):
            try:
                self._plan(tree, reason)
            finally:
                self.close()
        return tree

    def _plan(self, tree: GoalTree, reason: str | None) -> None:
        g = tree.goal
        agent = self.agent(g)
        tools = agent.tools if agent else None
        done = [m for m in tree.active_milestones() if m.status == "done"]
        for m in tree.active_milestones():
            if m.status != "done":
                m.status, m.note = "superseded", f"replaced by plan v{g.plan_version + 1}"
                self.store.save(m)
        for t in tree.tasks:
            ms = tree.milestone(t.milestone_id)
            if ms and ms.status == "superseded" and t.status != "superseded":
                t.status = "superseded"
                self.store.save(t)
        g.plan_version += 1
        if g.playbook:
            self._plan_playbook(tree, done)
            g.status, g.note = "planned", None
            self.store.save(g)
            return
        history = ""
        if g.plan_version > 1:
            history = (f"\n# This is a replan (plan version {g.plan_version})\nReason: {reason or 'requested'}\n"
                       f"Already finished, build on these and do not repeat them:\n"
                       f"{bullets([f'{m.title}: ' + '; '.join(m.done_when) for m in done])}\n")
        a = self._act(tree, "plan", actor=self.brain.planner_model,
                      input={"level": "goal", "plan_version": g.plan_version, "reason": reason,
                             "kept_milestones": [m.id for m in done]})
        with self._node("plan goal"):
            ans = self._ask(tree, a, MilestonePlan, "plan_milestones", self.brain.planner_model, self._pack(
                "plan_milestones", self.brain.planner_model,
                title=g.title, description=g.description or "(no further description)",
                success_criteria=bullets(g.success_criteria), constraints=bullets(g.constraints),
                workers=workers_text(g, tools), history=history))
        if ans is None:
            raise RuntimeError(f"planning failed: {a.reason}")
        start = max((m.seq for m in tree.milestones), default=0) + 1
        new = milestones_from(ans.value.milestones, g, g.plan_version, start)
        self._finish(tree, a, "passed", ans, reason=ans.value.reasoning,
                     output={"milestones": [f"M{m.seq} {m.title}" for m in new]})
        tree.milestones.extend(new)
        self.store.save(*new)
        plan_text = "\n".join(f"- M{m.seq} {m.title}" + ("  (done)" if m.status == "done" else "")
                              for m in done + new)
        for m in new:
            self._plan_milestone(tree, m, tools, plan_text.replace(f"- M{m.seq} ", f"- -> M{m.seq} "))
        g.status, g.note = "planned", None
        self.store.save(g)

    def _plan_playbook(self, tree: GoalTree, done: list[Milestone]) -> None:
        """A fixed plan from code: no planner call, the same records as a planned goal."""
        g = tree.goal
        pb = load_playbook(g.playbook)
        a = self._act(tree, "plan", actor_type="tool", actor=f"playbook:{pb.name}",
                      input={"level": "goal", "plan_version": g.plan_version, "playbook": pb.name})
        kept = {m.title for m in done}
        templates = [mt for mt in pb.plan(g) if mt.title not in kept]
        seq = max((m.seq for m in tree.milestones), default=0)
        ids: dict[str, str] = {t.step or t.code: t.id for t in tree.tasks if t.status == "done"}
        new_ms, new_tasks = [], []
        for mt in templates:
            seq += 1
            m = Milestone(goal_id=g.id, plan_version=g.plan_version, seq=seq, title=mt.title,
                          description=mt.description, done_when=mt.done_when, approval=mt.approval)
            new_ms.append(m)
            for j, tt in enumerate(mt.tasks, 1):
                model = tt.model or ((type_model(tt.type) or cheap_model()) if tt.step is None else None)
                t = Task(goal_id=g.id, milestone_id=m.id, plan_version=g.plan_version, seq=j, code=f"{seq}.{j}",
                         title=tt.title, description=tt.description, type=tt.type, difficulty=tt.difficulty,
                         risk=tt.risk, reversible=tt.reversible, acceptance_criteria=tt.criteria,
                         depends_on=[ids[k] for k in tt.depends_on if k in ids], input=tt.input,
                         expected_output=tt.expected_output, executor="step" if tt.step else "llm", step=tt.step,
                         verifier=tt.verifier, model=model, stages=list(tt.stages), max_attempts=tt.max_attempts,
                         est_steps=tt.est_steps, est_minutes=tt.est_minutes,
                         est_cost_usd=estimate_cost(model, tt.est_steps) if model else 0.0,
                         routing_reason=f"playbook {pb.name}: " + (f"code step {tt.step}" + (f" with {model}" if model else "")
                                                                   if tt.step else f"one model call with {model}"))
                t.approver = approver_for(t, g)
                ids[tt.key] = t.id
                new_tasks.append(t)
        self._finish(tree, a, "passed", reason=f"fixed plan from playbook {pb.name}",
                     output={"milestones": [f"M{m.seq} {m.title}" for m in new_ms]})
        tree.milestones.extend(new_ms)
        tree.tasks.extend(new_tasks)
        self.store.save(*new_ms, *new_tasks)

    def _plan_milestone(self, tree: GoalTree, m: Milestone, tools, plan_text: str) -> None:
        g = tree.goal
        a = self._act(tree, "plan", milestone=m, actor=self.brain.planner_model, input={"level": "milestone"})
        with self._node(f"plan M{m.seq}"):
            ans = self._ask(tree, a, TaskPlan, "plan_tasks", self.brain.planner_model, self._pack(
                "plan_tasks", self.brain.planner_model,
                title=g.title, description=g.description or "", constraints=bullets(g.constraints),
                milestones=plan_text, code=f"M{m.seq}", m_title=m.title, m_description=m.description,
                done_when=bullets(m.done_when), workers=workers_text(g, tools), task_types=task_types_text()))
        if ans is None:
            raise RuntimeError(f"planning M{m.seq} failed: {a.reason}")
        tasks = tasks_from(ans.value.tasks, g, m, bool(tools))
        self._finish(tree, a, "passed", ans, output={"tasks": [f"{t.code} {t.title}" for t in tasks]})
        tree.tasks.extend(tasks)
        self.store.save(*tasks)
        for t in tasks:
            if t.difficulty >= SPLIT_AT_DIFFICULTY and t.depth < MAX_DEPTH:
                self._split(tree, m, t, tools)

    def _split(self, tree: GoalTree, m: Milestone, t: Task, tools) -> None:
        g = tree.goal
        a = self._act(tree, "plan", task=t, actor=self.brain.planner_model, input={"level": "task", "why": "difficulty 5"})
        with self._node(f"plan {t.code}"):
            ans = self._ask(tree, a, TaskPlan, "split_task", self.brain.planner_model, self._pack(
                "split_task", self.brain.planner_model,
                title=g.title, constraints=bullets(g.constraints), m_code=f"M{m.seq}", m_title=m.title,
                code=t.code, t_title=t.title, t_description=t.description, t_input=t.input or "-",
                t_expected=t.expected_output, criteria=bullets(t.acceptance_criteria),
                workers=workers_text(g, tools), task_types=task_types_text()))
        if ans is None:                                   # keep it as one big task rather than stop planning
            return
        kids = tasks_from(ans.value.tasks, g, m, bool(tools), parent=t)
        self._finish(tree, a, "passed", ans, output={"subtasks": [f"{k.code} {k.title}" for k in kids]})
        as_container(t)
        tree.tasks.extend(kids)
        self.store.save(t, *kids)

    # ================================================================ running
    def run(self, goal_id: str, max_tasks: int | None = None) -> GoalTree:
        tree = self.store.load(goal_id)
        g = tree.goal
        if g.status in ("done", "cancelled"):
            return tree
        if not tree.active_milestones():
            raise ValueError(f"{g.id} has no plan yet: forgeone goal replan {g.id}")
        self.policy = resolve(g.context_policy)
        self.tasks_run, self.max_tasks = 0, max_tasks
        with self._session("goal.run", g):
            from .recovery import recover_interrupted

            self.store.save(*recover_interrupted(tree))
            g.status, g.note = "running", None
            g.started_at = g.started_at or now()
            self.store.save(g)
            try:
                self._run_goal(tree)
            except BudgetExhausted as e:
                g.status, g.note = "paused", str(e)
            except TaskLimit:
                g.status, g.note = "paused", f"stopped after {self.tasks_run} tasks (--max-tasks). Continue with: forgeone goal run {g.id}"
            finally:
                self.close()
                self.store.save(g)
                log_event("work.goal", goal=g.id, status=g.status, note=g.note, cost_usd=g.cost_usd)
        return tree

    def _run_goal(self, tree: GoalTree) -> None:
        g = tree.goal
        while True:
            replanned = False
            for m in tree.active_milestones():
                if m.status == "done":
                    continue
                outcome = self._run_milestone(tree, m)
                if outcome == "done":
                    continue
                if outcome == "waiting":
                    from .views import waiting_on_you

                    w = waiting_on_you(tree)
                    what = ", ".join(f"{x['code']} {x['title']}" for x in w[:2]) + (f" and {len(w) - 2} more" if len(w) > 2 else "")
                    g.status, g.note = "waiting", f"waiting for your approval: {what}"
                    return
                # failed
                if g.autonomy == "autonomous" and not g.playbook and g.plan_version - 1 < MAX_REPLANS:
                    self._plan(tree, reason=f"M{m.seq} {m.title} failed: {m.note}")
                    g.status = "running"
                    self.store.save(g)
                    replanned = True
                    break
                g.status, g.note = "failed", f"M{m.seq} {m.title} failed: {m.note}"
                g.finished_at = now()
                return
            if not replanned:
                g.status, g.note, g.finished_at = "done", None, now()
                return

    def _milestone_approval(self, tree: GoalTree, m: Milestone) -> Action | None:
        acts = [a for a in tree.actions if a.milestone_id == m.id and a.task_id is None and a.kind == "approve"]
        return max(acts, key=lambda a: a.started_at) if acts else None

    def _run_milestone(self, tree: GoalTree, m: Milestone) -> str:
        last = self._milestone_approval(tree, m)
        if last and last.status == "pending":
            m.status = "waiting"
            self.store.save(m)
            return "waiting"
        if last and last.status == "passed":
            m.status = "done"
            self.store.save(m)
            return "done"
        if last and last.status == "failed":               # rejected: needs a replan, not the same question again
            m.status, m.note = "failed", m.note or f"approval rejected: {last.reason or ''}"
            self.store.save(m)
            return "failed"
        if m.status != "running":
            m.status, m.started_at = "running", m.started_at or now()
            self.store.save(m)
        while True:
            progressed = False
            self.store.save(*sync_containers(tree, m.id))
            for t in tree.tasks_of(m.id):
                if t.status != "pending" or not t.stages:
                    continue
                deps = [d for d in (tree.task(i) for i in t.depends_on) if d is not None]
                bad = [d for d in deps if d.status in ("failed", "blocked", "skipped")]
                if bad:
                    t.status, t.failure_reason = "blocked", "blocked_dependency"
                    t.failure_detail = f"depends on {', '.join(d.code for d in bad)}, which did not finish"
                    self.store.save(t)
                    progressed = True
                    continue
                if any(d.status != "done" for d in deps):
                    continue
                if self.max_tasks is not None and self.tasks_run >= self.max_tasks:
                    raise TaskLimit()
                self._run_task(tree, m, t)
                self.tasks_run += 1
                progressed = True
            if not progressed:
                break
        self.store.save(*sync_containers(tree, m.id))
        tasks = tree.tasks_of(m.id)
        m.cost_usd = round(sum(t.cost_usd for t in tasks if t.stages)
                           + sum(a.cost_usd for a in tree.actions if a.milestone_id == m.id and a.task_id is None), 6)
        if any(t.status == "waiting" for t in tasks):
            m.status = "waiting"
            self.store.save(m)
            return "waiting"
        stuck = [t for t in tasks if t.status in ("failed", "blocked")]
        leftovers = [t for t in tasks if t.status == "pending" and t.stages]
        for t in leftovers:                                # unreachable: a cycle or an unknown dependency
            t.status, t.failure_reason, t.failure_detail = "blocked", "blocked_dependency", "dependency cycle"
            self.store.save(t)
        if stuck or leftovers:
            m.status = "failed"
            m.note = "; ".join(f"{t.code} {t.status}: {t.failure_detail or t.failure_reason}" for t in (stuck + leftovers)[:3])
            m.finished_at = now()
            self.store.save(m)
            return "failed"
        return self._approve_milestone(tree, m)

    def _approve_milestone(self, tree: GoalTree, m: Milestone) -> str:
        g = tree.goal
        who = milestone_approver(g, m)
        if who == "none":                                  # a playbook milestone that is done when its tasks are
            m.status, m.note, m.finished_at = "done", None, now()
            self.store.save(m)
            return "done"
        if who == "human":
            a = self._act(tree, "approve", milestone=m, actor_type="human", actor=self.human,
                          input={"done_when": m.done_when})
            a.status = "pending"
            m.status = "waiting"
            self.store.save(a, m)
            return "waiting"
        pack = self._pack("approve_milestone", self.brain.judge_model, goal=g.title, code=f"M{m.seq}",
                          title=m.title, description=m.description, done_when=bullets(m.done_when))
        pack.join("results", [(f"## {t.code} {t.title} ({t.status})", t.id, t.output)
                              for t in tree.tasks_of(m.id) if t.depth == 1], limit_each=MILESTONE_RESULT_LIMIT,
                  name="task result")
        pack.exclude("subtask outputs and earlier attempts", "a milestone is judged on its tasks' accepted results")
        a = self._act(tree, "approve", milestone=m, actor=self.brain.judge_model, input={"done_when": m.done_when})
        with self._node(f"approve M{m.seq}"):
            ans = self._ask(tree, a, Approval, "approve_milestone", self.brain.judge_model, pack)
        if ans is None:
            m.status, m.note = "failed", "milestone approval failed to run"
        else:
            v = ans.value
            self._finish(tree, a, "passed" if v.approved else "failed", ans, confidence=v.confidence,
                         reason=v.reason, feedback=v.feedback or None,
                         failure_reason=None if v.approved else "rejected")
            m.status, m.note = ("done", None) if v.approved else ("failed", f"approver: {v.reason}")
        m.finished_at = now()
        self.store.save(m)
        return "done" if m.status == "done" else "failed"

    # ---------------------------------------------------------------- one task
    def _run_task(self, tree: GoalTree, m: Milestone, t: Task) -> None:
        t.status, t.started_at = "running", t.started_at or now()
        self.store.save(t)
        if is_gate(t.stages):                              # irreversible: your go-ahead before it runs
            gates = [a for a in tree.actions_of(t.id) if a.kind == "approve" and a.input.get("gate")]
            if not any(a.status == "passed" for a in gates):
                if not any(a.status == "pending" for a in gates):
                    a = self._act(tree, "approve", task=t, attempt=t.attempts + 1, actor_type="human",
                                  actor=self.human, input={"gate": True, "criteria": t.acceptance_criteria})
                    a.status = "pending"
                    self.store.save(a)
                t.status = "waiting"
                self.store.save(t)
                return
        after = t.stages[t.stages.index("execute") + 1:]
        try:
            while t.attempts < t.max_attempts:
                self._check_budget(tree)
                t.attempts += 1
                n = t.attempts
                res = self._execute(tree, m, t, n)
                if not res.ok:
                    t.failure_reason, t.failure_detail = res.failure_reason or "other", res.error
                    t.feedback = f"The previous attempt failed: {res.error}. Take a different approach."
                    self.store.save(t)
                    continue
                t.output = res.output
                failed = None
                for stage in after:
                    self._check_budget(tree)
                    if stage == "approve" and t.approver == "human":
                        a = self._act(tree, "approve", task=t, attempt=n, actor_type="human", actor=self.human,
                                      input={"criteria": t.acceptance_criteria})
                        a.status = "pending"
                        t.status = "waiting"
                        self.store.save(a, t)
                        return
                    step = {"verify": self._verify, "edge_test": self._edge_test, "approve": self._approve}[stage]
                    a = step(tree, t, n, res)
                    if a.status == "failed":
                        failed = a
                        break
                if failed is None:
                    finish_task(tree, t, "done")
                    self.store.save(t)
                    self._on_done(tree, t)
                    return
                t.failure_reason = failed.failure_reason or ("rejected" if failed.kind == "approve" else "criteria_not_met")
                t.failure_detail = f"{failed.kind}: {failed.reason}"
                t.feedback = failed.feedback or failed.reason
                self.store.save(t)
            finish_task(tree, t, "failed")
            self.store.save(t)
        except BudgetExhausted:
            t.status = "pending"                           # picked up again when the goal continues
            self.store.save(t)
            raise

    def _on_done(self, tree: GoalTree, t: Task) -> None:
        if t.step and tree.goal.playbook:
            hook = load_playbook(tree.goal.playbook).on_done.get(t.step)
            if hook:
                hook(StepContext(goal=tree.goal, tree=tree, task=t, brain=self.brain,
                                 deps=[d for d in (tree.task(i) for i in t.depends_on) if d], run_id=self.run_id))

    def _context(self, tree: GoalTree, m: Milestone, t: Task, model: str | None) -> ContextPack:
        """What the executor sees. Recorded section by section in the execute action's manifest."""
        deps = [d for d in (tree.task(i) for i in t.depends_on) if d is not None]
        p = ContextPack("execute", model)
        p.add("goal", tree.goal.title, required=True)
        p.add("constraints", bullets(tree.goal.constraints))
        p.add("milestone", f"M{m.seq} {m.title}")
        for k, v in (("code", t.code), ("title", t.title), ("description", t.description), ("input", t.input or "-"),
                     ("expected_output", t.expected_output)):
            p.add(k, v, name="task", required=True)
        p.add("criteria", bullets(t.acceptance_criteria), name="acceptance criteria", required=True)
        p.join("deps", [(f"## {d.code} {d.title}", d.id, d.output) for d in deps], limit_each=DEP_LIMIT,
               name="dependency output", priority=P_DEPS)
        p.add("feedback", f"\n# Feedback on your previous attempt\n{t.feedback}\n" if t.feedback else "",
              name="feedback", source=f"attempt {t.attempts - 1}" if t.feedback else None, required=True)
        fails = [a for a in tree.actions_of(t.id) if a.status == "failed" and a.kind != "plan" and not a.input.get("gate")]
        earlier = fails[:-1] if t.feedback else fails      # the latest one is the feedback above
        if self.policy.attempt_history:
            p.join("history", [(None, f"attempt {a.attempt}",
                                f"- attempt {a.attempt}, {a.kind.replace('_', ' ')}: {a.reason or ''}"
                                + (f" Asked to fix: {a.feedback}" if a.feedback else "")) for a in earlier],
                   name="earlier attempts", priority=P_HISTORY, empty="", sep="\n",
                   prefix="\n# What went wrong in earlier attempts (do not repeat it)\n")
        else:
            p.add("history", "", name="earlier attempts")
            if earlier:
                p.exclude("feedback of attempts before the last one", f"context policy {self.policy.name}")
        lessons = []
        if self.policy.lessons:
            try:
                lessons = find_lessons(self.store.failures(t.type, exclude_task=t.id), t.title, t.description,
                                       limit=self.policy.max_lessons)
            except Exception as e:  # noqa: BLE001  (memory must never stop the work)
                log_event("memory.error", logging.WARNING, error=f"{type(e).__name__}: {e}")
            p.join("lessons", [(None, x.task_id, f"- {x.text}") for x in lessons], name="lessons from earlier work",
                   priority=P_LESSONS, empty="", sep="\n",
                   prefix="\n# Lessons from similar earlier work (avoid these mistakes)\n")
        else:
            p.add("lessons", "", name="lessons from earlier work")
            p.exclude("lessons from earlier work", f"context policy {self.policy.name}")
        p.exclude("outputs of earlier attempts", "only what went wrong in them is passed on")
        p.exclude("tasks this one does not depend on", "only direct dependencies are included")
        return p

    def _execute(self, tree: GoalTree, m: Milestone, t: Task, n: int) -> ExecResult:
        if t.executor == "step":
            executor = StepExecutor(self, tree)
        else:
            executor = self.agent(tree.goal) if t.executor == "agent" else None
            if executor is not None:
                executor.policy = self.policy
            executor = executor or LLMExecutor(self.brain)
        a = self._act(tree, "execute", task=t, attempt=n,
                      actor=f"step:{t.step}" if t.executor == "step" else f"{executor.name}:{t.model}",
                      actor_type="tool" if t.executor == "step" and not t.model else "ai",
                      input={"executor": executor.name, "depends_on": t.depends_on, "feedback": t.feedback})
        pack = self._context(tree, m, t, t.model)
        prompt = "execute_agent" if executor.name == "agent" else "execute"
        self._fit(pack, prompt, t.model)
        a.context = pack.manifest(template(prompt), prompt_version(prompt)) if executor.name != "step" else {}
        with self._node(self._label("execute", t), n):
            res = executor.run(t, pack.values)
        if res.context:                                    # steps bring their own manifest; agents add their history
            a.context = {**a.context, **res.context} if a.context else res.context
        if res.tokens_in and a.context:
            a.context["tokens_billed"] = res.tokens_in
        over = bool(t.timeout_s and res.duration_ms > t.timeout_s * 1000)
        self._finish(tree, a, "passed" if res.ok else "failed", model=res.model, prompt_version=res.prompt_version,
                     cost_usd=res.cost_usd, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                     duration_ms=res.duration_ms, reason=res.error, failure_reason=res.failure_reason,
                     output={"text": res.output, "evidence": res.evidence, "over_time": over})
        return res

    def _verify(self, tree: GoalTree, t: Task, n: int, res: ExecResult) -> Action:
        checks = res.evidence.get("checks") or []
        hard = [c for c in checks if c.get("hard", True) and not c["passed"]]
        as_criteria = [{"criterion": c["name"], "met": c["passed"], "note": c.get("detail", "")} for c in checks]
        if hard or t.verifier == "checks":                 # facts beat opinions: no model call needed
            a = self._act(tree, "verify", task=t, attempt=n, actor_type="tool", actor="checks")
            if not hard and checks:
                soft = [c["name"] for c in checks if not c["passed"]]
                return self._finish(tree, a, "passed", confidence=1.0, criteria=as_criteria,
                                    reason=f"{len(checks) - len(soft)} of {len(checks)} checks passed"
                                           + (f"; warnings: {', '.join(soft)}" if soft else ""))
            failure = (hard[0].get("failure_reason") or CHECK_FAILURE.get(hard[0]["name"], "criteria_not_met")
                       if hard else "criteria_not_met")
            return self._finish(tree, a, "failed", confidence=1.0, criteria=as_criteria, failure_reason=failure,
                                reason="; ".join(f"{c['name']}: {c.get('detail', '')}" for c in hard) or "no checks ran",
                                feedback=" ".join(c.get("fix") or FIX.get(c["name"]) or f"{c['name']}: {c.get('detail', '')}"
                                                  for c in hard) or None)
        evidence = ""
        if res.evidence:
            e = res.evidence
            evidence = ("\n# Evidence from the run (recorded facts, not claims)\n"
                        f"- pages the agent actually opened: {', '.join(e.get('opened_urls') or []) or 'none'}\n"
                        f"- automatic checks: {', '.join(c['name'] + (' passed' if c['passed'] else ' FAILED') for c in checks)}\n")
        a = self._act(tree, "verify", task=t, attempt=n, actor=self.brain.judge_model,
                      input={"criteria": len(t.acceptance_criteria), "evidence": bool(evidence)})
        pack = ContextPack("verify", self.brain.judge_model)
        pack.add("title", t.title, name="task", required=True)
        pack.add("expected_output", t.expected_output, name="task", required=True)
        pack.add("criteria", numbered(t.acceptance_criteria), name="acceptance criteria", required=True)
        pack.add("evidence", evidence, name="evidence", source="run facts", priority=P_EVIDENCE)
        pack.add("output", res.output, name="output", source=f"execute attempt {n}", limit=OUTPUT_LIMIT, required=True,
                 cuttable=True)
        pack.fixed("failure_reasons", ", ".join(FAILURE_REASONS))
        pack.exclude("the executor's reasoning, prompt and tool calls", "the reviewer judges the output, not the story")
        with self._node(self._label("verify", t), n):
            ans = self._ask(tree, a, Verdict, "verify", self.brain.judge_model, pack)
        if ans is None:
            return a
        v = ans.value
        passed = v.passed and bool(v.criteria) and all(c.met for c in v.criteria)
        return self._finish(tree, a, "passed" if passed else "failed", ans, confidence=v.confidence, reason=v.reason,
                            feedback=(v.feedback or None) if not passed else None,
                            failure_reason=None if passed else (v.failure_reason or "criteria_not_met"),
                            criteria=[c.model_dump() for c in v.criteria])

    def _edge_test(self, tree: GoalTree, t: Task, n: int, res: ExecResult) -> Action:
        a = self._act(tree, "edge_test", task=t, attempt=n, actor=self.brain.judge_model)
        pack = self._pack("edge_test", self.brain.judge_model, title=t.title, description=t.description,
                          expected_output=t.expected_output, criteria=bullets(t.acceptance_criteria))
        pack.add("output", res.output, name="output", source=f"execute attempt {n}", limit=OUTPUT_LIMIT, required=True,
                 cuttable=True)
        pack.exclude("the executor's reasoning", "edge cases are tested against the output only")
        with self._node(self._label("edge_test", t), n):
            ans = self._ask(tree, a, EdgeReport, "edge_test", self.brain.judge_model, pack)
        if ans is None:
            return a
        r = ans.value
        passed = r.passed and all(c.handled for c in r.cases)
        missed = [c.case for c in r.cases if not c.handled]
        return self._finish(tree, a, "passed" if passed else "failed", ans,
                            reason=f"{len(r.cases) - len(missed)} of {len(r.cases)} edge cases handled"
                                   + (f"; missed: {'; '.join(missed)}" if missed else ""),
                            feedback=(r.feedback or None) if not passed else None,
                            failure_reason=None if passed else "criteria_not_met",
                            criteria=[{"criterion": c.case, "met": c.handled, "note": c.note} for c in r.cases])

    def _approve(self, tree: GoalTree, t: Task, n: int, res: ExecResult) -> Action:
        reviews = "\n".join(f"- {a.kind}: {'passed' if a.status == 'passed' else 'failed'}. {a.reason or ''}"
                            for a in tree.actions_of(t.id) if a.attempt == n and a.kind in ("verify", "edge_test"))
        a = self._act(tree, "approve", task=t, attempt=n, actor=self.brain.judge_model)
        pack = self._pack("approve", self.brain.judge_model, goal=tree.goal.title, code=t.code, title=t.title,
                          expected_output=t.expected_output, criteria=bullets(t.acceptance_criteria))
        pack.add("reviews", reviews or "- none", name="reviews", source=f"attempt {n}", priority=P_REVIEWS)
        pack.add("output", res.output, name="output", source=f"execute attempt {n}", limit=OUTPUT_LIMIT, required=True,
                 cuttable=True)
        with self._node(self._label("approve", t), n):
            ans = self._ask(tree, a, Approval, "approve", self.brain.judge_model, pack)
        if ans is None:
            return a
        v = ans.value
        return self._finish(tree, a, "passed" if v.approved else "failed", ans, confidence=v.confidence,
                            reason=v.reason, feedback=(v.feedback or None) if not v.approved else None,
                            failure_reason=None if v.approved else "rejected")
