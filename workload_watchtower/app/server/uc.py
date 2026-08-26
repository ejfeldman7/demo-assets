"""
uc.py — read UC Delta history (trends) via the SQL warehouse.

Statement Execution is used (not Spark) so this runs identically local and in-app.
"""

from __future__ import annotations

import os
import time

from databricks.sdk import WorkspaceClient

from .db import w  # reuse the shared WorkspaceClient

WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
# Fully-qualified <catalog>.<schema>.workload_snapshots — set in app.yaml from your config.
UC_SNAPSHOTS = os.environ["WT_UC_SNAPSHOTS"]
# Trends are backed by the SQL warehouse (slow + cold-start-prone) and the Dashboard polls them
# from every open tab, so cache briefly. They change at most once per poll (~5 min), so a short
# TTL is safely fresh while collapsing repeated warehouse round-trips.
_TRENDS_TTL_SEC = float(os.environ.get("WT_TRENDS_TTL_SEC", "90"))
_trends_cache: dict[int, tuple[float, dict]] = {}  # hours -> (expires_at, data)


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
