"""
killer.py — cancel a running workload via the SDK.

Supported workload types:
  • query / pattern_match — statement_execution.cancel_execution(query_id). For queries submitted
    via the SQL editor or the Statement Execution API, the Query-History query_id is the unified
    statement_id and an admin cancels the running query by that id (verified live: SQL editor + API).
    Best-effort, though: a few persistent-session clients (notably the Python SQL Connector, and
    possibly some JDBC/ODBC/BI drivers) keep their statement in a session the Statement Execution
    API can't reach — there cancel_execution returns OK but is a no-op, and the query stops only when
    that client disconnects. So query kill works for the common channels but isn't guaranteed for
    every client. (job/pipeline/cluster kills below use canonical control APIs and aren't affected.)
  • job_run  — jobs.cancel_run
  • pipeline — pipelines.stop
  • cluster  — clusters.delete (terminate)

Runs as the poller / app service principal (workspace admin), which can cancel others' work — so
callers MUST gate this behind an explicit action + guardrail (manual confirm, or a critical
auto_kill rule), never fire it indiscriminately.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

_KILLABLE = ("query", "pattern_match", "job_run", "pipeline", "cluster")


def can_kill(workload_type: str) -> bool:
    return workload_type in _KILLABLE


def kill_workload(w: WorkspaceClient, workload_type: str, external_id: str) -> tuple[bool, str]:
    """Attempt to cancel one workload. Returns (ok, detail); never raises."""
    try:
        if workload_type in ("query", "pattern_match"):
            # pattern_match external_id is "<query_id>:<rule_id>"; a plain query is just the id.
            # Best-effort (see module docstring): cancel_execution can't reach some persistent-session
            # clients and no-ops there without raising, so a returned "cancelled" is not a guarantee.
            qid = external_id.split(":", 1)[0]
            w.statement_execution.cancel_execution(qid)
            return True, f"cancelled query {qid}"
        if workload_type == "job_run":
            w.jobs.cancel_run(run_id=int(external_id))
            return True, f"cancelled job run {external_id}"
        if workload_type == "pipeline":
            w.pipelines.stop(pipeline_id=external_id)
            return True, f"stopped pipeline {external_id}"
        if workload_type == "cluster":
            w.clusters.delete(cluster_id=external_id)  # terminate (not permanent delete)
            return True, f"terminated cluster {external_id}"
        return False, f"kill not supported for workload_type '{workload_type}'"
    except Exception as exc:
        return False, f"kill failed: {exc}"
