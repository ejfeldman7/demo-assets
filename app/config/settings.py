from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


DEFAULT_WORKSPACE_HOST = "<WORKSPACE URL>.cloud.databricks.com"
DEFAULT_WAREHOUSE_ID = "<WAREHOUSE ID>"
DEFAULT_CATALOG = "<CATALOG>"
DEFAULT_SCHEMA = "<SCHEMA>"
DEFAULT_ACCESS_TABLE = "<update>"
DEFAULT_AUDIT_TABLE = "<update>"
DEFAULT_ADMIN_GROUP = "<update>"
DEFAULT_APP_TITLE = "Unity Catalog Access Management"
DEFAULT_PAGE_ICON = "UC"


@dataclass(frozen=True)
class AppSettings:
    """Resolved configuration for the Streamlit application."""

    server_hostname: str
    http_path: str
    warehouse_id: str
    catalog: str
    schema: str
    access_table: str
    audit_table: str
    admin_group: str
    app_title: str
    page_icon: str


def _normalize_hostname(value: str) -> str:
    """Normalize a hostname or workspace URL into a bare hostname."""
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return parsed.netloc
    return value.replace("https://", "").replace("http://", "")


def _get_env(name: str, default: Optional[str]) -> str:
    """Read an environment variable with a fallback value."""
    return os.getenv(name, default or "").strip()


def get_settings() -> AppSettings:
    """Return the resolved application settings."""
    warehouse_id = _get_env("DATABRICKS_WAREHOUSE_ID", DEFAULT_WAREHOUSE_ID)
    http_path = _get_env("DATABRICKS_HTTP_PATH", f"/sql/1.0/warehouses/{warehouse_id}")
    server_hostname = _normalize_hostname(
        _get_env("DATABRICKS_SERVER_HOSTNAME", DEFAULT_WORKSPACE_HOST)
    )
    return AppSettings(
        server_hostname=server_hostname,
        http_path=http_path,
        warehouse_id=warehouse_id,
        catalog=_get_env("CATALOG_NAME", DEFAULT_CATALOG),
        schema=_get_env("SCHEMA_NAME", DEFAULT_SCHEMA),
        access_table=_get_env("ACCESS_TABLE", DEFAULT_ACCESS_TABLE),
        audit_table=_get_env("AUDIT_TABLE", DEFAULT_AUDIT_TABLE),
        admin_group=_get_env("ADMIN_GROUP", DEFAULT_ADMIN_GROUP),
        app_title=_get_env("APP_TITLE", DEFAULT_APP_TITLE),
        page_icon=_get_env("PAGE_ICON", DEFAULT_PAGE_ICON),
    )


def qualify_table(table_name: str, settings: AppSettings) -> str:
    """Return a fully qualified table name."""
    if "." in table_name:
        return table_name
    return f"{settings.catalog}.{settings.schema}.{table_name}"
