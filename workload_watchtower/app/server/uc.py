"""
uc.py — read UC Delta history (trends) via the SQL warehouse.

Statement Execution is used (not Spark) so this runs identically local and in-app.
"""

from __future__ import annotations

import os

from databricks.sdk import WorkspaceClient

from .db import w  # reuse the shared WorkspaceClient

WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
# Fully-qualified <catalog>.<schema>.workload_snapshots — set in app.yaml from your config.
UC_SNAPSHOTS = os.environ["WT_UC_SNAPSHOTS"]


def query(sql: str) -> list[dict]:
    resp = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout="30s"
    )
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


def trends(hours: int = 24) -> dict:
    """Cost + count trend of flagged workloads over the last `hours`, by type."""
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
