"""
routes.py — Watchtower REST API over Lakebase (triage state) + UC (trends) + Jobs.
"""

from __future__ import annotations

import json
import os

from databricks.sdk.service import sql
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import llm, mailer, uc
from .db import pool, rows_to_dicts, w

# Customer-available operator metrics we surface to the copilot (from Query History).
_METRIC_KEYS = [
    "compilation_time_ms", "execution_time_ms", "task_total_time_ms", "photon_total_time_ms",
    "read_bytes", "read_files_count", "pruned_files_count", "read_partitions_count",
    "rows_read_count", "rows_produced_count", "spill_to_disk_bytes", "read_cache_bytes",
    "read_remote_bytes", "result_from_cache",
]

# Condensed performance-tuning playbook (the "4 S's" + a customer-applicable fix catalog) so the
# copilot diagnoses like an SA and prescribes real Databricks fixes, not generic advice.
_PLAYBOOK = (
    "Diagnose Databricks workload performance with the '4 S's': SKEW (uneven task times), "
    "SPILL (any spill_to_disk_bytes>0 is a problem), SHUFFLE (excess data movement), SMALL FILES / "
    "poor pruning (high read_files_count with pruned_files_count~0, or rows_read_count >> "
    "rows_produced_count = weak selectivity). Also weigh compilation_time_ms vs execution_time_ms "
    "(high compile = plan complexity), photon_total_time_ms vs task_total_time_ms (low Photon share "
    "= unsupported ops/UDFs), and result_from_cache. Prescribe specific, customer-applicable fixes "
    "ranked by impact, chosen from: set a STATEMENT_TIMEOUT guardrail; add filters / partition "
    "pruning; Liquid Clustering or OPTIMIZE for small files; broadcast the small side or salt for "
    "skew; raise shuffle partitions to auto or executor memory for spill; replace UDFs with built-ins "
    "to keep Photon; right-size the warehouse; enforce a STATEMENT_TIMEOUT guardrail at the "
    "workspace/warehouse scope (note: a session-level SET overrides it, so the cap must be set by an "
    "admin, not left to users). Cite the metric that drives each fix. Recommend only — a human applies changes."
)


def _query_metrics(query_id: str) -> dict | None:
    """Curated operator metrics for a (usually completed) query, from Query History."""
    try:
        resp = w.query_history.list(
            filter_by=sql.QueryFilter(statement_ids=[query_id]), include_metrics=True, max_results=1)
        q = (resp.res or [None])[0]
        if not q or not q.metrics:
            return None
        m = q.metrics.as_dict()
        return {k: m[k] for k in _METRIC_KEYS if k in m}
    except Exception:
        return None

_TIMEOUT_DOC = "https://docs.databricks.com/aws/en/sql/language-manual/parameters/statement_timeout"

# severity is stored as TEXT; rank it numerically so ORDER BY is by severity, not alphabet
_SEV_RANK = "(CASE f.severity WHEN 'critical' THEN 3 WHEN 'warning' THEN 2 WHEN 'info' THEN 1 ELSE 0 END)"

router = APIRouter()


@router.get("/config")
def config():
    """Lightweight status for the UI (email wiring, monitoring dashboard URL, workspace label)."""
    return {"smtp_configured": mailer.smtp_configured(),
            "dashboard_url": os.environ.get("WT_DASHBOARD_URL"),
            "dashboard_embed_url": os.environ.get("WT_DASHBOARD_EMBED_URL"),
            "workspace": os.environ.get("WT_WORKSPACE_LABEL")}


# ── dashboard summary ────────────────────────────────────────────────────────
@router.get("/summary")
def summary():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT severity, count(*) FROM findings WHERE status <> 'resolved' GROUP BY severity")
        by_sev = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT status, count(*) FROM cards GROUP BY status")
        cards = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            "SELECT workload_type, count(*) FROM findings WHERE status <> 'resolved' GROUP BY workload_type")
        by_type = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            "SELECT round(coalesce(sum(est_cost_usd),0)::numeric,2) FROM findings WHERE status <> 'resolved'")
        open_cost = cur.fetchone()[0]
        cur.execute(
            "SELECT finished_at, workloads_seen, findings_new, duration_ms, seen_by_type "
            "FROM poll_runs ORDER BY id DESC LIMIT 1")
        last = cur.fetchone()
    return {
        "open_by_severity": by_sev,
        "open_by_type": by_type,
        "cards_by_status": cards,
        "open_est_cost_usd": float(open_cost or 0),
        "last_poll": (
            {"finished_at": last[0].isoformat() if last and last[0] else None,
             "workloads_seen": last[1] if last else None,
             "findings_new": last[2] if last else None,
             "duration_ms": last[3] if last else None,
             "seen_by_type": last[4] if last else None}
        ),
    }


