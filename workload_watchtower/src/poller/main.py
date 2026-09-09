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

import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

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
    setdefault("WT_FAIL_ON_CRITICAL", args.get("fail-on-critical"))
    setdefault("WT_MODEL", args.get("wt-model"))   # serving endpoint for semantic-rule classification
    setdefault("WT_APP_URL", args.get("wt-app-url"))   # app URL for the "View in Workload Watchtower" email link
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
import killer                    # noqa: E402
import budget                    # noqa: E402
import llm                       # noqa: E402

_TIMEOUT_DOC = "https://docs.databricks.com/aws/en/sql/language-manual/parameters/statement_timeout"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("poller")

# All required — set on the poller job (see databricks.yml / config.env). No hardcoded defaults.
WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
UC_SNAPSHOTS = os.environ["WT_UC_SNAPSHOTS"]   # <catalog>.<schema>.workload_snapshots
UC_ALERTS = os.environ["WT_UC_ALERTS"]         # <catalog>.<schema>.alert_events

_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}
# Semantic rules classify only queries started within this window (≈ the poll interval) so the
# 5-min poll doesn't re-send the same queries to the model across the overlapping 15-min scan.
_SEMANTIC_WINDOW_MIN = int(os.environ.get("WT_SEMANTIC_WINDOW_MIN", "6"))
_PATTERN_MAX_CHARS = int(os.environ.get("WT_PATTERN_MAX_CHARS", "20000"))


# ── UC Delta writes via the SQL warehouse ────────────────────────────────────
def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, datetime):
        # A naive datetime would be assumed to be system-local by astimezone(); treat it as UTC
        # so a non-UTC poller host doesn't shift the timestamp.
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        # Emit an explicit +00:00 offset so the value is an unambiguous UTC instant regardless of
        # the warehouse's session timezone (a zone-less literal is read in the session tz, which
        # would offset event_ts from current_timestamp() in the alert window on non-UTC sessions).
        return "TIMESTAMP '%s+00:00'" % v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
        "auto_kill": _wants_auto_kill(matched),
    }


def _wants_auto_kill(rules: list[dict]) -> bool:
    """Auto-kill fires only when a SINGLE matched rule legitimately requests it: it is critical,
    its own action includes 'kill', and auto_kill is set. Guards against a 'kill' token from one
    rule combining with a different rule's auto_kill flag (the union of actions is NOT sufficient)."""
    return any(r.get("auto_kill") and r["severity"] == "critical"
               and "kill" in (r["action"] or "").split("_") for r in rules)


