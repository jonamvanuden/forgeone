-- 006: the planner marks tasks that plain code could do without a model (Support page:
-- "could run without AI").
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS automatable     BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS automation_hint TEXT    NOT NULL DEFAULT '';
