from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

from config.settings import get_settings


def _get_header_token() -> Optional[str]:
    """Read the forwarded access token from Databricks App headers."""
    try:
        headers: Mapping[str, str] = st.context.headers or {}
    except Exception:
        return None
    return headers.get("X-Forwarded-Access-Token")


def _get_connection() -> sql.Connection:
    """Create a Databricks SQL connection using OAuth or forwarded token."""
    settings = get_settings()
    if not settings.server_hostname or not settings.http_path:
        raise ValueError("Databricks SQL configuration is incomplete.")
    sdk_cfg = Config()
    access_token = _get_header_token() or sdk_cfg.token
    connect_kwargs: Dict[str, Any] = {
        "server_hostname": settings.server_hostname,
        "http_path": settings.http_path,
        "use_cloud_fetch": False,
    }
    try:
        return sql.connect(**connect_kwargs, credentials_provider=lambda: sdk_cfg.authenticate)
    except Exception as exc:
        if access_token:
            try:
                return sql.connect(**connect_kwargs, access_token=access_token)
            except Exception as token_exc:  # pragma: no cover - runtime error surface
                raise RuntimeError(
                    "SQL connection failed using both OAuth and access_token. "
                    f"server_hostname={settings.server_hostname}, "
                    f"http_path={settings.http_path}"
                ) from token_exc
        raise RuntimeError(
            "SQL connection failed using OAuth. "
            f"server_hostname={settings.server_hostname}, "
            f"http_path={settings.http_path}"
        ) from exc


def _fetch_all(cursor: sql.client.Cursor) -> List[Dict[str, Any]]:
    """Return the cursor results as a list of dicts."""
    columns = [desc[0] for desc in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def execute_query(query: str, params: Optional[Mapping[str, object]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query and return rows as dictionaries."""
    with _get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params or {})
            return _fetch_all(cursor)


def execute_update(query: str, params: Optional[Mapping[str, object]] = None) -> int:
    """Execute a mutation query and return the affected row count."""
    with _get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params or {})
            connection.commit()
            return cursor.rowcount or 0


def execute_many(query: str, rows: Iterable[Mapping[str, object]]) -> int:
    """Execute a parameterized query for multiple rows."""
    total = 0
    with _get_connection() as connection:
        with connection.cursor() as cursor:
            for row in rows:
                cursor.execute(query, row)
                total += cursor.rowcount or 0
            connection.commit()
    return total
