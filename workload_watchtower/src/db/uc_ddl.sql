-- Workload Watchtower — Unity Catalog Delta (append-only history + reconciliation).
-- {uc_schema} is a placeholder for <catalog>.<schema>; setup/run_uc_ddl.py substitutes it
-- from your config and runs each statement on the configured SQL warehouse.

-- Immutable per-poll snapshot of every live workload observed (for trend charts).
CREATE TABLE IF NOT EXISTS {uc_schema}.workload_snapshots (
    poll_ts        TIMESTAMP,
    workload_type  STRING,        -- query | job_run | pipeline | cluster | serving
    external_id    STRING,
    owner          STRING,
    object_name    STRING,
    compute_ref    STRING,        -- warehouse_id / cluster_id / endpoint
    started_at     TIMESTAMP,
    elapsed_sec    DOUBLE,
    est_cost_usd   DOUBLE,        -- LIVE PROXY: elapsed_hrs * dbu_rate * list_price
    dbu_rate       DOUBLE,        -- assumed DBU/hr used for the estimate
    list_price     DOUBLE,        -- $/DBU from system.billing.list_prices
    severity       STRING,
    status         STRING
)
USING DELTA
COMMENT 'Append-only per-poll snapshot of live long-running/costly workloads.';

-- Immutable log of every alert raised (one row when a finding first crosses a rule).
CREATE TABLE IF NOT EXISTS {uc_schema}.alert_events (
    event_ts       TIMESTAMP,
    workload_type  STRING,
    external_id    STRING,
    owner          STRING,
    rule_name      STRING,
    metric         STRING,
    threshold      DOUBLE,
    observed       DOUBLE,
    severity       STRING,
    action_taken   STRING
)
USING DELTA
COMMENT 'Append-only log of alerts raised by the Watchtower poller.';

-- Cost reconciliation: LIVE PROXY estimate vs SETTLED actuals from system.billing.usage.
-- Joins snapshots (max estimate per workload) to settled usage once billing lands.
-- NOTE: warehouse/job/cluster attribution keys in system.billing.usage.usage_metadata
-- are validated during P3; this view is the scaffold for that reconciliation.
CREATE OR REPLACE VIEW {uc_schema}.cost_reconciliation AS
WITH est AS (
    SELECT
        workload_type,
        external_id,
        ANY_VALUE(owner)        AS owner,
        ANY_VALUE(object_name)  AS object_name,
        MAX(elapsed_sec)        AS max_elapsed_sec,
        MAX(est_cost_usd)       AS est_cost_usd,
        MIN(started_at)         AS started_at,
        MAX(poll_ts)            AS last_poll_ts
    FROM {uc_schema}.workload_snapshots
    GROUP BY workload_type, external_id
),
settled AS (
    SELECT
        usage_metadata.job_run_id            AS job_run_id,
        usage_metadata.warehouse_id          AS warehouse_id,
        usage_metadata.cluster_id            AS cluster_id,
        SUM(usage_quantity)                  AS dbus,
        MAX(usage_end_time)                  AS settled_through
    FROM system.billing.usage
    WHERE usage_date >= current_date() - INTERVAL 7 DAYS
    GROUP BY 1, 2, 3
)
SELECT
    e.workload_type,
    e.external_id,
    e.owner,
    e.object_name,
    e.started_at,
    e.max_elapsed_sec,
    e.est_cost_usd,
    e.last_poll_ts,
    s.dbus            AS settled_dbus,
    s.settled_through
FROM est e
LEFT JOIN settled s
  ON  (e.workload_type = 'job_run'  AND e.external_id = s.job_run_id)
   OR (e.workload_type = 'query'    AND e.external_id = s.warehouse_id)   -- refined in P3
   OR (e.workload_type = 'cluster'  AND e.external_id = s.cluster_id);