def _pattern_hit(rule: dict, text: str) -> bool:
    """Whether a `pattern`-kind rule matches the query text (regex or case-insensitive substring).
    Regex runs against a bounded prefix of the text — a cap on the input is a cheap guard against
    catastrophic backtracking (ReDoS) from an admin-authored pattern stalling the whole poll."""
    pat = rule.get("pattern") or ""
    if not pat:
        return False
    text = text[:_PATTERN_MAX_CHARS]
    if rule.get("pattern_is_regex"):
        try:
            return re.search(pat, text, re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            return False
    return pat.lower() in text.lower()


def _pattern_finding(q: dict, rule: dict, violation: str) -> dict:
    """Build a pattern_match workload (with _match attached) so it flows through the same
    findings-persistence path as threshold matches. external_id is per (query, rule) so one query
    can raise a distinct finding for each rule it trips, without colliding with `query` findings."""
    sev = rule["severity"]
    text = q["query_text"]
    return {
        "workload_type": "pattern_match",
        "external_id": f"{q['query_id']}:{rule['id']}",
        "owner": q.get("owner"),
        "object_name": (text[:120] + "…") if len(text) > 120 else text,
        "compute_ref": q.get("warehouse_id"),
        "started_at": q.get("started_at"),
        "elapsed_sec": q.get("elapsed_sec"),
        "est_cost_usd": 0.0,
        "_dbu_rate": 0.0,
        "query_text": text,
        "details": {"matched_rule": rule["name"], "kind": rule["kind"], "query_id": q["query_id"]},
        "_match": {
            "rule": rule, "actions": set((rule["action"] or "").split("_")), "count": 1,
            "violation_reason": violation, "health_status": _health(sev),
            "alert_priority": _priority(sev, q.get("elapsed_sec"), 0, {violation}),
            "auto_kill": _wants_auto_kill([rule]),
        },
    }


def _match_pattern_rules(w: WorkspaceClient, pattern_rules: list[dict], errors: list) -> list[dict]:
    """Scan the recent query window once and raise findings for every `pattern`/`semantic` rule
    that matches. Pattern matching is cheap (local regex) and scans the full window; semantic
    matching costs a model call, so it is restricted to queries started within ~the poll interval
    (else the 5-min poll re-classifies the overlapping 15-min window every cycle) and batched (one
    call per semantic rule)."""
    if not pattern_rules:
        return []
    try:
        queries = collectors.recent_queries(w, window_minutes=15)
    except Exception as exc:
        errors.append(f"pattern_scan: {exc}")
        log.warning("recent_queries failed: %s", exc)
        return []
    out = []
    subs = [r for r in pattern_rules if r["kind"] == "pattern"]
    sems = [r for r in pattern_rules if r["kind"] == "semantic"]
    for q in queries:
        for rule in subs:
            if _pattern_hit(rule, q["query_text"]):
                out.append(_pattern_finding(q, rule, "PATTERN_MATCH"))
    if sems:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_SEMANTIC_WINDOW_MIN)
        recent = [q for q in queries if q.get("started_at") and q["started_at"] >= cutoff]
        by_id = {q["query_id"]: q for q in recent}
        items = [(q["query_id"], q["query_text"]) for q in recent]
        for rule in sems:
            try:
                for qid in llm.classify_matches(w, rule.get("pattern") or rule["name"], items):
                    out.append(_pattern_finding(by_id[qid], rule, "SEMANTIC_MATCH"))
            except Exception as exc:
                errors.append(f"semantic rule {rule['id']}: {exc}")
                log.warning("semantic classify failed for rule %s: %s", rule["id"], exc)
    return out


# ── human-legible alert email ────────────────────────────────────────────────
# WT_APP_URL is deployment-specific (set via config.env → app.yaml / poller param); default empty so
# the "View in Workload Watchtower" line only renders when a real URL is configured (no hardcoding).
_APP_URL = os.environ.get("WT_APP_URL", "")
_TYPE_LABEL = {"query": "SQL query", "pattern_match": "SQL query", "job_run": "Job run",
               "pipeline": "Pipeline", "cluster": "Cluster", "serving": "Serving endpoint"}
# Fallback short labels (used only if a violation enum has no richer phrase in _reason_phrase).
_VIOLATION_LABEL = {
    "LONG_RUNNING": "long-running",
    "COST_BURST": "high estimated cost",
    "STATEMENT_TIMEOUT_OVERRIDE": "session STATEMENT_TIMEOUT override",
    "PATTERN_MATCH": "matched a query-text pattern rule",
    "SEMANTIC_MATCH": "matched a semantic (LLM) rule",
}
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.I)
_owner_cache: dict[str, str] = {}


def _display_owner(w: WorkspaceClient, raw: str | None) -> str:
    """Resolve a workload owner into something a human reads, never a bare UUID. Interactive users
    already arrive as an email/username; service-principal-submitted workloads arrive as the SP's
    application id (a UUID) — resolve that to the SP's display name. Cached per process; a lookup
    miss is logged (an unresolved id is a data gap worth surfacing, not silently passing through)
    and falls back to a short, labelled form."""
    if not raw:
        return "(unknown)"
    if "@" in raw or not _UUID_RE.match(raw):
        return raw                                   # email / username / already a display name
    if raw in _owner_cache:
        return _owner_cache[raw]
    disp = None
    try:
        matches = list(w.service_principals.list(filter=f'applicationId eq "{raw}"'))
        if matches and matches[0].display_name:
            disp = f"{matches[0].display_name} (service principal)"
    except Exception as exc:
        log.info("owner lookup failed for %s: %s", raw, exc)
    if not disp:
        log.warning("could not resolve owner id %s to a display name", raw)
        disp = f"service principal {raw[:8]}…"
    _owner_cache[raw] = disp
    return disp


