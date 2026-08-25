"""
collectors.py — read LIVE workload state from Databricks REST APIs via the SDK.

Each collector returns a list of normalized workload dicts:

    {
      workload_type: query|job_run|pipeline|cluster|serving,
      external_id:   stable id used to dedupe findings,
      owner:         user/SP that launched it,
      object_name:   query snippet / job / pipeline / cluster / endpoint name,
      compute_ref:   warehouse_id / cluster_id / endpoint,
      started_at:    tz-aware datetime or None,
      elapsed_sec:   float or None,
      query_text:    str or None,
      dbu_meta:      dict passed to cost.dbu_per_hr,
      details:       dict of extras,
    }

Collectors read cross-user state, so the caller must run as a workspace admin
(the poller SP). Each collector may raise; main.py isolates failures per type.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql, jobs, pipelines, compute

_now = lambda: datetime.now(timezone.utc)


def _from_ms(ms: int | None):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _elapsed(start: datetime | None) -> float | None:
    return (_now() - start).total_seconds() if start else None


def _parse_ts(v):
    """Pipeline creation_time may be epoch-ms int or an ISO string."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return _from_ms(int(v))
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    # An ISO string without an offset parses to a naive datetime; assume UTC so downstream
    # UC/Lakebase writes aren't shifted by the host's local timezone.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
def collect_queries(w: WorkspaceClient, wh_size_cache: dict) -> list[dict]:
    """Running / queued SQL statements from Query History."""
    flt = sql.QueryFilter(statuses=[sql.QueryStatus.RUNNING, sql.QueryStatus.QUEUED])
    out = []
    resp = w.query_history.list(filter_by=flt, include_metrics=False, max_results=1000)
    for q in (resp.res or []):  # SDK returns ListQueriesResponse, not an iterator
        start = _from_ms(q.query_start_time_ms)
        wid = q.warehouse_id
        if wid and wid not in wh_size_cache:
            try:
                wh_size_cache[wid] = w.warehouses.get(wid).cluster_size
            except Exception:
                wh_size_cache[wid] = None
        text = (q.query_text or "").strip()
        out.append({
            "workload_type": "query",
            "external_id": q.query_id,
            "owner": q.user_name or q.executed_as_user_name,
            "object_name": (text[:120] + "…") if len(text) > 120 else text,
            "compute_ref": wid,
            "started_at": start,
            "elapsed_sec": _elapsed(start),
            "query_text": text or None,
            "dbu_meta": {"warehouse_size": wh_size_cache.get(wid)},
            "details": {"status": q.status.value if q.status else None,
                        "statement_type": q.statement_type.value if q.statement_type else None},
        })
    return out


# Capture the value in `SET STATEMENT_TIMEOUT = 300` or `SET statement_timeout TO 300`.
_TIMEOUT_VAL_RE = re.compile(r"statement_timeout\s*(?:=|to)?\s*'?(\d+)", re.IGNORECASE)

# A genuine session SET of statement_timeout: the statement is a SET (after any leading SQL
# line/block comments + whitespace) that mentions statement_timeout. This flags real overrides —
# including `-- note\nSET statement_timeout=...` — while excluding statements that merely embed the
# text as a value (e.g. the poller's own INSERTs into alert_events), which is the false positive the
# earlier substring match produced.
_SET_TIMEOUT_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*set\b[^;]*statement_timeout",
    re.IGNORECASE | re.DOTALL)


def collect_timeout_overrides(w: WorkspaceClient, window_minutes: int = 15) -> list[dict]:
    """Session-level `SET STATEMENT_TIMEOUT` statements from Query History — a user overriding the
    workspace/warehouse guardrail (session scope wins over warehouse/workspace). ANY such SET is
    flagged. Scans the recent window; findings dedupe on the SET statement's query id."""
    now_ms = int(_now().timestamp() * 1000)
    flt = sql.QueryFilter(query_start_time_range=sql.TimeRange(
        start_time_ms=now_ms - window_minutes * 60 * 1000, end_time_ms=now_ms))
    out = []
    resp = w.query_history.list(filter_by=flt, include_metrics=False, max_results=1000)
    for q in (resp.res or []):
        text = (q.query_text or "")
        if not _SET_TIMEOUT_RE.match(text):
            continue
        m = _TIMEOUT_VAL_RE.search(text)
        val = int(m.group(1)) if m else None
        start = _from_ms(q.query_start_time_ms)
        out.append({
            "workload_type": "timeout_override",
            "external_id": q.query_id,
            "owner": q.user_name or q.executed_as_user_name,
            "object_name": text.strip()[:120],
            "compute_ref": q.warehouse_id,
            "started_at": start,
            "elapsed_sec": None,
            "query_text": text.strip(),
            "dbu_meta": {},
            "details": {"set_value_seconds": val,
                        "statement_type": q.statement_type.value if q.statement_type else None},
        })
    return out


