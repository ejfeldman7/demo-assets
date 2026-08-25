-- Workload Watchtower — Lakebase (Postgres) OLTP schema.
-- database: databricks_postgres · schema: {schema} (substituted from LAKEBASE_SCHEMA by
-- bootstrap.py). Holds mutable triage/operational state; append-only history lives in UC
-- Delta (see uc_ddl.sql). Created once by the schema owner via `python -m db.bootstrap`.

CREATE SCHEMA IF NOT EXISTS {schema};
SET search_path TO {schema};

-- Seeded roster of IT-org members who can be assigned triage cards.
CREATE TABLE IF NOT EXISTS it_members (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL UNIQUE,
    role        TEXT        NOT NULL DEFAULT 'triager',   -- admin | triager | oncall
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Distribution list ("list serve") for email automations — alerts are sent to every
-- active subscriber. Managed from the app's Actions view.
CREATE TABLE IF NOT EXISTS subscribers (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       TEXT        NOT NULL UNIQUE,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Threshold + automation rules. A finding is raised when a live workload of
-- `workload_type` crosses `threshold` on `metric`; `action` decides what fires.
CREATE TABLE IF NOT EXISTS rules (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           TEXT        NOT NULL UNIQUE,
    workload_type  TEXT        NOT NULL,   -- query | job_run | pipeline | cluster | serving
    metric         TEXT        NOT NULL DEFAULT 'elapsed_sec',  -- elapsed_sec | est_cost_usd
    threshold      DOUBLE PRECISION NOT NULL,
    severity       TEXT        NOT NULL DEFAULT 'warning',      -- info | warning | critical
    action         TEXT        NOT NULL DEFAULT 'card',         -- none | card | email | card_email
    enabled        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per detected workload occurrence. The poller upserts on
-- (workload_type, external_id): first_seen stays, last_seen/elapsed/est_cost refresh.
CREATE TABLE IF NOT EXISTS findings (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workload_type  TEXT        NOT NULL,
    external_id    TEXT        NOT NULL,   -- query_id | run_id | update_id | cluster_id | endpoint
    owner          TEXT,                   -- user or SP that launched it
    object_name    TEXT,                   -- job/pipeline/endpoint name or query snippet
    compute_ref    TEXT,                   -- warehouse_id / cluster_id / endpoint name
    started_at     TIMESTAMPTZ,
    elapsed_sec    DOUBLE PRECISION,
    est_cost_usd   DOUBLE PRECISION,
    severity       TEXT        NOT NULL DEFAULT 'warning',
    health_status  TEXT,                   -- GOOD | INFO | WARNING | CRITICAL (Kanban color)
    alert_priority INTEGER     NOT NULL DEFAULT 0,   -- 0-100 triage sort score
    violation_reason TEXT,                 -- LONG_RUNNING | COST_BURST | STATEMENT_TIMEOUT_OVERRIDE | ...
    matched_rule   BIGINT      REFERENCES rules(id),
    status         TEXT        NOT NULL DEFAULT 'open',   -- open | acknowledged | resolved | expired
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    query_text     TEXT,
    details        JSONB,
    UNIQUE (workload_type, external_id)
);
CREATE INDEX IF NOT EXISTS findings_status_idx ON findings (status);
CREATE INDEX IF NOT EXISTS findings_owner_idx  ON findings (owner);
CREATE INDEX IF NOT EXISTS findings_lastseen_idx ON findings (last_seen DESC);

-- Triage cards on the Kanban board. One card per finding a triager works.
CREATE TABLE IF NOT EXISTS cards (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    finding_id     BIGINT      NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    assignee_id    BIGINT      REFERENCES it_members(id),
    status         TEXT        NOT NULL DEFAULT 'new',   -- new | investigating | assigned | resolved
    priority       TEXT        NOT NULL DEFAULT 'medium',-- low | medium | high
    notes          TEXT,
    sla_due        TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (finding_id)
);
CREATE INDEX IF NOT EXISTS cards_status_idx   ON cards (status);
CREATE INDEX IF NOT EXISTS cards_assignee_idx ON cards (assignee_id);

-- Audit log of every automation action attempted (email sends, card auto-creation).
CREATE TABLE IF NOT EXISTS action_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    finding_id   BIGINT      REFERENCES findings(id) ON DELETE SET NULL,
    rule_id      BIGINT      REFERENCES rules(id),
    action       TEXT        NOT NULL,     -- email | card
    target       TEXT,                     -- recipient email / member
    payload      JSONB,
    result       TEXT        NOT NULL DEFAULT 'pending', -- pending | sent | drafted | failed
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit of each poll cycle for observability of the poller itself.
CREATE TABLE IF NOT EXISTS poll_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    duration_ms    INTEGER,
    workloads_seen INTEGER     NOT NULL DEFAULT 0,
    seen_by_type   JSONB,                  -- {"query":1,"serving":47,...} live-workload mix
    findings_new   INTEGER     NOT NULL DEFAULT 0,
    findings_upd   INTEGER     NOT NULL DEFAULT 0,
    errors         TEXT
);
