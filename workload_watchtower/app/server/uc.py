"""
uc.py — read UC Delta history (trends) via the SQL warehouse.

Statement Execution is used (not Spark) so this runs identically local and in-app.
"""

from __future__ import annotations

import os
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql as dbsql

from .db import w  # reuse the shared WorkspaceClient

WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
# Fully-qualified <catalog>.<schema>.workload_snapshots — set in app.yaml from your config.
UC_SNAPSHOTS = os.environ["WT_UC_SNAPSHOTS"]
# Trends are backed by the SQL warehouse (slow + cold-start-prone) and the Dashboard polls them
# from every open tab, so cache briefly. They change at most once per poll (~5 min), so a short
# TTL is safely fresh while collapsing repeated warehouse round-trips.
_TRENDS_TTL_SEC = float(os.environ.get("WT_TRENDS_TTL_SEC", "90"))
_trends_cache: dict[int, tuple[float, dict]] = {}  # hours -> (expires_at, data)

# Per-user SQL cost (Budget view). Same proration model as src/poller/budget.py (settled SQL cost
# over the configured workspace(s) prorated by query-history execution share, SPs excluded) — keep
# the two in sync. {ws_in} is a safe IN-list built from the admin-configured (numeric) workspace ids.
# Cached briefly since the Budget view polls it and the underlying system tables change slowly.
_USER_COST_TTL_SEC = float(os.environ.get("WT_USER_COST_TTL_SEC", "120"))
_user_cost_cache: dict[tuple, tuple[float, list]] = {}
_USER_COST_SQL = """
WITH billing_base AS (
  SELECT u.usage_date, u.usage_quantity, lp.pricing.effective_list.default AS list_price
  FROM system.billing.usage u
  JOIN system.billing.list_prices lp ON lp.sku_name = u.sku_name
   AND u.usage_end_time >= lp.price_start_time
   AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
  WHERE u.billing_origin_product = 'SQL' AND u.workspace_id IN {ws_in}
),
query_base AS (
  SELECT executed_by, total_duration_ms, start_time
  FROM system.query.history
  WHERE workspace_id IN {ws_in} AND executed_by IS NOT NULL
    AND executed_by NOT RLIKE '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
),
sql_billing AS (
  SELECT SUM(usage_quantity) AS total_dbus, SUM(usage_quantity * list_price) AS total_list_cost_usd
  FROM billing_base
  WHERE usage_date >= (SELECT MAX(usage_date) FROM billing_base) - INTERVAL {hours} HOURS
),
user_execution AS (
  SELECT executed_by, SUM(total_duration_ms) AS user_duration_ms, COUNT(*) AS query_count
  FROM query_base
  WHERE start_time >= (SELECT MAX(CAST(start_time AS DATE)) FROM query_base) - INTERVAL {hours} HOURS
  GROUP BY executed_by
)
SELECT ue.executed_by AS user_identity, ue.query_count,
  ROUND(ue.user_duration_ms / 1000.0 / 60, 2) AS total_execution_min,
  ROUND(ue.user_duration_ms * 100.0 / SUM(ue.user_duration_ms) OVER (), 2) AS pct_of_total_execution,
  ROUND(sb.total_dbus * ue.user_duration_ms / SUM(ue.user_duration_ms) OVER (), 4) AS estimated_dbus,
  ROUND(sb.total_list_cost_usd * ue.user_duration_ms / SUM(ue.user_duration_ms) OVER (), 2) AS estimated_list_cost_usd
FROM user_execution ue CROSS JOIN sql_billing sb
ORDER BY estimated_list_cost_usd DESC
"""


def _ws_in(workspace_ids: str) -> str:
    """Safe `('id', ...)` IN-list from comma-separated numeric workspace ids (guards injection)."""
    ids = [t.strip() for t in (workspace_ids or "").split(",") if t.strip().isdigit()]
    if not ids:
        raise RuntimeError("no valid (numeric) workspace ids configured")
    return "(" + ", ".join(f"'{i}'" for i in ids) + ")"


