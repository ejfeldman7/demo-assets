from __future__ import annotations

from typing import Dict, List, Optional

from config.settings import get_settings, qualify_table
from utils.auth import get_current_user_email
from utils.db_connection import execute_query, execute_update


def log_action(
    action_type: str,
    object_type: str,
    object_name: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Log an action to the audit table."""
    settings = get_settings()
    user = get_current_user_email()
    query = f"""
        INSERT INTO {qualify_table(settings.audit_table, settings)}
        (timestamp, user, action_type, object_type, object_name, old_value, new_value, notes)
        VALUES (
            CURRENT_TIMESTAMP(),
            :user,
            :action_type,
            :object_type,
            :object_name,
            :old_value,
            :new_value,
            :notes
        )
    """
    execute_update(
        query,
        {
            "user": user,
            "action_type": action_type,
            "object_type": object_type,
            "object_name": object_name,
            "old_value": old_value,
            "new_value": new_value,
            "notes": notes,
        },
    )


def get_audit_log(filters: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
    """Retrieve audit log entries with optional filters."""
    settings = get_settings()
    query = f"""
        SELECT
            timestamp,
            user,
            action_type,
            object_type,
            object_name,
            old_value,
            new_value,
            notes
        FROM {qualify_table(settings.audit_table, settings)}
        WHERE 1 = 1
    """
    params: Dict[str, object] = {}
    if filters:
        if filters.get("start_date"):
            query += " AND timestamp >= :start_date"
            params["start_date"] = filters["start_date"]
        if filters.get("end_date"):
            query += " AND timestamp <= :end_date"
            params["end_date"] = filters["end_date"]
        if filters.get("user"):
            query += " AND user = :user"
            params["user"] = filters["user"]
        if filters.get("action_type"):
            actions = list(filters["action_type"])
            placeholders = []
            for idx, action in enumerate(actions):
                key = f"action_type_{idx}"
                placeholders.append(f":{key}")
                params[key] = action
            query += f" AND action_type IN ({', '.join(placeholders)})"
        if filters.get("object_type"):
            query += " AND object_type = :object_type"
            params["object_type"] = filters["object_type"]
    query += " ORDER BY timestamp DESC LIMIT 1000"
    return execute_query(query, params)
