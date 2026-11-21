-- ForgeOne event store. Idempotent: safe to run again.
-- Every row that comes from a log event carries event_id (UNIQUE), so replays
-- and backfills never create duplicates.

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    name                TEXT,
    goal                TEXT,
    model               TEXT,
    status              TEXT NOT NULL DEFAULT 'running',   -- running | ok | error
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    duration_ms         INTEGER,
    llm_calls           INTEGER NOT NULL DEFAULT 0,
    total_input_tokens  INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    meta                JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id         BIGSERIAL PRIMARY KEY,
    event_id   TEXT NOT NULL UNIQUE,
    ts         TIMESTAMPTZ NOT NULL,
    run_id     TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    level      TEXT NOT NULL,
    event      TEXT NOT NULL,
    node       TEXT,
    iteration  INTEGER,
    fields     JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_run_ts ON events (run_id, ts);
CREATE INDEX IF NOT EXISTS events_event  ON events (event);
CREATE INDEX IF NOT EXISTS events_errors ON events (ts) WHERE level IN ('ERROR', 'CRITICAL');

CREATE TABLE IF NOT EXISTS llm_calls (
    id            BIGSERIAL PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    ts            TIMESTAMPTZ NOT NULL,
    run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node          TEXT,
    iteration     INTEGER,
    model         TEXT,
    status        TEXT NOT NULL,          -- ok | error
    latency_ms    INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    stop_reason   TEXT,
    error         TEXT,
    prompt        JSONB,                  -- full messages sent
    response      JSONB                   -- full content + tool calls
);
CREATE INDEX IF NOT EXISTS llm_calls_run   ON llm_calls (run_id, ts);
CREATE INDEX IF NOT EXISTS llm_calls_model ON llm_calls (model, ts);

CREATE TABLE IF NOT EXISTS decisions (
    id        BIGSERIAL PRIMARY KEY,
    event_id  TEXT NOT NULL UNIQUE,
    ts        TIMESTAMPTZ NOT NULL,
    run_id    TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node      TEXT,
    iteration INTEGER,
    decision  TEXT NOT NULL,
    reason    TEXT,
    details   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions (run_id, ts);

CREATE TABLE IF NOT EXISTS artifacts (
    id        BIGSERIAL PRIMARY KEY,
    event_id  TEXT NOT NULL UNIQUE,
    ts        TIMESTAMPTZ NOT NULL,
    run_id    TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node      TEXT,
    iteration INTEGER,
    kind      TEXT NOT NULL,              -- spec | report | verdict | ...
    name      TEXT,
    body      JSONB
);
CREATE INDEX IF NOT EXISTS artifacts_run  ON artifacts (run_id, ts);
CREATE INDEX IF NOT EXISTS artifacts_kind ON artifacts (kind, name, ts);

-- Dashboard helpers ------------------------------------------------------------
CREATE OR REPLACE VIEW v_node_stats AS
SELECT run_id, node,
       count(*)                       AS llm_calls,
       sum(input_tokens)              AS input_tokens,
       sum(output_tokens)             AS output_tokens,
       round(avg(latency_ms))         AS avg_latency_ms,
       count(*) FILTER (WHERE status = 'error') AS errors
FROM llm_calls GROUP BY run_id, node;

CREATE OR REPLACE VIEW v_daily_usage AS
SELECT date_trunc('day', ts) AS day, model,
       count(*) AS llm_calls, sum(input_tokens) AS input_tokens,
       sum(output_tokens) AS output_tokens
FROM llm_calls GROUP BY 1, 2;