# ── findings ─────────────────────────────────────────────────────────────────
@router.get("/findings")
def list_findings(status: str | None = None, limit: int = 200):
    sql = ("SELECT f.*, r.name AS rule_name FROM findings f "
           "LEFT JOIN rules r ON f.matched_rule = r.id")
    params: list = []
    if status:
        sql += " WHERE f.status = %s"
        params.append(status)
    sql += f" ORDER BY {_SEV_RANK} DESC, f.elapsed_sec DESC NULLS LAST LIMIT %s"
    params.append(limit)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return rows_to_dicts(cur)


# ── agentic: Triage Copilot (explain + recommend) ────────────────────────────
@router.post("/findings/{finding_id}/explain")
def explain_finding(finding_id: int):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT workload_type, external_id, object_name, owner, compute_ref, elapsed_sec, "
            "est_cost_usd, severity, violation_reason, query_text FROM findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "finding not found")
    wt, external_id, obj, owner, compute_ref, elapsed, cost, sev, violation, qtext = row
    # Deep diagnosis: ground the copilot in the query's REAL operator metrics when available.
    metrics = _query_metrics(external_id) if wt == "query" else None
    system = (
        "You are a Databricks platform-engineering copilot. Given ONE flagged workload — its facts, its "
        "SQL, and (when available) its real operator metrics from Query History — explain concisely WHY "
        "it is long-running or costly, then give specific, ranked, actionable Databricks remediations. "
        "Ground every claim in the provided metrics/SQL; do not invent data. Be brief (a few bullets). "
        "End with a one-line drafted note to the owner.\n\n" + _PLAYBOOK)
    facts = {"workload_type": wt, "object_name": obj, "owner": owner, "compute": compute_ref,
             "elapsed_sec": elapsed, "est_cost_usd": cost, "severity": sev, "violation_reason": violation}
    user = f"Flagged workload facts:\n{json.dumps(facts, indent=2, default=str)}\n"
    if metrics:
        user += f"\nReal operator metrics (Query History):\n{json.dumps(metrics, indent=2, default=str)}\n"
    if qtext:
        user += f"\nSQL:\n{qtext[:4000]}\n"
    try:
        answer = llm.chat(system, user)
    except Exception as exc:
        raise HTTPException(502, f"copilot unavailable: {exc}")
    return {"finding_id": finding_id, "model": llm.MODEL, "explanation": answer, "metrics": metrics}


# ── agentic: Ask Watchtower (grounded NL Q&A over current state) ──────────────
class Ask(BaseModel):
    question: str


@router.post("/ask")
def ask(a: Ask):
    q = (a.question or "").strip()
    if not q:
        raise HTTPException(400, "empty question")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT severity, count(*) FROM findings WHERE status <> 'resolved' GROUP BY severity")
        by_sev = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT workload_type, count(*), round(coalesce(sum(est_cost_usd),0)::numeric,2) "
                    "FROM findings WHERE status <> 'resolved' GROUP BY workload_type")
        by_type = [{"type": r[0], "count": r[1], "est_cost_usd": float(r[2])} for r in cur.fetchall()]
        cur.execute("SELECT workload_type, object_name, owner, round(elapsed_sec)::int, est_cost_usd, "
                    "severity, violation_reason FROM findings WHERE status <> 'resolved' "
                    "ORDER BY coalesce(alert_priority,0) DESC LIMIT 15")
        cols = ["type", "object", "owner", "elapsed_sec", "est_cost_usd", "severity", "violation"]
        top = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT status, count(*) FROM cards GROUP BY status")
        cards = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT name, workload_type, metric, threshold, severity, enabled FROM rules ORDER BY workload_type")
        rcols = ["name", "workload_type", "metric", "threshold", "severity", "enabled"]
        rules = [dict(zip(rcols, r)) for r in cur.fetchall()]
    context = {"open_by_severity": by_sev, "open_by_type": by_type, "top_open_findings": top,
               "cards_by_status": cards, "rules": rules}
    system = (
        "You are Watchtower's analyst. Answer the user's question ONLY from the JSON context of current "
        "Databricks workload findings, cards, and rules. Be concise; cite specific numbers, owners, and "
        "object names. If the answer isn't in the context, say so plainly. Never invent data.")
    user = f"Context:\n{json.dumps(context, indent=2, default=str)}\n\nQuestion: {q}"
    try:
        answer = llm.chat(system, user)
    except Exception as exc:
        raise HTTPException(502, f"ask unavailable: {exc}")
    return {"question": q, "model": llm.MODEL, "answer": answer}