def _fmt_dur(sec) -> str | None:
    if not sec:
        return None
    sec = int(sec); h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return (f"{h}h " if h else "") + (f"{m}m " if (m or h) else "") + f"{s}s"


def _reason_phrase(v: str, f: dict) -> str:
    """One human sentence for a violation enum — the single source of truth for reason copy, enriched
    with the matched rule's own threshold where we have it, so a new violation type is a dict/branch
    edit here rather than template surgery. Never returns a raw enum."""
    metric, thr = f.get("rule_metric"), f.get("rule_threshold")
    if v == "LONG_RUNNING":
        dur = _fmt_dur(f.get("elapsed_sec"))
        base = f"ran {dur}" if dur else "long-running"
        if metric == "elapsed_sec" and thr:
            # _fmt_dur rounds to whole seconds and returns None below 1s; keep a sub-second
            # threshold legible rather than printing "None".
            thr_disp = _fmt_dur(int(thr)) or f"{thr:g}s"
            base += f", over the {thr_disp} threshold for this workload"
        return base
    if v == "COST_BURST":
        c = f.get("est_cost_usd")
        base = f"estimated ${c:.2f}" if c is not None else "high estimated cost"
        if metric == "est_cost_usd" and thr:
            base += f", over the ${float(thr):.2f} threshold"
        return base
    if v == "STATEMENT_TIMEOUT_OVERRIDE":
        return "set a session STATEMENT_TIMEOUT that overrides the workspace/warehouse guardrail"
    return _VIOLATION_LABEL.get(v, v.replace("_", " ").lower())


def _alert_email(f: dict, kill_result: tuple | None = None) -> tuple[str, str]:
    """Compose a human-legible (subject, body) from finding facts instead of dumping JSON. Leads with
    a plain-English sentence (what was flagged + what action was taken) so the reader isn't left to
    assemble it from labels, then an aligned label/value block. `f` keys: object, workload_type,
    owner (already resolved via _display_owner), elapsed_sec, est_cost_usd, violations (pipe-joined),
    rule_name, rule_metric, rule_threshold, started_at, statement_id (SQL only — the Query History
    id the recipient can search by). `kill_result` = (ok, detail) when an auto-kill ran for this
    finding."""
    wl = _TYPE_LABEL.get(f.get("workload_type"), "workload")
    obj = (f.get("object") or "(unknown)").strip()
    owner = f.get("owner") or "(unknown)"
    viols = [v for v in (f.get("violations") or "").split("|") if v]
    reason = "; ".join(_reason_phrase(v, f) for v in viols) or "flagged by a rule"

    # lead sentence: what happened + the action taken, in plain English
    if kill_result is None:
        verb = "flagged"
        action_line = "No automated action was taken — review and triage in Workload Watchtower."
    elif kill_result[0]:
        verb = "flagged and automatically cancelled"
        action_line = "Action taken: automatically cancelled by Workload Watchtower."
    else:
        verb = "flagged"
        action_line = f"Action attempted: automatic cancel did not succeed ({kill_result[1]})."
    who = f" from {owner}" if owner != "(unknown)" else ""
    # lowercase the noun for mid-sentence use but keep acronyms (SQL) intact
    wl_lead = " ".join(t if t.isupper() else t.lower() for t in wl.split())
    lead = f"Workload Watchtower {verb} a {wl_lead}{who}."

    # subject: action-aware, no query snippet (a truncated SQL tail read as noise)
    subject = f"[Workload Watchtower] {wl} flagged"
    if kill_result and kill_result[0]:
        subject += " and cancelled"
    label = "Query" if f.get("workload_type") in ("query", "pattern_match") else "Object"
    L = [lead, ""]

    def row(k: str, v: str) -> None:
        L.append(f"  {k:<11} {v}")   # left-aligned label column so the block scans as a table

    row("Workload", wl)
    row(label, obj if len(obj) <= 200 else obj[:199] + "…")
    # Statement ID is a UUID, but (unlike the owner id) it's actionable: the recipient can paste it
    # into Query History to find this exact statement. Only SQL workloads carry one.
    if f.get("statement_id"):
        row("Statement", f"{f['statement_id']}  (search Query History by this ID)")
    row("Owner", owner)
    row("Reason", reason)
    # Truthy check (not `is not None`): pattern/semantic findings hardcode est_cost_usd=0.0 (cost is
    # never computed for them), so a "$0.00 estimate" line would be misleading — omit it there.
    if f.get("est_cost_usd"):
        row("Est. cost", f"${f['est_cost_usd']:.2f}  (live estimate, final cost may differ)")
    sa = f.get("started_at")
    if sa:
        # UTC: the recipient's local tz isn't known server-side, so label the zone rather than guess.
        row("Started", sa.strftime("%Y-%m-%d %H:%M UTC") if hasattr(sa, "strftime") else str(sa))
    if f.get("rule_name"):
        row("Rule", f'"{f["rule_name"]}"')
    L += ["", f"  {action_line}"]
    if "STATEMENT_TIMEOUT_OVERRIDE" in viols:
        L.append(f"  Note: a session-level SET STATEMENT_TIMEOUT overrides the guardrail. See {_TIMEOUT_DOC}")
    if _APP_URL:
        L += ["", f"  View in Workload Watchtower: {_APP_URL}"]
    L += ["", "— Workload Watchtower, automated"]
    return subject, "\n".join(L)


