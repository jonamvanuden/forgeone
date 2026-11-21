-- Eval results: one eval run = one agent version against one suite version.
CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id    TEXT PRIMARY KEY,
    agent          TEXT NOT NULL,
    agent_version  TEXT NOT NULL,          -- hash of the AgentSpec
    suite_version  TEXT NOT NULL,          -- hash of the suite; compare only like with like
    model          TEXT,
    repeats        INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running',
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    scorecard      JSONB,                  -- forgeone.evals.scoring.Scorecard
    gate           JSONB,                  -- forgeone.evals.gate.GateDecision
    meta           JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS eval_runs_agent ON eval_runs (agent, started_at);

CREATE TABLE IF NOT EXISTS eval_results (
    id            BIGSERIAL PRIMARY KEY,
    eval_run_id   TEXT NOT NULL REFERENCES eval_runs(eval_run_id) ON DELETE CASCADE,
    case_id       TEXT NOT NULL,
    category      TEXT NOT NULL,
    repeat        INTEGER NOT NULL,
    passed        BOOLEAN NOT NULL,
    hard_failures TEXT[] NOT NULL DEFAULT '{}',
    checks        JSONB NOT NULL DEFAULT '[]',
    judge_scores  JSONB NOT NULL DEFAULT '{}',
    failure_tags  TEXT[] NOT NULL DEFAULT '{}',
    cost_usd      NUMERIC,
    duration_ms   INTEGER,
    trace_run_id  TEXT,                    -- the agent run in `runs`, for drill-down
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (eval_run_id, case_id, repeat)
);
CREATE INDEX IF NOT EXISTS eval_results_case ON eval_results (case_id, created_at);
