-- 004: the work model. Goal -> milestones -> tasks -> actions.
-- Columns mirror forgeone.work.model one to one (the store upserts whole records).

CREATE TABLE IF NOT EXISTS goals (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    agent            TEXT,
    agent_type       TEXT NOT NULL DEFAULT 'generic',
    success_criteria JSONB NOT NULL DEFAULT '[]',
    constraints      JSONB NOT NULL DEFAULT '[]',
    budget_usd       DOUBLE PRECISION,
    deadline         TIMESTAMPTZ,
    autonomy         TEXT NOT NULL DEFAULT 'supervised',
    status           TEXT NOT NULL,
    note             TEXT,
    plan_version     INT NOT NULL DEFAULT 0,
    cost_usd         DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS milestones (
    id           TEXT PRIMARY KEY,
    goal_id      TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    plan_version INT NOT NULL,
    seq          INT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    done_when    JSONB NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL,
    note         TEXT,
    cost_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS milestones_goal ON milestones (goal_id, seq);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    goal_id             TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    milestone_id        TEXT NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
    parent_id           TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    plan_version        INT NOT NULL,
    seq                 INT NOT NULL,
    code                TEXT NOT NULL,
    depth               INT NOT NULL DEFAULT 1,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    type                TEXT NOT NULL,
    difficulty          INT NOT NULL,
    risk                TEXT NOT NULL,
    reversible          BOOLEAN NOT NULL DEFAULT true,
    acceptance_criteria JSONB NOT NULL DEFAULT '[]',
    depends_on          JSONB NOT NULL DEFAULT '[]',
    input               TEXT NOT NULL DEFAULT '',
    expected_output     TEXT NOT NULL DEFAULT '',
    executor            TEXT NOT NULL,
    model               TEXT,
    routing_reason      TEXT NOT NULL DEFAULT '',
    stages              JSONB NOT NULL DEFAULT '[]',
    approver            TEXT,
    max_attempts        INT NOT NULL DEFAULT 3,
    budget_usd          DOUBLE PRECISION,
    timeout_s           INT,
    est_steps           INT,
    est_minutes         DOUBLE PRECISION,
    est_cost_usd        DOUBLE PRECISION,
    status              TEXT NOT NULL,
    attempts            INT NOT NULL DEFAULT 0,
    failure_reason      TEXT,
    failure_detail      TEXT,
    feedback            TEXT,
    output              TEXT,
    cost_usd            DOUBLE PRECISION NOT NULL DEFAULT 0,
    tokens_in           INT NOT NULL DEFAULT 0,
    tokens_out          INT NOT NULL DEFAULT 0,
    duration_ms         INT NOT NULL DEFAULT 0,
    actual_difficulty   DOUBLE PRECISION,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_goal ON tasks (goal_id);
CREATE INDEX IF NOT EXISTS tasks_milestone ON tasks (milestone_id);

CREATE TABLE IF NOT EXISTS actions (
    id             TEXT PRIMARY KEY,
    goal_id        TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    milestone_id   TEXT REFERENCES milestones(id) ON DELETE CASCADE,
    task_id        TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,          -- plan | execute | verify | edge_test | approve
    attempt        INT NOT NULL DEFAULT 1,
    actor_type     TEXT NOT NULL,          -- ai | human | tool
    actor          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,          -- running | passed | failed | pending
    confidence     DOUBLE PRECISION,
    reason         TEXT,
    feedback       TEXT,
    failure_reason TEXT,
    criteria       JSONB NOT NULL DEFAULT '[]',
    input          JSONB NOT NULL DEFAULT '{}',
    output         JSONB NOT NULL DEFAULT '{}',
    prompt_version TEXT,
    model          TEXT,
    cost_usd       DOUBLE PRECISION NOT NULL DEFAULT 0,
    tokens_in      INT NOT NULL DEFAULT 0,
    tokens_out     INT NOT NULL DEFAULT 0,
    duration_ms    INT NOT NULL DEFAULT 0,
    run_id         TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS actions_task ON actions (task_id, started_at);
CREATE INDEX IF NOT EXISTS actions_goal ON actions (goal_id);
CREATE INDEX IF NOT EXISTS actions_pending ON actions (status) WHERE status = 'pending';

-- Knowledge: how each stage performs per task type and model. Routing rules come from here.
CREATE OR REPLACE VIEW v_stage_knowledge AS
SELECT t.type AS task_type, a.kind AS stage, coalesce(a.model, a.actor) AS model,
       count(*)                                                     AS actions,
       avg(CASE WHEN a.status = 'passed' THEN 1.0 ELSE 0.0 END)     AS pass_rate,
       avg(a.cost_usd)                                              AS mean_cost_usd,
       avg(a.duration_ms)                                           AS mean_duration_ms,
       avg(a.confidence)                                            AS mean_confidence
FROM actions a JOIN tasks t ON t.id = a.task_id
WHERE a.status IN ('passed', 'failed')
GROUP BY 1, 2, 3;

-- Planner calibration: predicted vs. actual per task type.
CREATE OR REPLACE VIEW v_task_calibration AS
SELECT type AS task_type, model,
       count(*)                                                      AS tasks,
       avg(CASE WHEN status = 'done' THEN 1.0 ELSE 0.0 END)          AS done_rate,
       avg(CASE WHEN status = 'done' AND attempts = 1 THEN 1.0 ELSE 0.0 END) AS first_try_rate,
       avg(difficulty)                                               AS predicted_difficulty,
       avg(actual_difficulty)                                        AS actual_difficulty,
       avg(est_cost_usd)                                             AS est_cost_usd,
       avg(cost_usd)                                                 AS actual_cost_usd,
       avg(est_minutes * 60000)                                      AS est_duration_ms,
       avg(duration_ms)                                              AS actual_duration_ms
FROM tasks
WHERE status IN ('done', 'failed') AND stages <> '[]'::jsonb
GROUP BY 1, 2;

-- What goes wrong most.
CREATE OR REPLACE VIEW v_failure_reasons AS
SELECT t.type AS task_type, a.kind AS stage, a.failure_reason, count(*) AS n
FROM actions a JOIN tasks t ON t.id = a.task_id
WHERE a.status = 'failed' AND a.failure_reason IS NOT NULL
GROUP BY 1, 2, 3;
