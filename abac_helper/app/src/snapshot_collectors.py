"""
snapshot_collectors.py — data collectors for the nightly permission snapshot.

Three sources feed the effective-permissions snapshot:

1. Unity Catalog grants — read in bulk from system.information_schema.*_privileges
   via one SQL statement per securable level. This REPLACES the old per-object
   grants.get() fan-out in uc_grants.py: it is complete (every catalog/schema/
   table/volume/function/... grant, no drill-down cap) and returns in one round
   trip instead of thousands of API calls.

2. Workspace-object ACLs — jobs/pipelines/warehouses/dashboards/apps/genies/
   clusters/cluster-policies. No system table exposes these, so they still use
   the Permissions REST API — but here they run once in the background job, not
   on every user click.

3. Group membership — resolved once, fully transitively, by scim.build_membership_graph.

Everything is emitted as flat dict rows ready to upsert into Lakebase.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterable, Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL helper (SDK statement-execution API — SP auth, no sql-connector needed)
# ---------------------------------------------------------------------------

def run_sql(w: WorkspaceClient, warehouse_id: str, statement: str) -> list[dict]:
    """Execute a SQL statement on a warehouse and return rows as dicts."""
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout="30s",
    )
    # Poll if the warehouse didn't finish within the inline wait window.
    statement_id = resp.statement_id
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        resp = w.statement_execution.get_statement(statement_id)
    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if (resp.status and resp.status.error) else "unknown error"
        raise RuntimeError(f"SQL failed: {err}\n  statement: {statement[:200]}")
    result = resp.result
    if not result or not result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns] if resp.manifest and resp.manifest.schema else []
    return [dict(zip(cols, row)) for row in result.data_array]


# ---------------------------------------------------------------------------
# 1. Unity Catalog grants — bulk from information_schema
# ---------------------------------------------------------------------------

# (object_type label, information_schema table, [catalog_col, schema_col, name_col])
# name parts are concatenated with '.' to form the object_name; None parts are skipped.
_UC_PRIVILEGE_SOURCES: list[tuple[str, str, list[Optional[str]]]] = [
    ("Catalog",           "catalog_privileges",            ["catalog_name"]),
    ("Schema",            "schema_privileges",             ["catalog_name", "schema_name"]),
    ("Table",             "table_privileges",              ["table_catalog", "table_schema", "table_name"]),
    ("Volume",            "volume_privileges",             ["volume_catalog", "volume_schema", "volume_name"]),
    ("Function",          "routine_privileges",            ["specific_catalog", "specific_schema", "specific_name"]),
    ("Connection",        "connection_privileges",         ["connection_name"]),
    ("External Location", "external_location_privileges",  ["external_location_name"]),
    ("Storage Credential","storage_credential_privileges", ["storage_credential_name"]),
    ("Metastore",         "metastore_privileges",          ["metastore_id"]),
]


def collect_uc_grants(w: WorkspaceClient, warehouse_id: str) -> list[dict]:
    """Return every UC grant as flat rows.

    Row shape: {object_type, object_name, object_id, grantee, permission}.
    grantee may be a login/email, a group display name, OR an opaque group UUID
    (all three occur in the privilege tables) — the snapshot job resolves UUIDs
    against the membership graph before writing.
    """
    rows: list[dict] = []
    for object_type, table, name_cols in _UC_PRIVILEGE_SOURCES:
        select_cols = ", ".join([c for c in name_cols if c] + ["grantee", "privilege_type"])
        stmt = f"SELECT {select_cols} FROM system.information_schema.{table}"
        try:
            for r in run_sql(w, warehouse_id, stmt):
                name = ".".join(str(r[c]) for c in name_cols if c and r.get(c) is not None)
                rows.append({
                    "object_type": object_type,
                    "object_name": name,
                    "object_id": name,          # UC securables are keyed by full name
                    "grantee": r.get("grantee") or "",
                    "permission": r.get("privilege_type") or "",
                })
        except Exception as exc:
            log.warning("UC grants collect failed for %s: %s", table, exc)
    return rows


# ---------------------------------------------------------------------------
# 2. Workspace-object ACLs — Permissions REST API
# ---------------------------------------------------------------------------

# (object_type label, permissions API resource_type, lister -> [(id, name), ...])
def _workspace_object_sources(w: WorkspaceClient) -> list[tuple[str, str, Callable[[], Iterable[tuple[str, str]]]]]:
    return [
        ("Cluster",        "clusters",        lambda: [(c.cluster_id, c.cluster_name or c.cluster_id) for c in w.clusters.list() if c.cluster_id]),
        ("Cluster Policy", "cluster-policies", lambda: [(p.policy_id, p.name or p.policy_id) for p in w.cluster_policies.list() if p.policy_id]),
        ("Job",            "jobs",            lambda: [(str(j.job_id), (j.settings.name if j.settings else None) or str(j.job_id)) for j in w.jobs.list() if j.job_id]),
        ("Pipeline",       "pipelines",       lambda: [(p.pipeline_id, p.name or p.pipeline_id) for p in w.pipelines.list_pipelines() if p.pipeline_id]),
        ("SQL Warehouse",  "sql/warehouses",  lambda: [(wh.id, wh.name or wh.id) for wh in w.warehouses.list() if wh.id]),
        ("Dashboard",      "dashboards",      lambda: [(d.dashboard_id, d.display_name or d.dashboard_id) for d in w.lakeview.list() if d.dashboard_id]),
        ("App",            "apps",            lambda: [(a.name, a.name) for a in w.apps.list() if a.name]),
        ("Genie Space",    "genie",           lambda: [(s.space_id, s.title or s.space_id) for s in (getattr(w.genie.list_spaces(), "spaces", None) or []) if s.space_id]),
    ]


def collect_workspace_acls(w: WorkspaceClient) -> list[dict]:
    """Return every workspace-object ACL entry as flat rows.

    Row shape: {object_type, object_name, object_id, grantee, grantee_kind, permission}.
    grantee_kind is user|group|service_principal so the resolver can match cleanly.
    """
    rows: list[dict] = []
    for object_type, resource_type, lister in _workspace_object_sources(w):
        try:
            objects = list(lister())
        except Exception as exc:
            log.warning("Listing %s failed: %s", object_type, exc)
            continue
        for obj_id, obj_name in objects:
            try:
                acl = w.permissions.get(resource_type, obj_id)
            except Exception as exc:
                log.debug("permissions.get(%s, %s) failed: %s", resource_type, obj_id, exc)
                continue
            for entry in (acl.access_control_list or []):
                if entry.user_name:
                    grantee, kind = entry.user_name, "user"
                elif entry.group_name:
                    grantee, kind = entry.group_name, "group"
                elif entry.service_principal_name:
                    grantee, kind = str(entry.service_principal_name), "service_principal"
                else:
                    continue
                for perm in (entry.all_permissions or []):
                    level = perm.permission_level.value if perm.permission_level else str(perm.permission_level)
                    rows.append({
                        "object_type": object_type,
                        "object_name": obj_name,
                        "object_id": str(obj_id),
                        "grantee": grantee,
                        "grantee_kind": kind,
                        "permission": level,
                    })
    return rows