# ── one poll cycle ───────────────────────────────────────────────────────────
def poll(w: WorkspaceClient) -> dict:
    t0 = time.time()
    errors: list[str] = []

    # 1. rules + roster (Lakebase)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, workload_type, kind, metric, threshold, severity, action, "
                    "pattern, pattern_is_regex, auto_kill FROM rules WHERE enabled = TRUE")
        rules_by_type: dict[str, list[dict]] = {}   # threshold rules, keyed by workload_type
        pattern_rules: list[dict] = []              # pattern + semantic rules (scan query text)
        for rid, name, wt, kind, metric, thr, sev, action, pattern, is_rx, auto_kill in cur.fetchall():
            r = {"id": rid, "name": name, "kind": kind, "metric": metric, "threshold": float(thr),
                 "severity": sev, "action": action, "pattern": pattern,
                 "pattern_is_regex": is_rx, "auto_kill": auto_kill}
            if kind in ("pattern", "semantic"):
                pattern_rules.append(r)
            else:
                rules_by_type.setdefault(wt, []).append(r)
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

    # 3b. pattern / semantic rules — scan the recent query window and raise pattern_match findings.
    findings.extend(_match_pattern_rules(w, pattern_rules, errors))

    # 4/5. persist findings + cards + alerts + snapshots
    new_ct = upd_ct = new_critical = new_warning = 0
    snap_rows, alert_rows, pending_sends, pending_kills = [], [], [], []
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
                if m["rule"]["severity"] == "critical":
                    new_critical += 1
                elif m["rule"]["severity"] == "warning":
                    new_warning += 1
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
                        # Collect the facts; the legible body is composed AFTER auto-kills run (5b)
                        # so it can report whether the workload was cancelled. Keep the RAW owner —
                        # _display_owner may make a SCIM call, resolved post-txn in the send phase.
                        facts = {"object": wl.get("object_name"), "workload_type": wl["workload_type"],
                                 "owner": wl.get("owner"), "elapsed_sec": wl.get("elapsed_sec"),
                                 "est_cost_usd": wl["est_cost_usd"], "violations": m["violation_reason"],
                                 "rule_name": m["rule"]["name"], "rule_metric": m["rule"]["metric"],
                                 "rule_threshold": m["rule"]["threshold"], "started_at": wl.get("started_at"),
                                 # SQL statement id (pattern_match external_id is "query_id:rule_id")
                                 "statement_id": (wl["external_id"].split(":", 1)[0]
                                                  if wl["workload_type"] in ("query", "pattern_match") else None)}
                        pending_sends.append((aid, fid, recipients, facts))
                # kill action: auto-cancel a NEW finding when a matched rule legitimately requests
                # it (see _wants_auto_kill), for any killable workload type (queries included, via
                # cancel_execution). Executed OUTSIDE the DB transaction, like the email sends.
                if m.get("auto_kill") and killer.can_kill(wl["workload_type"]):
                    pending_kills.append((fid, m["rule"]["id"], wl["workload_type"], wl["external_id"]))
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

    # 5b. execute auto-kills FIRST (outside the DB transaction) so the alert email can report the
    # outcome; record each attempt in action_log and keep a per-finding result for the email.
    kill_by_finding: dict[int, tuple[bool, str]] = {}
    if pending_kills:
        kres = []
        for fid, rid, wt, ext in pending_kills:
            ok, detail = killer.kill_workload(w, wt, ext)
            kill_by_finding[fid] = (ok, detail)
            kres.append((fid, rid, ext, "killed" if ok else "failed", detail))
            if not ok:
                errors.append(f"kill {wt}:{ext}: {detail}")
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            for fid, rid, ext, res, detail in kres:
                cur.execute(
                    "INSERT INTO action_log (finding_id, rule_id, action, target, payload, result, error) "
                    "VALUES (%s,%s,'kill',%s,%s,%s,%s)",
                    (fid, rid, ext, Json({"detail": detail}), res, None if res == "killed" else detail))

    # 5c. auto-send critical emails (legible body, now including any auto-kill outcome). Owner is
    # resolved here (post-txn): _display_owner may make a SCIM lookup, kept off the Lakebase conn.
    if pending_sends:
        results = []
        for aid, fid, recipients, facts in pending_sends:
            facts = {**facts, "owner": _display_owner(w, facts.get("owner"))}
            subject, body = _alert_email(facts, kill_by_finding.get(fid))
            ok, detail = mailer.send(smtp_cfg, recipients, subject, body)
            results.append((aid, "sent" if ok else "failed", None if ok else detail))
            if not ok:
                errors.append(f"email {aid}: {detail}")
        with lakebase.connect(w) as conn, conn.cursor() as cur:
            for aid, res, err in results:
                cur.execute("UPDATE action_log SET result = %s, error = %s, updated_at = now() WHERE id = %s",
                            (res, err, aid))

    # 5d. per-user cost budget (Feature 3) — hourly-gated inside the poll; emails over-budget users.
    try:
        budget_summary = budget.run_budget_scan(w)
    except Exception as exc:
        budget_summary = {"error": str(exc)}
    if budget_summary.get("error"):
        errors.append(f"budget: {budget_summary['error']}")

    # 6. record poll run
    dur_ms = int((time.time() - t0) * 1000)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO poll_runs (finished_at, duration_ms, workloads_seen, seen_by_type, "
            "findings_new, findings_upd, errors) VALUES (now(),%s,%s,%s,%s,%s,%s)",
            (dur_ms, len(workloads), Json(seen_by_type), new_ct, upd_ct, "; ".join(errors) or None))

    # Publish per-severity new-finding counts as job task values so the downstream `alert_gate`
    # condition task can decide whether to run the SQL-Alert evaluation at all (skipping it on quiet
    # polls saves serverless SQL cost). Best-effort: no-op locally or if the runtime lacks dbutils.
    try:
        from databricks.sdk.runtime import dbutils   # noqa: E402
        dbutils.jobs.taskValues.set(key="new_critical", value=new_critical)
        dbutils.jobs.taskValues.set(key="new_warning", value=new_warning)
    except Exception as exc:
        log.info("task values not set (local run or unsupported task context): %s", exc)

    summary = {"workloads_seen": len(workloads), "seen_by_type": seen_by_type,
               "findings": len(findings), "new": new_ct, "new_critical": new_critical,
               "new_warning": new_warning, "updated": upd_ct, "errors": errors,
               "duration_ms": dur_ms, "list_price": price, "budget": budget_summary}
    log.info("poll complete: %s", summary)
    return summary


