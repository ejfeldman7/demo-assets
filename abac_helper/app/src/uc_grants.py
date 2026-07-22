"""
uc_grants.py — Unity Catalog grants fetching.

UC uses a different API from workspace-level permissions:
  WorkspaceClient().grants.get(securable_type, full_name)
returns PermissionsList with a list of PrivilegeAssignment objects.

We default to Schema-level enumeration (Catalog -> Schema) to avoid the
combinatorial explosion of listing every table/volume.  An optional drill-down
to table/volume level is supported via the drill_down_schema parameter.
"""

from __future__ import annotations

import logging
from typing import Optional

from databricks.sdk.service.catalog import SecurableType

from src.auth import get_client, get_sp_client

log = logging.getLogger(__name__)



def get_uc_acl_entries(
    securable_type: str,
    full_name: str,
) -> list[dict]:
    """Return normalised ACL entries for a UC securable."""
    for client_fn, label in [(get_client, "user"), (get_sp_client, "SP")]:
        try:
            w = client_fn()
            result = w.grants.get(SecurableType(securable_type.upper()), full_name)
            entries: list[dict] = []
            for assignment in result.privilege_assignments or []:
                principal = assignment.principal or ""
                for priv_obj in assignment.privileges or []:
                    if hasattr(priv_obj, "privilege"):
                        priv = priv_obj.privilege.value if priv_obj.privilege else None
                    elif hasattr(priv_obj, "value"):
                        priv = priv_obj.value
                    else:
                        priv = str(priv_obj)
                    if not priv:
                        continue
                    entries.append({
                        "principal_type": "unknown",
                        "principal": principal,
                        "permission": priv,
                    })
            return entries
        except Exception as exc:
            exc_str = str(exc)
            if "required scopes" in exc_str and label == "user":
                log.info("User token lacks scope for UC grants.get(%s), falling back to SP", securable_type)
                continue
            log.warning("UC grants.get(%s, %s) failed (%s): %s", securable_type, full_name, label, exc)
            return []
    return []


def _uc_list_with_fallback(fn_user, fn_sp, label: str) -> list:
    """Try with user client first; fall back to SP on scope errors."""
    try:
        return fn_user()
    except Exception as exc:
        if "required scopes" in str(exc):
            log.info("User token lacks scope for %s, falling back to SP", label)
            try:
                return fn_sp()
            except Exception as exc2:
                log.warning("SP fallback failed for %s: %s", label, exc2)
                return []
        log.warning("Failed to %s: %s", label, exc)
        return []


def list_catalogs() -> list[str]:
    return _uc_list_with_fallback(
        lambda: [c.full_name or c.name for c in get_client().catalogs.list() if c.name],
        lambda: [c.full_name or c.name for c in get_sp_client().catalogs.list() if c.name],
        "list catalogs",
    )


def list_schemas(catalog_name: str) -> list[str]:
    def _list(w):
        return [
            s.full_name or f"{catalog_name}.{s.name}"
            for s in w.schemas.list(catalog_name=catalog_name)
            if s.name
        ]
    return _uc_list_with_fallback(
        lambda: _list(get_client()),
        lambda: _list(get_sp_client()),
        f"list schemas in {catalog_name}",
    )


def list_tables(schema_full_name: str) -> list[str]:
    catalog, schema = schema_full_name.split(".", 1) if "." in schema_full_name else ("", schema_full_name)
    def _list(w):
        return [
            t.full_name or f"{schema_full_name}.{t.name}"
            for t in w.tables.list(catalog_name=catalog, schema_name=schema)
            if t.name
        ]
    return _uc_list_with_fallback(
        lambda: _list(get_client()),
        lambda: _list(get_sp_client()),
        f"list tables in {schema_full_name}",
    )


def list_volumes(schema_full_name: str) -> list[str]:
    catalog, schema = schema_full_name.split(".", 1) if "." in schema_full_name else ("", schema_full_name)
    def _list(w):
        return [
            f"{schema_full_name}.{v.name}"
            for v in w.volumes.list(catalog_name=catalog, schema_name=schema)
            if v.name
        ]
    return _uc_list_with_fallback(
        lambda: _list(get_client()),
        lambda: _list(get_sp_client()),
        f"list volumes in {schema_full_name}",
    )


def fetch_all_uc_acls(
    drill_down_schema: Optional[str] = None,
) -> list[tuple[str, str, list[dict]]]:
    """Fetch UC ACL entries for all catalogs and schemas (+ optional drill-down)."""
    results: list[tuple[str, str, list[dict]]] = []

    catalogs = list_catalogs()
    for cat in catalogs:
        acl = get_uc_acl_entries("catalog", cat)
        results.append(("Catalog", cat, acl))

        schemas = list_schemas(cat)
        for schema_fn in schemas:
            acl = get_uc_acl_entries("schema", schema_fn)
            results.append(("Schema", schema_fn, acl))

            if drill_down_schema and schema_fn == drill_down_schema:
                for table_fn in list_tables(schema_fn):
                    acl = get_uc_acl_entries("table", table_fn)
                    results.append(("Table", table_fn, acl))
                for vol_fn in list_volumes(schema_fn):
                    acl = get_uc_acl_entries("volume", vol_fn)
                    results.append(("Volume", vol_fn, acl))

    return results
