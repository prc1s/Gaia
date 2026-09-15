CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    employee_id       TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('pending','running','paused','completed','failed','cancelled')),
    current_step      INTEGER NOT NULL DEFAULT 1,
    pause_reason      TEXT,
    approval_decision TEXT CHECK (approval_decision IN ('approved','rejected')),
    owner_token       TEXT,
    lease_expires_at  TEXT,
    max_steps         INTEGER NOT NULL DEFAULT 20,
    tool_call_count   INTEGER NOT NULL DEFAULT 0,
    max_tool_calls    INTEGER NOT NULL DEFAULT 40,
    error             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    run_id       TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','skipped')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    output       TEXT,
    error        TEXT,
    started_at   TEXT,
    completed_at TEXT,
    PRIMARY KEY (run_id, idx),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS tool_invocations (
    idempotency_key TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    step_idx        INTEGER NOT NULL,
    tool            TEXT NOT NULL,
    args            TEXT,
    status          TEXT NOT NULL CHECK (status IN ('in_flight','succeeded','failed')),
    result          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS effects (
    idempotency_key TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL,
    step_idx INTEGER,
    event    TEXT NOT NULL,
    detail   TEXT,
    at       TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace(run_id, id);
CREATE INDEX IF NOT EXISTS idx_inv_run ON tool_invocations(run_id, step_idx);
CREATE UNIQUE INDEX IF NOT EXISTS idx_effects_kind ON effects(idempotency_key, kind);