def send_action(w: WorkspaceClient, action_id: int) -> dict:
    """Send one drafted/failed email action via SMTP (from jobs compute, which — unlike Apps
    compute — can reach SMTP). Triggered by the app's /actions/{id}/send through the
    `watchtower-send` job. Same recipients (active subscribers, else the workload owner) and body
    as the app's former in-process send; writes back 'sent'/'failed' (recoverable cases stay
    'drafted')."""
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT f.object_name, f.owner, f.violation_reason, f.workload_type, f.elapsed_sec, "
            "f.est_cost_usd, f.started_at, f.external_id, r.name, r.metric, r.threshold "
            "FROM action_log a LEFT JOIN findings f ON a.finding_id = f.id "
            "LEFT JOIN rules r ON f.matched_rule = r.id WHERE a.id = %s", (action_id,))
        row = cur.fetchone()
    if row is None:
        log.warning("send_action: action %s not found", action_id)
        return {"ok": False, "detail": "action not found"}
    obj, owner, violation, wtype, elapsed, cost, started, external_id, rule_name, rmetric, rthr = row
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM subscribers WHERE active ORDER BY email")
        recipients = [r[0] for r in cur.fetchall()]
    if not recipients and owner:
        recipients = [owner]

    subject, body = _alert_email({
        "object": obj, "workload_type": wtype, "owner": _display_owner(w, owner),
        "elapsed_sec": elapsed, "est_cost_usd": cost, "violations": violation,
        "rule_name": rule_name, "rule_metric": rmetric,
        "rule_threshold": float(rthr) if rthr is not None else None, "started_at": started,
        "statement_id": (external_id.split(":", 1)[0]
                         if wtype in ("query", "pattern_match") and external_id else None)})

    cfg = mailer.load_config(w)
    if not cfg:
        # Not-configured is recoverable (drop the secret in, then retry) — keep it 'drafted'.
        ok, detail, result = False, "SMTP not configured in the secret scope", "drafted"
    else:
        ok, detail = mailer.send(cfg, recipients, subject, body)
        # 'no recipients' is likewise recoverable → stays 'drafted'; only a real send error is 'failed'.
        recoverable = (not ok) and ("no recipients" in detail)
        result = "sent" if ok else ("drafted" if recoverable else "failed")
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("UPDATE action_log SET result = %s, error = %s, updated_at = now() WHERE id = %s",
                    (result, None if ok else detail, action_id))
    log.info("send_action %s -> %s (%s)", action_id, result, detail)
    return {"ok": ok, "result": result, "detail": detail}


