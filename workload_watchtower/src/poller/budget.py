"""
budget.py — per-user SQL-cost budget scan (Feature 3).

Prorates the workspace's SETTLED SQL cost (system.billing.usage × list_prices) over the last
window across users by their share of system.query.history execution time — real dollars,
attributed per user, service principals excluded. Users over the configured $/user budget get an
email (the admin list, plus the over-budget user themselves when notify_users is on).

Runs INSIDE the poll job but self-gates to an hourly cadence (budget_config.scan_every_min via
last_scan_at) and dedupes per user off the budget_alerts audit trail (no re-email within the
window). Reads cost via the SQL warehouse (Statement Execution), like uc.py.
"""

from __future__ import annotations

import logging
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql as dbsql

from db import lakebase
import mailer

log = logging.getLogger("poller")

WAREHOUSE_ID = os.environ.get("WT_WAREHOUSE_ID", "8d87b02f99f584f2")

# Per-user cost = settled SQL cost (over the configured workspace(s)) prorated by each user's
# query-history execution share. SPs excluded via the UUID-shaped executed_by filter. Window is the
# latest `hours` present in each source table (both lag independently, so each keys off its own max).
# {ws_in} is a safe IN-list built from the admin-configured (numeric) workspace ids.
_SCAN_SQL = """
WITH billing_base AS (
  SELECT u.usage_date, u.usage_quantity, lp.pricing.effective_list.default AS list_price
  FROM system.billing.usage u
  JOIN system.billing.list_prices lp
    ON lp.sku_name = u.sku_name
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
SELECT
  ue.executed_by AS user_identity, ue.query_count,
  ROUND(ue.user_duration_ms / 1000.0 / 60, 2) AS total_execution_min,
  ROUND(ue.user_duration_ms * 100.0 / SUM(ue.user_duration_ms) OVER (), 2) AS pct_of_total_execution,
  ROUND(sb.total_dbus * ue.user_duration_ms / SUM(ue.user_duration_ms) OVER (), 4) AS estimated_dbus,
  ROUND(sb.total_list_cost_usd * ue.user_duration_ms / SUM(ue.user_duration_ms) OVER (), 2) AS estimated_list_cost_usd
FROM user_execution ue CROSS JOIN sql_billing sb
ORDER BY estimated_list_cost_usd DESC
"""


def ws_in_clause(workspace_ids: str) -> str:
    """Build a safe `('id', ...)` IN-list from a comma-separated string. Workspace ids are numeric;
    non-digit tokens are dropped, which also guards against SQL injection via the config field."""
    ids = [t.strip() for t in (workspace_ids or "").split(",") if t.strip().isdigit()]
    if not ids:
        raise ValueError("no valid (numeric) workspace ids configured for the budget scan")
    return "(" + ", ".join(f"'{i}'" for i in ids) + ")"


def scan_user_costs(w: WorkspaceClient, hours: int, workspace_ids: str) -> list[dict]:
    """Run the proration query and return per-user cost rows (numbers coerced from strings)."""
    resp = w.statement_execution.execute_statement(
        statement=_SCAN_SQL.format(ws_in=ws_in_clause(workspace_ids), hours=int(hours)),
        warehouse_id=WAREHOUSE_ID, wait_timeout="50s",
        on_wait_timeout=dbsql.ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"budget scan query did not succeed ({state}): {err}")
    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    out = []
    for row in resp.result.data_array:
        d = dict(zip(cols, row))
        for k in ("estimated_list_cost_usd", "estimated_dbus", "total_execution_min"):
            d[k] = float(d[k]) if d.get(k) not in (None, "") else 0.0
        d["query_count"] = int(d["query_count"]) if d.get("query_count") not in (None, "") else 0
        out.append(d)
    return out


def _config(cur) -> dict | None:
    cur.execute("SELECT user_budget_usd, window_hours, scan_every_min, workspace_ids, enabled, "
                "notify_users, last_scan_at FROM budget_config WHERE id = TRUE")
    row = cur.fetchone()
    if not row:
        return None
    keys = ["user_budget_usd", "window_hours", "scan_every_min", "workspace_ids", "enabled",
            "notify_users", "last_scan_at"]
    return dict(zip(keys, row))


