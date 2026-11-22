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

from contextlib import contextmanager

from ..observability import log_event, start_run
from ..observability.context import iteration_var, node_var, run_id_var
from .brain import Answer, Brain
from .executors import CHECK_FAILURE, AgentExecutor, ExecResult, LLMExecutor
from .model import FAILURE_REASONS, Action, Goal, GoalTree, Milestone, Task, now
from .planner import bullets, milestones_from, numbered, task_types_text, tasks_from, workers_text
from .policy import MAX_DEPTH, MAX_REPLANS, SPLIT_AT_DIFFICULTY, actual_difficulty, as_container, milestone_approver
from .schemas import Approval, EdgeReport, MilestonePlan, TaskPlan, Verdict
from .store import WorkStore, default_store

OUTPUT_LIMIT = 30000      # characters of output a reviewer sees
DEP_LIMIT = 4000          # characters of each dependency's output an executor sees


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

    # ================================================================ plumbing
    @contextmanager
    def _session(self, name: str, goal: Goal):
        if run_id_var.get():                               # already inside a run (tests, nested use)
            self.run_id = run_id_var.get()
            yield
            return
        with start_run(name, goal_id=goal.id, title=goal.title, agent=goal.agent) as rid:
            self.run_id = rid
            yield

    @contextmanager
    def _node(self, name: str, attempt: int | None = None):
        a, b = node_var.set(name), iteration_var.set(attempt)
        try:
            yield
        finally:
            node_var.reset(a)
            iteration_var.reset(b)

    def agent(self, goal: Goal) -> AgentExecutor | None:
        if not goal.agent:
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

    def _ask(self, tree: GoalTree, a: Action, schema, prompt: str, model: str, **values) -> Answer | None:
        """A model call for an action; a failed call closes the action as failed (model_error)."""
        try:
            return self.brain.ask(schema, prompt, model, **values)
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
        history = ""
        if g.plan_version > 1:
            history = (f"\n# This is a replan (plan version {g.plan_version})\nReason: {reason or 'requested'}\n"
                       f"Already finished, build on these and do not repeat them:\n"
                       f"{bullets([f'{m.title}: ' + '; '.join(m.done_when) for m in done])}\n")
        a = self._act(tree, "plan", actor=self.brain.planner_model,
                      input={"level": "goal", "plan_version": g.plan_version, "reason": reason,
                             "kept_milestones": [m.id for m in done]})
        with self._node("plan goal"):
            ans = self._ask(tree, a, MilestonePlan, "plan_milestones", self.brain.planner_model,
                            title=g.title, description=g.description or "(no further description)",
                            success_criteria=bullets(g.success_criteria), constraints=bullets(g.constraints),
                            workers=workers_text(g, tools), history=history)
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

    def _plan_milestone(self, tree: GoalTree, m: Milestone, tools, plan_text: str) -> None:
        g = tree.goal
        a = self._act(tree, "plan", milestone=m, actor=self.brain.planner_model, input={"level": "milestone"})
        with self._node(f"plan M{m.seq}"):
            ans = self._ask(tree, a, TaskPlan, "plan_tasks", self.brain.planner_model,
                            title=g.title, description=g.description or "", constraints=bullets(g.constraints),
                            milestones=plan_text, code=f"M{m.seq}", m_title=m.title, m_description=m.description,
                            done_when=bullets(m.done_when), workers=workers_text(g, tools),
                            task_types=task_types_text())
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
            ans = self._ask(tree, a, TaskPlan, "split_task", self.brain.planner_model,
                            title=g.title, constraints=bullets(g.constraints), m_code=f"M{m.seq}", m_title=m.title,
                            code=t.code, t_title=t.title, t_description=t.description, t_input=t.input or "-",
                            t_expected=t.expected_output, criteria=bullets(t.acceptance_criteria),
                            workers=workers_text(g, tools), task_types=task_types_text())
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
        self.tasks_run, self.max_tasks = 0, max_tasks
        with self._session("goal.run", g):
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
                if g.autonomy == "autonomous" and g.plan_version - 1 < MAX_REPLANS:
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
        if milestone_approver(g) == "human":
            a = self._act(tree, "approve", milestone=m, actor_type="human", actor=self.human,
                          input={"done_when": m.done_when})
            a.status = "pending"
            m.status = "waiting"
            self.store.save(a, m)
            return "waiting"
        results = "\n\n".join(f"## {t.code} {t.title} ({t.status})\n{_cut(t.output, 1500)}"
                              for t in tree.tasks_of(m.id) if t.depth == 1)
        a = self._act(tree, "approve", milestone=m, actor=self.brain.judge_model, input={"done_when": m.done_when})
        with self._node(f"approve M{m.seq}"):
            ans = self._ask(tree, a, Approval, "approve_milestone", self.brain.judge_model, goal=g.title,
                            code=f"M{m.seq}", title=m.title, description=m.description,
                            done_when=bullets(m.done_when), results=results)
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
                for stage in t.stages[1:]:
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

    def _context(self, tree: GoalTree, m: Milestone, t: Task) -> dict:
        deps = [d for d in (tree.task(i) for i in t.depends_on) if d is not None]
        return dict(goal=tree.goal.title, constraints=bullets(tree.goal.constraints), milestone=f"M{m.seq} {m.title}",
                    code=t.code, title=t.title, description=t.description, input=t.input or "-",
                    expected_output=t.expected_output, criteria=bullets(t.acceptance_criteria),
                    deps="\n\n".join(f"## {d.code} {d.title}\n{_cut(d.output, DEP_LIMIT)}" for d in deps) or "none",
                    feedback=f"\n# Feedback on your previous attempt\n{t.feedback}\n" if t.feedback else "")

    def _execute(self, tree: GoalTree, m: Milestone, t: Task, n: int) -> ExecResult:
        executor = self.agent(tree.goal) if t.executor == "agent" else None
        executor = executor or LLMExecutor(self.brain)
        a = self._act(tree, "execute", task=t, attempt=n, actor=f"{executor.name}:{t.model}",
                      input={"executor": executor.name, "depends_on": t.depends_on, "feedback": t.feedback})
        with self._node(f"execute {t.code}", n):
            res = executor.run(t, self._context(tree, m, t))
        over = bool(t.timeout_s and res.duration_ms > t.timeout_s * 1000)
        self._finish(tree, a, "passed" if res.ok else "failed", model=res.model, prompt_version=res.prompt_version,
                     cost_usd=res.cost_usd, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                     duration_ms=res.duration_ms, reason=res.error, failure_reason=res.failure_reason,
                     output={"text": res.output, "evidence": res.evidence, "over_time": over})
        return res

    def _verify(self, tree: GoalTree, t: Task, n: int, res: ExecResult) -> Action:
        checks = res.evidence.get("checks") or []
        hard = [c for c in checks if c["hard"] and not c["passed"]]
        if hard:                                           # facts beat opinions: no model call needed
            a = self._act(tree, "verify", task=t, attempt=n, actor_type="tool", actor="checks")
            return self._finish(tree, a, "failed", confidence=1.0,
                                reason="; ".join(f"{c['name']}: {c['detail']}" for c in hard),
                                failure_reason=CHECK_FAILURE.get(hard[0]["name"], "other"),
                                feedback="Cite only pages you opened in this attempt, and quote text exactly as it "
                                         "appears on the page.",
                                criteria=[{"criterion": c["name"], "met": c["passed"], "note": c["detail"]} for c in checks])
        evidence = ""
        if res.evidence:
            e = res.evidence
            evidence = ("\n# Evidence from the run (recorded facts, not claims)\n"
                        f"- pages the agent actually opened: {', '.join(e.get('opened_urls') or []) or 'none'}\n"
                        f"- automatic checks: {', '.join(c['name'] + (' passed' if c['passed'] else ' FAILED') for c in checks)}\n")
        a = self._act(tree, "verify", task=t, attempt=n, actor=self.brain.judge_model,
                      input={"criteria": len(t.acceptance_criteria), "evidence": bool(evidence)})
        with self._node(f"verify {t.code}", n):
            ans = self._ask(tree, a, Verdict, "verify", self.brain.judge_model, title=t.title,
                            expected_output=t.expected_output, criteria=numbered(t.acceptance_criteria),
                            evidence=evidence, output=_cut(res.output, OUTPUT_LIMIT),
                            failure_reasons=", ".join(FAILURE_REASONS))
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
        with self._node(f"edge_test {t.code}", n):
            ans = self._ask(tree, a, EdgeReport, "edge_test", self.brain.judge_model, title=t.title,
                            description=t.description, expected_output=t.expected_output,
                            criteria=bullets(t.acceptance_criteria), output=_cut(res.output, OUTPUT_LIMIT))
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
        with self._node(f"approve {t.code}", n):
            ans = self._ask(tree, a, Approval, "approve", self.brain.judge_model, goal=tree.goal.title, code=t.code,
                            title=t.title, expected_output=t.expected_output, criteria=bullets(t.acceptance_criteria),
                            reviews=reviews or "- none", output=_cut(res.output, OUTPUT_LIMIT))
        if ans is None:
            return a
        v = ans.value
        return self._finish(tree, a, "passed" if v.approved else "failed", ans, confidence=v.confidence,
                            reason=v.reason, feedback=(v.feedback or None) if not v.approved else None,
                            failure_reason=None if v.approved else "rejected")