# ── triage cards (Kanban) ────────────────────────────────────────────────────
@router.get("/cards")
def list_cards(limit: int = 500):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.id, c.finding_id, c.status, c.priority, c.notes, c.assignee_id,
                      m.name AS assignee_name,
                      f.workload_type, f.object_name, f.owner, f.elapsed_sec,
                      f.est_cost_usd, f.severity, f.external_id, f.query_text,
                      f.health_status, coalesce(f.alert_priority, 0) AS alert_priority,
                      f.violation_reason
               FROM cards c
               JOIN findings f ON c.finding_id = f.id
               LEFT JOIN it_members m ON c.assignee_id = m.id
               ORDER BY coalesce(f.alert_priority,0) DESC, """ + _SEV_RANK + """ DESC, c.created_at DESC
               LIMIT %s""", (limit,))
        return rows_to_dicts(cur)


class CardPatch(BaseModel):
    status: str | None = None
    assignee_id: int | None = None
    priority: str | None = None
    notes: str | None = None


@router.patch("/cards/{card_id}")
def update_card(card_id: int, patch: CardPatch):
    sets, params = [], []
    for field in ("status", "assignee_id", "priority", "notes"):
        val = getattr(patch, field)
        if val is not None:
            sets.append(f"{field} = %s")
            params.append(val)
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    params.append(card_id)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE cards SET {', '.join(sets)} WHERE id = %s RETURNING id", params)
        if cur.fetchone() is None:
            raise HTTPException(404, "card not found")
    return {"ok": True}


# ── rules ────────────────────────────────────────────────────────────────────
@router.get("/rules")
def list_rules():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM rules ORDER BY workload_type, threshold")
        return rows_to_dicts(cur)


class Rule(BaseModel):
    name: str
    workload_type: str
    metric: str = "elapsed_sec"
    threshold: float
    severity: str = "warning"
    action: str = "card"
    enabled: bool = True


@router.post("/rules")
def create_rule(rule: Rule):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rules (name, workload_type, metric, threshold, severity, action, enabled) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (rule.name, rule.workload_type, rule.metric, rule.threshold,
             rule.severity, rule.action, rule.enabled))
        return {"id": cur.fetchone()[0]}


class RulePatch(BaseModel):
    threshold: float | None = None
    severity: str | None = None
    action: str | None = None
    enabled: bool | None = None


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, patch: RulePatch):
    sets, params = [], []
    for field in ("threshold", "severity", "action", "enabled"):
        val = getattr(patch, field)
        if val is not None:
            sets.append(f"{field} = %s")
            params.append(val)
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = now()")
    params.append(rule_id)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE rules SET {', '.join(sets)} WHERE id = %s RETURNING id", params)
        if cur.fetchone() is None:
            raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rules WHERE id = %s", (rule_id,))
    return {"ok": True}


# ── members + actions ────────────────────────────────────────────────────────
@router.get("/members")
def list_members():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, email, role FROM it_members WHERE active ORDER BY name")
        return rows_to_dicts(cur)


# ── distribution list (email subscribers) ───────────────────────────────────
@router.get("/subscribers")
def list_subscribers():
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, email, active, created_at FROM subscribers ORDER BY email")
        return rows_to_dicts(cur)


class Subscriber(BaseModel):
    email: str


@router.post("/subscribers")
def add_subscriber(sub: Subscriber):
    email = sub.email.strip()
    if "@" not in email:
        raise HTTPException(400, "invalid email")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO subscribers (email) VALUES (%s) "
            "ON CONFLICT (email) DO UPDATE SET active = TRUE RETURNING id", (email,))
        return {"id": cur.fetchone()[0]}


@router.delete("/subscribers/{sub_id}")
def delete_subscriber(sub_id: int):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM subscribers WHERE id = %s", (sub_id,))
    return {"ok": True}


@router.get("/actions")
def list_actions(limit: int = 100):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.*, f.object_name, f.owner FROM action_log a "
            "LEFT JOIN findings f ON a.finding_id = f.id ORDER BY a.id DESC LIMIT %s", (limit,))
        return rows_to_dicts(cur)


@router.post("/actions/{action_id}/send")
def send_action(action_id: int):
    """Send a drafted email via SMTP (mailer). If SMTP isn't configured in the
    'watchtower' secret scope, the action stays a draft (nothing is sent)."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.target, a.payload, f.object_name, f.owner, f.violation_reason "
            "FROM action_log a LEFT JOIN findings f ON a.finding_id = f.id "
            "WHERE a.id = %s AND a.result IN ('drafted','failed')", (action_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "action not found or already sent")
    target, payload, obj, owner, violation = row
    # send to the distribution list; fall back to the workload owner if the list is empty
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM subscribers WHERE active ORDER BY email")
        recipients = [r[0] for r in cur.fetchall()]
    if not recipients and owner:
        recipients = [owner]
    subject = f"[Workload Watchtower] Flagged workload: {obj or 'your workload'}"
    body = (
        f"Hi {owner or 'there'},\n\n"
        f"Workload Watchtower flagged one of your Databricks workloads.\n\n"
        f"Details:\n{json.dumps(payload, indent=2, default=str)}\n\n"
    )
    if violation and "STATEMENT_TIMEOUT_OVERRIDE" in violation:
        body += f"A session-level STATEMENT_TIMEOUT override was detected — this bypasses the " \
                f"workspace/warehouse guardrail (session scope wins). Review:\n{_TIMEOUT_DOC}\n\n"
    body += "— Workload Watchtower"

    ok, detail = mailer.send_email(recipients, subject, body)
    # config / no-recipient problems stay 'drafted' (recoverable once fixed); real send
    # errors become 'failed' but remain retryable via this same endpoint.
    recoverable = ("not configured" in detail) or ("no recipients" in detail)
    result = "sent" if ok else ("drafted" if recoverable else "failed")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE action_log SET result = %s, error = %s WHERE id = %s",
                    (result, None if ok else detail, action_id))
    return {"ok": ok, "result": result, "detail": detail}


