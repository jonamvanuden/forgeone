-- 005: playbooks (fixed plans for known processes, like building an agent), code steps,
-- checks-only verification and per-milestone approval.
ALTER TABLE goals      ADD COLUMN IF NOT EXISTS playbook      TEXT;
ALTER TABLE goals      ADD COLUMN IF NOT EXISTS playbook_args JSONB NOT NULL DEFAULT '{}';
ALTER TABLE milestones ADD COLUMN IF NOT EXISTS approval      TEXT;
ALTER TABLE tasks      ADD COLUMN IF NOT EXISTS step          TEXT;
ALTER TABLE tasks      ADD COLUMN IF NOT EXISTS verifier      TEXT NOT NULL DEFAULT 'model';
