"""
main.py — Workload Watchtower poller.

One poll cycle:
  1. load enabled rules from Lakebase,
  2. collect live workloads (queries/jobs/pipelines/clusters/serving),
  3. evaluate each against its rules (elapsed_sec / est_cost_usd thresholds),
  4. upsert findings in Lakebase; on a NEW finding auto-create a triage card and
     draft the configured email action; append an alert to UC Delta,
  5. snapshot every finding to UC Delta (trend + reconciliation source),
  6. record the poll run.

Runs identically locally (as a user) and in the Lakeflow Job (as the poller SP).
UC Delta writes go through the SQL warehouse so no Spark session is required.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from psycopg.types.json import Json

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Serverless spark_python_task exec's the entrypoint without __file__ defined,
    # so anchor on a sibling module across likely roots (cwd, sys.path, deployed bundle path).
    # The bundle files path is identity/target-specific, so discover it by glob rather than
    # hardcoding a user — override with WT_BUNDLE_FILES_PATH if the search ever misses.
    import glob
    HERE = None
    _bases = [os.getcwd(), *sys.path]
    if os.environ.get("WT_BUNDLE_FILES_PATH"):
        _bases.insert(1, os.environ["WT_BUNDLE_FILES_PATH"])
    _bases += glob.glob("/Workspace/Users/*/.bundle/*/*/files")
    for _base in _bases:
        for _rel in ("src/poller", "poller", "."):
            if os.path.exists(os.path.join(_base, _rel, "collectors.py")):
                HERE = os.path.abspath(os.path.join(_base, _rel))
                break
        if HERE:
            break
    if HERE is None:
        raise RuntimeError("cannot locate poller module directory")
SRC = os.path.dirname(HERE)
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _config_from_args() -> None:
    """Serverless jobs can't set arbitrary env vars, so the bundle passes deployment config
    as `--key=value` task parameters (interpolated from bundle variables). Translate them into
    the env vars the sibling modules (lakebase/uc/mailer) read — but ONLY when not already set,
    so a real environment (e.g. local dev via config.env) always wins. Must run before the
    sibling imports below, which read their env at import time."""
    args = {}
    for a in sys.argv[1:]:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            if v:
                args[k] = v

    def setdefault(env_key: str, val: str | None):
        if val and not os.environ.get(env_key):
            os.environ[env_key] = val

    setdefault("WT_WAREHOUSE_ID", args.get("warehouse-id"))
    setdefault("LAKEBASE_ENDPOINT", args.get("lakebase-endpoint"))
    setdefault("LAKEBASE_HOST", args.get("lakebase-host"))
    setdefault("LAKEBASE_SCHEMA", args.get("lakebase-schema"))
    setdefault("WT_SMTP_SCOPE", args.get("secret-scope"))
    # UC snapshot/alert tables derive from a single <catalog>.<schema> for convenience.
    uc = args.get("uc-schema")
    if uc:
        setdefault("WT_UC_SNAPSHOTS", f"{uc}.workload_snapshots")
        setdefault("WT_UC_ALERTS", f"{uc}.alert_events")


_config_from_args()

from db import lakebase          # noqa: E402
import cost                      # noqa: E402
import collectors                # noqa: E402
import mailer                    # noqa: E402

_TIMEOUT_DOC = "https://docs.databricks.com/aws/en/sql/language-manual/parameters/statement_timeout"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("poller")

# All required — set on the poller job (see databricks.yml / config.env). No hardcoded defaults.
WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
UC_SNAPSHOTS = os.environ["WT_UC_SNAPSHOTS"]   # <catalog>.<schema>.workload_snapshots
UC_ALERTS = os.environ["WT_UC_ALERTS"]         # <catalog>.<schema>.alert_events

_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


# ── UC Delta writes via the SQL warehouse ────────────────────────────────────
def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, datetime):
        return "TIMESTAMP '%s'" % v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # Spark SQL treats backslash as an escape char in string literals — double it, then quotes
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def _list_price(w: WorkspaceClient) -> float:
    """Live $/DBU for serverless SQL from system.billing.list_prices (replaces the
    hardcoded default). Falls back to cost.PRICE_PER_DBU if unavailable."""
    try:
        resp = w.statement_execution.execute_statement(
            statement="SELECT pricing.`default` AS p FROM system.billing.list_prices "
                      "WHERE sku_name ILIKE '%SQL%' AND sku_name ILIKE '%SERVERLESS%' "
                      "AND price_end_time IS NULL ORDER BY price_start_time DESC LIMIT 1",
            warehouse_id=WAREHOUSE_ID, wait_timeout="30s")
        if resp.result and resp.result.data_array:
            return float(resp.result.data_array[0][0])
    except Exception as exc:
        log.warning("list_price lookup failed, using default: %s", exc)
    return cost.PRICE_PER_DBU


def _uc_insert(w: WorkspaceClient, table: str, cols: list[str], rows: list[list]) -> None:
    if not rows:
        return
    values = ",".join("(" + ",".join(_lit(v) for v in row) + ")" for row in rows)
    stmt = f"INSERT INTO {table} ({','.join(cols)}) VALUES {values}"
    resp = w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=WAREHOUSE_ID, wait_timeout="30s"
    )
    state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
    if state not in ("SUCCEEDED",):
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"UC insert into {table} failed: {err}")


# ── rule evaluation ──────────────────────────────────────────────────────────
_VIOLATION = {
    "elapsed_sec": "LONG_RUNNING",
    "est_cost_usd": "COST_BURST",
    "session_override": "STATEMENT_TIMEOUT_OVERRIDE",
}


def _metric_value(wl: dict, metric: str):
    if metric == "elapsed_sec":
        return wl.get("elapsed_sec")
    if metric == "est_cost_usd":
        return wl.get("est_cost_usd")
    return None


def _fires(wl: dict, rule: dict) -> bool:
    """Whether a rule fires for a workload."""
    metric = rule["metric"]
    if metric == "session_override":
        # Governance: any session-level SET STATEMENT_TIMEOUT (overriding the workspace/warehouse
        # guardrail, since session scope wins). Presence-based — fires on any such statement.
        return wl["workload_type"] == "timeout_override"
    val = _metric_value(wl, metric)
    return val is not None and val >= rule["threshold"]


def _health(severity: str) -> str:
    return {"critical": "CRITICAL", "warning": "WARNING", "info": "INFO"}.get(severity, "INFO")


def _priority(severity: str, elapsed_sec, est_cost, violations: set[str]) -> int:
    """0-100 triage sort score: severity base + cost/elapsed factors + no-timeout bump."""
    score = {"critical": 60, "warning": 35, "info": 15}.get(severity, 15)
    score += min(25, (est_cost or 0) / 2.0)        # $50 est -> +25
    score += min(15, (elapsed_sec or 0) / 600.0)   # +1 per 10 min, capped +15
    if "STATEMENT_TIMEOUT_OVERRIDE" in violations:
        score += 10
    return int(min(100, round(score)))


def _evaluate(wl: dict, rules: list[dict]) -> dict | None:
    """Return match info if any rule for this workload type fires, else None."""
    matched = [r for r in rules if _fires(wl, r)]
    if not matched:
        return None
    top = max(matched, key=lambda r: (_SEV_RANK.get(r["severity"], 0), r["threshold"]))
    actions: set[str] = set()
    for r in matched:
        actions.update(r["action"].split("_"))  # card_email -> {card, email}
    violations = {_VIOLATION.get(r["metric"], r["metric"].upper()) for r in matched}
    health = _health(top["severity"])
    priority = _priority(top["severity"], wl.get("elapsed_sec"), wl.get("est_cost_usd"), violations)
    return {
        "rule": top, "actions": actions, "count": len(matched),
        "violation_reason": "|".join(sorted(violations)),
        "health_status": health, "alert_priority": priority,
    }


# ── one poll cycle ───────────────────────────────────────────────────────────
def poll(w: WorkspaceClient) -> dict:
    t0 = time.time()
    errors: list[str] = []

    # 1. rules + roster (Lakebase)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, workload_type, metric, threshold, severity, action "
                    "FROM rules WHERE enabled = TRUE")
        rules_by_type: dict[str, list[dict]] = {}
        for rid, name, wt, metric, thr, sev, action in cur.fetchall():
            rules_by_type.setdefault(wt, []).append(
                {"id": rid, "name": name, "metric": metric, "threshold": float(thr),
                 "severity": sev, "action": action})
        cur.execute("SELECT id FROM it_members WHERE active AND role <> 'admin' ORDER BY id")
        roster = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT email FROM subscribers WHERE active ORDER BY email")
        subscribers = [r[0] for r in cur.fetchall()]
    smtp_cfg = mailer.load_config(w)   # read SMTP config once per poll (None if unset)

    # 2. collect
    wh_cache: dict = {}
    workloads: list[dict] = []
    for wtype, fn in collectors.COLLECTORS.items():
        try:
            got = fn(w, wh_cache) if wtype == "query" else fn(w)
            workloads.extend(got)
        except Exception as exc:  # isolate a failing collector
            errors.append(f"{wtype}: {exc}")
            log.warning("collector %s failed: %s", wtype, exc)

    # 3. cost proxy (live list price) + evaluate; tally the live-workload mix by type
    price = _list_price(w)
    seen_by_type: dict[str, int] = {}
    findings = []
    for wl in workloads:
        seen_by_type[wl["workload_type"]] = seen_by_type.get(wl["workload_type"], 0) + 1
        rate = cost.dbu_per_hr(wl["workload_type"], wl.get("dbu_meta"))
        wl["est_cost_usd"] = cost.estimate(wl.get("elapsed_sec"), rate, price)
        wl["_dbu_rate"] = rate
        m = _evaluate(wl, rules_by_type.get(wl["workload_type"], []))
        if m:
            wl["_match"] = m
            findings.append(wl)

    # 4/5. persist findings + cards + alerts + snapshots
    new_ct = upd_ct = 0
    snap_rows, alert_rows, pending_sends = [], [], []
    poll_ts = datetime.now(timezone.utc)
    rr = 0  # round-robin assignee index
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        for wl in findings:
            m = wl["_match"]
            cur.execute(
                """INSERT INTO findings
                   (workload_type, external_id, owner, object_name, compute_ref, started_at,
                    elapsed_sec, est_cost_usd, severity, health_status, alert_priority,
                    violation_reason, matched_rule, query_text, details, status, last_seen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',now())
                   ON CONFLICT (workload_type, external_id) DO UPDATE SET
                     owner=EXCLUDED.owner, object_name=EXCLUDED.object_name,
                     compute_ref=EXCLUDED.compute_ref, started_at=EXCLUDED.started_at,
                     elapsed_sec=EXCLUDED.elapsed_sec, est_cost_usd=EXCLUDED.est_cost_usd,
                     severity=EXCLUDED.severity, health_status=EXCLUDED.health_status,
                     alert_priority=EXCLUDED.alert_priority, violation_reason=EXCLUDED.violation_reason,
                     matched_rule=EXCLUDED.matched_rule,
                     query_text=EXCLUDED.query_text, details=EXCLUDED.details,
                     status=CASE WHEN findings.status='resolved' THEN 'open' ELSE findings.status END,
                     last_seen=now()
                   RETURNING id, (xmax = 0) AS inserted""",
                (wl["workload_type"], wl["external_id"], wl.get("owner"), wl.get("object_name"),
                 wl.get("compute_ref"), wl.get("started_at"), wl.get("elapsed_sec"),
                 wl["est_cost_usd"], m["rule"]["severity"], m["health_status"],
                 m["alert_priority"], m["violation_reason"], m["rule"]["id"],
                 wl.get("query_text"), Json(wl.get("details") or {})),
            )
            fid, inserted = cur.fetchone()
            if inserted:
                new_ct += 1
                # auto-create triage card (round-robin assignee) if action includes 'card'
                if "card" in m["actions"]:
                    assignee = roster[rr % len(roster)] if roster else None
                    rr += 1
                    priority = "high" if m["rule"]["severity"] == "critical" else "medium"
                    cur.execute(
                        "INSERT INTO cards (finding_id, assignee_id, status, priority) "
                        "VALUES (%s,%s,'new',%s) ON CONFLICT (finding_id) DO NOTHING",
                        (fid, assignee, priority))
                # email action: draft the action now; CRITICAL findings are sent AFTER this
                # DB transaction closes (network I/O must not be held inside it).
                if "email" in m["actions"]:
                    recipients = subscribers or ([wl["owner"]] if wl.get("owner") else [])
                    payload = {"object": wl.get("object_name"),
                               "elapsed_sec": wl.get("elapsed_sec"),
                               "est_cost_usd": wl["est_cost_usd"],
                               "violation_reason": m["violation_reason"]}
                    target = ", ".join(recipients) or wl.get("owner")
                    cur.execute(
                        "INSERT INTO action_log (finding_id, rule_id, action, target, payload, result) "
                        "VALUES (%s,%s,'email',%s,%s,'drafted') RETURNING id",
                        (fid, m["rule"]["id"], target, Json(payload)))
                    aid = cur.fetchone()[0]
                    if m["rule"]["severity"] == "critical" and smtp_cfg and recipients:
                        subject = f"[Workload Watchtower] CRITICAL: {wl.get('object_name') or 'flagged workload'}"
                        body = (f"Workload Watchtower auto-alert.\n\nOwner: {wl.get('owner')}\n"
                                f"Details:\n{json.dumps(payload, indent=2, default=str)}\n")
                        if "STATEMENT_TIMEOUT_OVERRIDE" in m["violation_reason"]:
                            body += (f"\nA session-level STATEMENT_TIMEOUT override was detected — this "
                                     f"bypasses the workspace/warehouse guardrail (session scope wins). "
                                     f"Review: {_TIMEOUT_DOC}\n")
                        pending_sends.append((aid, recipients, subject, body))
                alert_rows.append([poll_ts, wl["workload_type"], wl["external_id"], wl.get("owner"),
                                   m["rule"]["name"], m["rule"]["metric"], m["rule"]["threshold"],
                                   _metric_value(wl, m["rule"]["metric"]), m["rule"]["severity"],
                                   "|".join(sorted(m["actions"]))])
            else:
                upd_ct += 1
            snap_rows.append([poll_ts, wl["workload_type"], wl["external_id"], wl.get("owner"),
                              wl.get("object_name"), wl.get("compute_ref"), wl.get("started_at"),
                              wl.get("elapsed_sec"), wl["est_cost_usd"], wl["_dbu_rate"],
                              price, m["rule"]["severity"], "open"])

    # UC Delta appends (via warehouse)
    try:
        _uc_insert(w, UC_SNAPSHOTS,
                   ["poll_ts", "workload_type", "external_id", "owner", "object_name",
                    "compute_ref", "started_at", "elapsed_sec", "est_cost_usd", "dbu_rate",
                    "list_price", "severity", "status"], snap_rows)
        _uc_insert(w, UC_ALERTS,
                   ["event_ts", "workload_type", "external_id", "owner", "rule_name", "metric",
                    "threshold", "observed", "severity", "action_taken"], alert_rows)
    except Exception as exc:
        errors.append(f"uc_delta: {exc}")
        log.warning("UC Delta write failed: %s", exc)

    # 5b. auto-send critical emails OUTSIDE the DB transaction (blocking network I/O),
    # then record results in one short connection.
    if pending_sends:
        results = []
        for aid, recipients, subject, body in pending_sends:
            ok, detail = mailer.send(smtp_cfg, recipients, subject, body)
            results.append((aid, "sent" if ok else "failed", None if ok else detail))
            if not ok:
                errors.append(f"email {aid}: {detail}")
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            for aid, res, err in results:
                cur.execute("UPDATE action_log SET result = %s, error = %s WHERE id = %s", (res, err, aid))

    # 6. record poll run
    dur_ms = int((time.time() - t0) * 1000)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO poll_runs (finished_at, duration_ms, workloads_seen, seen_by_type, "
            "findings_new, findings_upd, errors) VALUES (now(),%s,%s,%s,%s,%s,%s)",
            (dur_ms, len(workloads), Json(seen_by_type), new_ct, upd_ct, "; ".join(errors) or None))

    summary = {"workloads_seen": len(workloads), "seen_by_type": seen_by_type,
               "findings": len(findings), "new": new_ct, "updated": upd_ct,
               "errors": errors, "duration_ms": dur_ms, "list_price": price}
    log.info("poll complete: %s", summary)
    return summary


def main() -> None:
    w = WorkspaceClient()
    try:
        lakebase.bootstrap_schema(w)   # first-run convenience; no-op once the schema exists
    except Exception as exc:
        # A non-owner run identity (e.g. the app SP) can't (re)create indexes on tables it
        # doesn't own — that's expected; the schema is created once by the owner. Poll anyway.
        log.info("bootstrap_schema skipped (already exists / not owner): %s", exc)
    poll(w)


if __name__ == "__main__":
    main()
