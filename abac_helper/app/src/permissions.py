"""
permissions.py — Permission fetching for all workspace object types.

Uses WorkspaceClient().permissions.get(resource_type, resource_id) for workspace
objects and delegates to uc_grants.py for Unity Catalog.

Each public function returns a list of normalised ACL dicts:
  {"principal_type": "user"|"group"|"service_principal",
   "principal": "<login or group name>",
   "permission": "<PERMISSION_LEVEL>"}

Caching strategy:
  - @st.cache_data(ttl=300) is applied to the *listing* functions (list_clusters,
    list_jobs, etc.) because listing all objects is expensive.
  - Per-ACL fetching (get_cluster_acl, etc.) is NOT cached because the caller
    only invokes these for a specific user selection.
"""

from __future__ import annotations

import logging

import streamlit as st

from src.auth import get_client, get_sp_client

log = logging.getLogger(__name__)


def _get_client_with_fallback():
    """Return the user client, falling back to the SP client on scope errors."""
    return get_client()


def _list_with_fallback(fn_user, fn_sp, label: str) -> list:
    """Try listing with the user client; if it fails due to missing scopes, retry with SP."""
    try:
        return fn_user()
    except Exception as exc:
        if "does not have required scopes" in str(exc) or "required scopes" in str(exc):
            log.info("User token lacks scope for %s, falling back to SP client", label)
            try:
                return fn_sp()
            except Exception as exc2:
                log.warning("SP fallback also failed for %s: %s", label, exc2)
                return []
        log.warning("Failed to list %s: %s", label, exc)
        return []


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise_workspace_acl(acl_response) -> list[dict]:
    """Convert SDK AccessControlResponse list to our canonical dict format."""
    entries: list[dict] = []
    for acl in (acl_response or []):
        if acl.user_name:
            principal_type = "user"
            principal = acl.user_name
        elif acl.group_name:
            principal_type = "group"
            principal = acl.group_name
        elif acl.service_principal_name:
            principal_type = "service_principal"
            principal = str(acl.service_principal_name)
        else:
            continue

        for perm in (acl.all_permissions or []):
            level = perm.permission_level.value if perm.permission_level else str(perm.permission_level)
            entries.append({
                "principal_type": principal_type,
                "principal": principal,
                "permission": level,
            })
    return entries


# ---------------------------------------------------------------------------
# Listing functions — cached for 5 minutes
# ---------------------------------------------------------------------------

def _list_clusters_with(w) -> list[dict]:
    return [
        {"id": c.cluster_id, "name": c.cluster_name or c.cluster_id}
        for c in w.clusters.list()
        if c.cluster_id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_clusters() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_clusters_with(get_client()),
        lambda: _list_clusters_with(get_sp_client()),
        "clusters",
    )


def _list_cluster_policies_with(w) -> list[dict]:
    return [
        {"id": p.policy_id, "name": p.name or p.policy_id}
        for p in w.cluster_policies.list()
        if p.policy_id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_cluster_policies() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_cluster_policies_with(get_client()),
        lambda: _list_cluster_policies_with(get_sp_client()),
        "cluster policies",
    )


def _list_jobs_with(w) -> list[dict]:
    return [
        {"id": str(j.job_id), "name": (j.settings.name if j.settings else None) or str(j.job_id)}
        for j in w.jobs.list()
        if j.job_id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_jobs() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_jobs_with(get_client()),
        lambda: _list_jobs_with(get_sp_client()),
        "jobs",
    )


def _list_pipelines_with(w) -> list[dict]:
    return [
        {"id": p.pipeline_id, "name": p.name or p.pipeline_id}
        for p in w.pipelines.list_pipelines()
        if p.pipeline_id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_pipelines() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_pipelines_with(get_client()),
        lambda: _list_pipelines_with(get_sp_client()),
        "pipelines",
    )


def _list_warehouses_with(w) -> list[dict]:
    return [
        {"id": wh.id, "name": wh.name or wh.id}
        for wh in w.warehouses.list()
        if wh.id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_warehouses() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_warehouses_with(get_client()),
        lambda: _list_warehouses_with(get_sp_client()),
        "SQL warehouses",
    )


def _list_dashboards_with(w) -> list[dict]:
    return [
        {"id": d.dashboard_id, "name": d.display_name or d.dashboard_id}
        for d in w.lakeview.list()
        if d.dashboard_id
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_dashboards() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_dashboards_with(get_client()),
        lambda: _list_dashboards_with(get_sp_client()),
        "dashboards",
    )


def _list_apps_with(w) -> list[dict]:
    return [
        {"id": a.name, "name": a.name}
        for a in w.apps.list()
        if a.name
    ]


@st.cache_data(ttl=300, show_spinner=False)
def list_apps() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_apps_with(get_client()),
        lambda: _list_apps_with(get_sp_client()),
        "apps",
    )


def _list_genie_spaces_with(w) -> list[dict]:
    resp = w.genie.list_spaces()
    spaces = []
    for space in getattr(resp, "spaces", None) or []:
        spaces.append({"id": space.space_id, "name": space.title or space.space_id})
    return spaces


@st.cache_data(ttl=300, show_spinner=False)
def list_genie_spaces() -> list[dict]:
    return _list_with_fallback(
        lambda: _list_genie_spaces_with(get_client()),
        lambda: _list_genie_spaces_with(get_sp_client()),
        "Genie spaces",
    )


# ---------------------------------------------------------------------------
# ACL-fetching functions (one per resource type)
# ---------------------------------------------------------------------------

def _get_workspace_acl(resource_type: str, resource_id: str) -> list[dict]:
    """Fetch ACL for a workspace object, falling back to SP on scope errors."""
    for client_fn, label in [(get_client, "user"), (get_sp_client, "SP")]:
        try:
            w = client_fn()
            result = w.permissions.get(resource_type, resource_id)
            return _normalise_workspace_acl(result.access_control_list)
        except Exception as exc:
            exc_str = str(exc)
            if "required scopes" in exc_str and label == "user":
                log.info("User token lacks scope for permissions.get(%s), falling back to SP", resource_type)
                continue
            if "does not have" in exc_str and "permission" in exc_str.lower() and label == "user":
                log.info("User lacks permission for %s/%s, trying SP", resource_type, resource_id)
                continue
            log.warning("permissions.get(%s, %s) failed (%s): %s", resource_type, resource_id, label, exc)
            return []
    return []


def get_cluster_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Cluster", c["name"], _get_workspace_acl("clusters", c["id"]))
        for c in list_clusters()
    ]


def get_cluster_policy_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Cluster Policy", p["name"], _get_workspace_acl("cluster-policies", p["id"]))
        for p in list_cluster_policies()
    ]


def get_job_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Job", j["name"], _get_workspace_acl("jobs", j["id"]))
        for j in list_jobs()
    ]


def get_pipeline_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Pipeline", p["name"], _get_workspace_acl("pipelines", p["id"]))
        for p in list_pipelines()
    ]


def get_warehouse_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("SQL Warehouse", wh["name"], _get_workspace_acl("sql/warehouses", wh["id"]))
        for wh in list_warehouses()
    ]


def get_dashboard_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Dashboard", d["name"], _get_workspace_acl("dashboards", d["id"]))
        for d in list_dashboards()
    ]


def get_app_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("App", a["name"], _get_workspace_acl("apps", a["id"]))
        for a in list_apps()
    ]


def get_genie_acls() -> list[tuple[str, str, list[dict]]]:
    return [
        ("Genie Space", g["name"], _get_workspace_acl("genie", g["id"]))
        for g in list_genie_spaces()
    ]