def main() -> None:
    w = WorkspaceClient()
    # `watchtower-send` job mode: config comes as --key=value params (see _config_from_args);
    # --send-action=<id> means "send one action's email, then exit" (no poll / bootstrap).
    send_args = [a for a in sys.argv[1:] if a.startswith("--send-action=")]
    if send_args:
        val = send_args[0].split("=", 1)[1]
        try:
            aid = int(val)
        except ValueError:
            log.error("--send-action requires a numeric action id; got %r", val)
            return
        send_action(w, aid)
        return
    try:
        lakebase.bootstrap_schema(w)   # first-run convenience; no-op once the schema exists
    except Exception as exc:
        # A non-owner run identity (e.g. the app SP) can't (re)create indexes on tables it
        # doesn't own — that's expected; the schema is created once by the owner. Poll anyway.
        log.info("bootstrap_schema skipped (already exists / not owner): %s", exc)
    summary = poll(w)
    # Opt-in: fail the job run when a NEW critical finding was raised, so the job's native
    # notifications (email / Slack / PagerDuty via webhook_notifications) fire. Off by default.
    if os.environ.get("WT_FAIL_ON_CRITICAL", "").strip().lower() in ("1", "true", "yes") \
            and summary.get("new_critical"):
        raise SystemExit(
            f"WT_FAIL_ON_CRITICAL: {summary['new_critical']} new critical finding(s) this poll "
            f"— failing the run to trigger native job notifications (findings already recorded).")


if __name__ == "__main__":
    main()
