"""
killer.py — cancel a running workload via the SDK.

Supported: job_run (cancel_run), pipeline (stop), cluster (terminate). SQL queries are a
KNOWN GAP — there is no public API to cancel an arbitrary running query by its Query-History
id (Statement Execution can only cancel statements it submitted), so query/pattern_match kills
return a clear "unsupported" result rather than silently failing. TODO: revisit if an internal
cancel path becomes available.

Runs as the poller / app service principal (workspace admin), which can cancel others' work —
so callers MUST gate this behind an explicit action + guardrail (manual confirm, or a critical
auto_kill rule), never fire it indiscriminately.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

QUERY_KILL_UNSUPPORTED = (
    "query cancellation not supported — no public API cancels an arbitrary running query by its "
    "history id (TODO: revisit)")


def can_kill(workload_type: str) -> bool:
    return workload_type in ("job_run", "pipeline", "cluster")


def kill_workload(w: WorkspaceClient, workload_type: str, external_id: str) -> tuple[bool, str]:
    """Attempt to cancel one workload. Returns (ok, detail); never raises."""
    try:
        if workload_type == "job_run":
            w.jobs.cancel_run(run_id=int(external_id))
            return True, f"cancelled job run {external_id}"
        if workload_type == "pipeline":
            w.pipelines.stop(pipeline_id=external_id)
            return True, f"stopped pipeline {external_id}"
        if workload_type == "cluster":
            w.clusters.delete(cluster_id=external_id)  # terminate (not permanent delete)
            return True, f"terminated cluster {external_id}"
        if workload_type in ("query", "pattern_match"):
            return False, QUERY_KILL_UNSUPPORTED
        return False, f"kill not supported for workload_type '{workload_type}'"
    except Exception as exc:
        return False, f"kill failed: {exc}"
