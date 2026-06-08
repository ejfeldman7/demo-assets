from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional

from config.settings import get_settings, qualify_table
from utils.audit_logger import log_action
from utils.auth import get_current_user_email
from utils.db_connection import execute_query, execute_update
from utils.validators import normalize_customer_ids


def get_access_rules(filters: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
    """Return access rules with optional filters."""
    settings = get_settings()
    query = f"""
        SELECT
            id,
            group_name,
            customer_ids,
            access_type,
            effective_date,
            expiration_date,
            notes,
            created_by,
            created_at,
            modified_by,
            modified_at
        FROM {qualify_table(settings.access_table, settings)}
        WHERE 1 = 1
    """
    params: Dict[str, object] = {}
    if filters:
        if filters.get("group_name"):
            query += " AND group_name = :group_name"
            params["group_name"] = filters["group_name"]
        if filters.get("status") == "active":
            query += " AND (expiration_date IS NULL OR expiration_date > CURRENT_DATE())"
        elif filters.get("status") == "expired":
            query += " AND expiration_date <= CURRENT_DATE()"
        if filters.get("customer_id"):
            query += " AND array_contains(customer_ids, :customer_id)"
            params["customer_id"] = int(filters["customer_id"])
    query += " ORDER BY group_name, effective_date DESC"
    return execute_query(query, params)


def add_access_rule(
    group_name: str,
    customer_ids: Iterable[int],
    access_type: str,
    effective_date: date,
    expiration_date: Optional[date],
    notes: Optional[str],
) -> bool:
    """Insert a new access rule."""
    settings = get_settings()
    user = get_current_user_email()
    normalized_ids = normalize_customer_ids(customer_ids)
    customer_ids_array = (
        f"array({','.join(map(str, normalized_ids))})" if normalized_ids else "NULL"
    )
    query = f"""
        INSERT INTO {qualify_table(settings.access_table, settings)}
        (group_name, customer_ids, access_type, effective_date, expiration_date,
         notes, created_by, created_at, modified_by, modified_at)
        VALUES (
            :group_name,
            {customer_ids_array},
            :access_type,
            :effective_date,
            :expiration_date,
            :notes,
            :user,
            CURRENT_TIMESTAMP(),
            :user,
            CURRENT_TIMESTAMP()
        )
    """
    params = {
        "group_name": group_name,
        "access_type": access_type,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "notes": notes,
        "user": user,
    }
    execute_update(query, params)
    log_action(
        action_type="INSERT",
        object_type="GROUP_ACCESS",
        object_name=group_name,
        new_value=f"customer_ids={normalized_ids}, access_type={access_type}",
        notes=notes,
    )
    return True


def update_access_rule(
    rule_id: int,
    group_name: str,
    customer_ids: Iterable[int],
    access_type: str,
    effective_date: date,
    expiration_date: Optional[date],
    notes: Optional[str],
) -> bool:
    """Update an existing access rule."""
    settings = get_settings()
    user = get_current_user_email()
    normalized_ids = normalize_customer_ids(customer_ids)
    customer_ids_array = (
        f"array({','.join(map(str, normalized_ids))})" if normalized_ids else "NULL"
    )
    query = f"""
        UPDATE {qualify_table(settings.access_table, settings)}
        SET
            group_name = :group_name,
            customer_ids = {customer_ids_array},
            access_type = :access_type,
            effective_date = :effective_date,
            expiration_date = :expiration_date,
            notes = :notes,
            modified_by = :user,
            modified_at = CURRENT_TIMESTAMP()
        WHERE id = :rule_id
    """
    params = {
        "rule_id": rule_id,
        "group_name": group_name,
        "access_type": access_type,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "notes": notes,
        "user": user,
    }
    execute_update(query, params)
    log_action(
        action_type="UPDATE",
        object_type="GROUP_ACCESS",
        object_name=str(rule_id),
        new_value=(
            f"group_name={group_name}, customer_ids={normalized_ids}, "
            f"access_type={access_type}"
        ),
        notes=notes,
    )
    return True


def expire_access_rule(rule_id: int) -> bool:
    """Expire a rule by setting its expiration date to today."""
    settings = get_settings()
    user = get_current_user_email()
    query = f"""
        UPDATE {qualify_table(settings.access_table, settings)}
        SET
            expiration_date = CURRENT_DATE(),
            modified_by = :user,
            modified_at = CURRENT_TIMESTAMP()
        WHERE id = :rule_id
    """
    execute_update(query, {"rule_id": rule_id, "user": user})
    log_action(
        action_type="EXPIRE",
        object_type="GROUP_ACCESS",
        object_name=str(rule_id),
        new_value="expired",
        notes="Manually expired via UI",
    )
    return True


def delete_access_rule(rule_id: int) -> bool:
    """Delete a rule that is already expired."""
    settings = get_settings()
    query = f"""
        DELETE FROM {qualify_table(settings.access_table, settings)}
        WHERE id = :rule_id
          AND expiration_date <= CURRENT_DATE()
    """
    row_count = execute_update(query, {"rule_id": rule_id})
    if row_count:
        log_action(
            action_type="DELETE",
            object_type="GROUP_ACCESS",
            object_name=str(rule_id),
            new_value="deleted",
            notes="Deleted via UI after expiration",
        )
    return row_count > 0