def query(sql: str) -> list[dict]:
    # CANCEL (not the CONTINUE default) so a query that outruns wait_timeout is cancelled and we
    # can raise — the CONTINUE default returns no result on a slow/cold warehouse, which would
    # silently render an empty trends chart instead of surfacing the problem.
    resp = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout="30s",
        on_wait_timeout=dbsql.ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"trends query did not succeed ({state}): {err}")
    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in resp.result.data_array]


def _num(x, cast=float):
    """Statement Execution returns every value as a STRING — coerce numerics so the
    frontend never gets a string where it expects a number (e.g. .toFixed)."""
    if x is None:
        return None
    try:
        return cast(x)
    except (TypeError, ValueError):
        return None


def user_costs(hours: int = 24, workspace_ids: str = "") -> list[dict]:
    """Per-user SQL cost over the window for the given workspace(s) (see _USER_COST_SQL). Cached
    briefly (keyed on hours + workspace set). Numerics coerced."""
    key = (int(hours), workspace_ids or "")
    now = time.time()
    cached = _user_cost_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    resp = w.statement_execution.execute_statement(
        statement=_USER_COST_SQL.format(ws_in=_ws_in(workspace_ids), hours=int(hours)),
        warehouse_id=WAREHOUSE_ID, wait_timeout="50s",
        on_wait_timeout=dbsql.ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"user-cost query did not succeed ({state}): {err}")
    rows = []
    if resp.result and resp.result.data_array:
        cols = [c.name for c in resp.manifest.schema.columns]
        for row in resp.result.data_array:
            d = dict(zip(cols, row))
            d["estimated_list_cost_usd"] = _num(d.get("estimated_list_cost_usd"))
            d["estimated_dbus"] = _num(d.get("estimated_dbus"))
            d["total_execution_min"] = _num(d.get("total_execution_min"))
            d["query_count"] = _num(d.get("query_count"), int)
            rows.append(d)
    _user_cost_cache[key] = (now + _USER_COST_TTL_SEC, rows)
    return rows


def trends(hours: int = 24) -> dict:
    """Cost + count trend of flagged workloads over the last `hours`. Cached (short TTL) so a slow
    warehouse query isn't re-run on every Dashboard poll."""
    now = time.time()
    cached = _trends_cache.get(hours)
    if cached and cached[0] > now:
        return cached[1]
    data = _compute_trends(hours)
    _trends_cache[hours] = (now + _TRENDS_TTL_SEC, data)
    return data


def _compute_trends(hours: int = 24) -> dict:
    by_type = query(
        f"""SELECT workload_type,
                   count(DISTINCT external_id) AS workloads,
                   round(sum(est_cost_usd), 2)  AS est_cost_usd,
                   round(max(elapsed_sec)/60, 1) AS max_elapsed_min
            FROM {UC_SNAPSHOTS}
            WHERE poll_ts >= current_timestamp() - INTERVAL {int(hours)} HOURS
            GROUP BY workload_type ORDER BY est_cost_usd DESC"""
    )
    timeline = query(
        f"""SELECT date_trunc('HOUR', poll_ts) AS hour,
                   count(DISTINCT external_id) AS workloads,
                   round(sum(est_cost_usd), 2)  AS est_cost_usd
            FROM {UC_SNAPSHOTS}
            WHERE poll_ts >= current_timestamp() - INTERVAL {int(hours)} HOURS
            GROUP BY 1 ORDER BY 1"""
    )
    for r in by_type:
        r["workloads"] = _num(r.get("workloads"), int)
        r["est_cost_usd"] = _num(r.get("est_cost_usd"))
        r["max_elapsed_min"] = _num(r.get("max_elapsed_min"))
    for r in timeline:
        r["workloads"] = _num(r.get("workloads"), int)
        r["est_cost_usd"] = _num(r.get("est_cost_usd"))
    return {"by_type": by_type, "timeline": timeline}
