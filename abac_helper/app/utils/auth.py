from __future__ import annotations

from typing import Mapping, Optional

import streamlit as st

from config.settings import get_settings
from utils.db_connection import execute_query


def get_current_user_email() -> str:
    """Return the current user's email from forwarded headers."""
    email: Optional[str] = None
    try:
        headers: Mapping[str, str] = st.context.headers or {}
        email = headers.get("X-Forwarded-Email")
    except Exception:
        email = None
    if not email:
        try:
            user_info = st.experimental_user  # type: ignore[attr-defined]
            email = user_info.get("email") if isinstance(user_info, Mapping) else None
        except Exception:
            email = None
    return email or "unknown"


def check_admin_access() -> bool:
    """Check whether the current user is in the admin group."""
    settings = get_settings()
    group_name = settings.admin_group.replace("'", "''")
    try:
        query = f"SELECT is_member('{group_name}') AS is_admin"
        result = execute_query(query)
    except Exception as exc:
        st.error("Admin check failed while executing SQL.")
        st.exception(exc)
        return False
    if not result:
        return False
    return bool(result[0].get("is_admin"))