def run_budget_scan(w: WorkspaceClient, force: bool = False) -> dict:
    """One budget cycle: cadence-gated unless force=True. Returns a summary dict."""
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cfg = _config(cur)
        if not cfg or not cfg["enabled"]:
            return {"skipped": "disabled"}
        prior = cfg["last_scan_at"]
        # Atomic claim: the cadence gate and the slot claim are ONE conditional UPDATE, so two
        # overlapping polls can't both pass — exactly one advances last_scan_at and proceeds; the
        # loser gets no row back and skips (no double scan / double emails). force bypasses cadence.
        if force:
            cur.execute("UPDATE budget_config SET last_scan_at = now(), updated_at = now() "
                        "WHERE id = TRUE RETURNING id")
        else:
            cur.execute("UPDATE budget_config SET last_scan_at = now(), updated_at = now() "
                        "WHERE id = TRUE AND (last_scan_at IS NULL "
                        "OR last_scan_at <= now() - make_interval(mins => %s)) RETURNING id",
                        (cfg["scan_every_min"],))
        if cur.fetchone() is None:
            return {"skipped": "cadence"}

    budget = float(cfg["user_budget_usd"])
    hours = int(cfg["window_hours"])
    try:
        rows = scan_user_costs(w, hours, cfg["workspace_ids"])
    except Exception as exc:
        # We claimed the slot BEFORE scanning (to block concurrent runs); a transient scan failure
        # must not then suppress the whole next window, so roll the cadence clock back to `prior`.
        log.warning("budget scan query failed, restoring last_scan_at: %s", exc)
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            cur.execute("UPDATE budget_config SET last_scan_at = %s WHERE id = TRUE", (prior,))
        return {"error": str(exc)}
    over = [r for r in rows if r["estimated_list_cost_usd"] >= budget]
    log.info("budget scan: %d users, %d over $%.2f", len(rows), len(over), budget)
    if not over:
        return {"users": len(rows), "over": 0}

    smtp_cfg = mailer.load_config(w)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM subscribers WHERE active ORDER BY email")
        admins = [r[0] for r in cur.fetchall()]

    sent = skipped = failed = 0
    for u in over:
        user = u["user_identity"]
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            # Dedup off the audit trail: no re-alert if a 'sent' row exists within the window.
            cur.execute(
                "SELECT 1 FROM budget_alerts WHERE user_identity = %s AND result = 'sent' "
                "AND alerted_at > now() - make_interval(hours => %s) LIMIT 1", (user, hours))
            if cur.fetchone():
                skipped += 1
                continue
        recipients = list(admins)
        if cfg["notify_users"]:
            recipients.append(user)
        recipients = sorted({r for r in recipients if r})
        subject = f"[Workload Watchtower] Budget exceeded: {user} at ${u['estimated_list_cost_usd']:.2f}"
        body = (
            f"User {user} has exceeded the configured SQL budget over the last {hours}h.\n\n"
            f"  Estimated SQL cost: ${u['estimated_list_cost_usd']:.2f}\n"
            f"  Budget:             ${budget:.2f}\n"
            f"  Queries:            {u['query_count']}\n"
            f"  Execution time:     {u['total_execution_min']} min\n"
            f"  Estimated DBUs:     {u['estimated_dbus']}\n\n"
            f"Cost is settled workspace SQL spend prorated by this user's query-history execution "
            f"share (an estimate). — Workload Watchtower")
        if smtp_cfg and recipients:
            ok, detail = mailer.send(smtp_cfg, recipients, subject, body)
        else:
            ok, detail = False, "SMTP not configured" if not smtp_cfg else "no recipients"
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO budget_alerts (user_identity, est_cost_usd, budget_usd, window_hours, "
                "recipients, result, error) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (user, u["estimated_list_cost_usd"], budget, hours, ", ".join(recipients),
                 "sent" if ok else "failed", None if ok else detail))
        sent += int(ok)
        failed += int(not ok)
    return {"users": len(rows), "over": len(over), "sent": sent, "skipped": skipped, "failed": failed}