@router.get("/poll-runs")
def poll_runs(limit: int = 30):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM poll_runs ORDER BY id DESC LIMIT %s", (limit,))
        return rows_to_dicts(cur)


# ── trends (UC Delta) ────────────────────────────────────────────────────────
@router.get("/trends")
def trends(hours: int = 24):
    try:
        return uc.trends(hours)
    except Exception as exc:  # UC/warehouse may be cold or empty
        raise HTTPException(502, f"trends unavailable: {exc}")


# ── admin op: trigger the poller on demand ───────────────────────────────────
# The poller normally runs on its scheduled interval (the Lakeflow job). This lets an
# admin force a poll now — e.g. right after setup, or to confirm a rule change takes effect.
_POLLER_JOB_NAME = os.environ.get("WT_POLLER_JOB_NAME", "watchtower-poller")


@router.post("/ops/poll")
def trigger_poll():
    """Run the poller job now. Requires the app's service principal to have run
    permission (CAN_MANAGE_RUN) on the poller job."""
    jobs = list(w.jobs.list(name=_POLLER_JOB_NAME))
    if not jobs:
        raise HTTPException(404, f"poller job '{_POLLER_JOB_NAME}' not found")
    run = w.jobs.run_now(job_id=jobs[0].job_id)
    return {"job_id": jobs[0].job_id, "run_id": run.run_id}