def collect_job_runs(w: WorkspaceClient) -> list[dict]:
    """Active (running) job runs."""
    out = []
    for r in w.jobs.list_runs(active_only=True, expand_tasks=False):
        start = _from_ms(r.start_time)
        life = r.state.life_cycle_state.value if (r.state and r.state.life_cycle_state) else None
        out.append({
            "workload_type": "job_run",
            "external_id": str(r.run_id),
            "owner": r.creator_user_name,
            "object_name": r.run_name,
            "compute_ref": str(r.job_id) if r.job_id else None,
            "started_at": start,
            "elapsed_sec": _elapsed(start),
            "query_text": None,
            "dbu_meta": {},
            "details": {"life_cycle_state": life, "run_page_url": r.run_page_url},
        })
    return out


def collect_pipelines(w: WorkspaceClient) -> list[dict]:
    """Pipelines currently in a RUNNING state, timed from their latest update."""
    out = []
    for p in w.pipelines.list_pipelines(max_results=100):
        if p.state != pipelines.PipelineState.RUNNING:
            continue
        start = None
        update_id = None
        if p.latest_updates:
            latest = p.latest_updates[0]
            start = _parse_ts(latest.creation_time)
            update_id = latest.update_id
        out.append({
            "workload_type": "pipeline",
            "external_id": p.pipeline_id,
            "owner": p.creator_user_name or p.run_as_user_name,
            "object_name": p.name,
            "compute_ref": p.cluster_id,
            "started_at": start,
            "elapsed_sec": _elapsed(start),
            "query_text": None,
            "dbu_meta": {},
            "details": {"update_id": update_id, "state": p.state.value if p.state else None},
        })
    return out


def collect_clusters(w: WorkspaceClient) -> list[dict]:
    """All-purpose clusters currently RUNNING (excludes job-owned clusters)."""
    out = []
    for c in w.clusters.list():
        if c.state != compute.State.RUNNING:
            continue
        if c.cluster_source == compute.ClusterSource.JOB:
            continue  # job clusters are covered by job_run findings
        start = _from_ms(c.start_time)
        out.append({
            "workload_type": "cluster",
            "external_id": c.cluster_id,
            "owner": c.creator_user_name,
            "object_name": c.cluster_name,
            "compute_ref": c.cluster_id,
            "started_at": start,
            "elapsed_sec": _elapsed(start),
            "query_text": None,
            "dbu_meta": {"cluster_cores": c.cluster_cores},
            "details": {"source": c.cluster_source.value if c.cluster_source else None,
                        "autotermination_minutes": c.autotermination_minutes},
        })
    return out


def collect_serving(w: WorkspaceClient) -> list[dict]:
    """Serving endpoints (persistent). Snapshot-only: no elapsed/runaway concept,
    surfaced for cost visibility rather than duration alerting."""
    out = []
    for e in w.serving_endpoints.list():
        start = _from_ms(e.creation_timestamp)
        state = None
        if e.state and e.state.ready:
            state = e.state.ready.value
        out.append({
            "workload_type": "serving",
            "external_id": e.name,
            "owner": e.creator,
            "object_name": e.name,
            "compute_ref": e.name,
            "started_at": start,
            "elapsed_sec": None,          # persistent — duration rules don't apply
            "query_text": None,
            "dbu_meta": {},
            "details": {"ready": state},
        })
    return out


COLLECTORS = {
    "query": collect_queries,       # takes (w, wh_size_cache)
    "job_run": collect_job_runs,
    "pipeline": collect_pipelines,
    "cluster": collect_clusters,
    "serving": collect_serving,
    "timeout_override": collect_timeout_overrides,
}
