"""
build_permission_snapshot.py — nightly job that rebuilds the permission snapshot.

Run as the app service principal (workspace admin). Three collectors -> flatten ->
full-replace into Lakebase. The Permission Explorer then answers "what can user X
access?" with one indexed Lakebase query instead of thousands of live API calls.

Run locally:   python -m jobs.build_permission_snapshot
Run as a job:  spark_python_task / python_wheel_task entry point = main()

Auth: the SDK WorkspaceClient() resolves ambient job credentials (SP) or the local
CLI profile. The SP must be a workspace admin (SCIM + Permissions API) and hold
SELECT on system.information_schema.* (granted to `account users` by default on UC).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import uuid

# --- serverless FIPS guard (must run BEFORE psycopg is imported) ---
# Databricks serverless runs OpenSSL in FIPS mode; psycopg[binary] bundles its own
# OpenSSL that fails the FIPS self-test on import (mislabelled "out of memory").
# Neutralising OPENSSL_CONF only affects psycopg's later-loaded copy; the SDK's
# system OpenSSL is already initialised, and sslmode=require needs no CA verify.
os.environ["OPENSSL_CONF"] = "/dev/null"
os.environ.pop("OPENSSL_MODULES", None)
os.environ.pop("OPENSSL_FORCE_FIPS_MODE", None)

# Allow running as `python jobs/build_permission_snapshot.py` from app/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from databricks.sdk import WorkspaceClient

from src import lakebase
from src.scim import build_membership_graph
from src.snapshot_collectors import collect_uc_grants, collect_workspace_acls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("snapshot")

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "6a09f4ec67bb14b5")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _flatten_membership(graph) -> list[dict]:
    """Membership graph -> one (principal, group) edge row per transitive membership."""
    rows: list[dict] = []
    for principal_id, group_ids in graph.transitive_groups.items():
        principal_name = graph.user_name_by_id.get(principal_id) or graph.group_name_by_id.get(principal_id)
        for gid in group_ids:
            rows.append({
                "principal_id": principal_id,
                "principal_name": principal_name,
                "group_id": gid,
                "group_name": graph.group_name_by_id.get(gid, gid),
            })
    return rows


def _resolve_uuid_grantees(uc_rows: list[dict], group_name_by_id: dict[str, str]) -> list[dict]:
    """UC grantees are sometimes opaque group UUIDs; add the human name where known.

    We keep the original grantee value (so exact-match reads still work) and, when
    it resolves to a group name, ALSO emit the name so name-based lookups match.
    """
    out: list[dict] = []
    for r in uc_rows:
        grantee = r.get("grantee", "")
        resolved_name = group_name_by_id.get(grantee)
        r = {**r, "grantee_kind": "group" if resolved_name else "unknown"}
        out.append(r)
        if resolved_name and resolved_name != grantee:
            # duplicate row keyed by the human-readable group name
            out.append({**r, "grantee": resolved_name})
    return out


def main() -> int:
    w = WorkspaceClient()
    run_id = str(uuid.uuid4())
    started = _now()
    log.info("Snapshot %s starting (warehouse=%s)", run_id, WAREHOUSE_ID)

    with lakebase.connect(w, create_schema=True) as conn:
        lakebase.ensure_schema(conn)
        lakebase.record_run(conn, run_id, started, None, "running", 0, 0, 0)

        try:
            log.info("Building membership graph...")
            graph = build_membership_graph()
            group_rows = _flatten_membership(graph)
            log.info("  %d groups, %d membership edges", len(graph.group_name_by_id), len(group_rows))

            log.info("Collecting UC grants from information_schema...")
            uc_rows = collect_uc_grants(w, WAREHOUSE_ID)
            uc_rows = _resolve_uuid_grantees(uc_rows, graph.group_name_by_id)
            log.info("  %d UC grant rows (after UUID resolution)", len(uc_rows))

            log.info("Collecting workspace-object ACLs...")
            ws_rows = collect_workspace_acls(w)
            log.info("  %d workspace ACL rows", len(ws_rows))

            all_acls = uc_rows + ws_rows
            ts = _now()
            n_groups = lakebase.replace_identity_groups(conn, group_rows, ts)
            lakebase.replace_group_uuid_map(conn, graph.group_name_by_id, ts)
            n_acls = lakebase.replace_acls(conn, all_acls, ts)

            lakebase.record_run(conn, run_id, started, _now(), "success",
                                len(uc_rows), n_acls, n_groups)
            log.info("Snapshot %s SUCCESS: %d ACL rows, %d membership edges", run_id, n_acls, n_groups)
            return 0
        except Exception as exc:
            log.exception("Snapshot failed")
            lakebase.record_run(conn, run_id, started, _now(), "failed", 0, 0, 0, str(exc)[:500])
            raise


if __name__ == "__main__":
    raise SystemExit(main())
