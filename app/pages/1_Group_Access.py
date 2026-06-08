from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

import polars as pl
import streamlit as st

from config.settings import get_settings
from utils.access_manager import add_access_rule, delete_access_rule, expire_access_rule, get_access_rules, update_access_rule
from utils.auth import check_admin_access
from utils.validators import parse_customer_ids, validate_dates


def _build_filters() -> Dict[str, object]:
    """Collect filters from the sidebar."""
    st.sidebar.header("Filters")
    group_filter = st.sidebar.text_input("Group Name")
    customer_id_filter = st.sidebar.text_input("Customer ID")
    status_filter = st.sidebar.selectbox("Status", ["all", "active", "expired"])
    filters: Dict[str, object] = {}
    if group_filter:
        filters["group_name"] = group_filter.strip()
    if customer_id_filter:
        try:
            filters["customer_id"] = int(customer_id_filter)
        except ValueError:
            st.sidebar.warning("Customer ID must be an integer.")
    if status_filter != "all":
        filters["status"] = status_filter
    return filters


def _rules_to_dataframe(rules: List[Dict[str, object]]) -> pl.DataFrame:
    """Convert rule dictionaries to a Polars DataFrame."""
    if not rules:
        return pl.DataFrame()
    df = pl.DataFrame(rules)
    for col_name in ("effective_date", "expiration_date"):
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Date))
    if "customer_ids" in df.columns:
        df = df.with_columns(
            pl.col("customer_ids")
            .map_elements(
                lambda ids: _format_customer_ids(ids),
                return_dtype=pl.Utf8,
            )
            .alias("customer_ids_display")
        )
    return df


def _format_customer_ids(ids: object) -> str:
    """Format customer IDs for display."""
    if ids is None:
        return ""
    if isinstance(ids, list):
        items = ids
    else:
        try:
            items = list(ids)
        except TypeError:
            return ""
    if not items:
        return ""
    preview = ", ".join(map(str, items[:5]))
    suffix = f" (+{len(items) - 5} more)" if len(items) > 5 else ""
    return f"{preview}{suffix}"


def _render_metrics(df: pl.DataFrame) -> None:
    """Render summary metrics for the access rules."""
    total_rules = df.height
    today = date.today()
    if df.is_empty():
        active_count = 0
        expiring_soon = 0
    else:
        active_mask = (pl.col("expiration_date").is_null()) | (
            pl.col("expiration_date") > today
        )
        expiring_mask = (pl.col("expiration_date").is_not_null()) & (
            pl.col("expiration_date") <= today + timedelta(days=7)
        )
        active_count = df.filter(active_mask).height
        expiring_soon = df.filter(expiring_mask).height
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Rules", total_rules)
    with col2:
        st.metric("Active Rules", active_count)
    with col3:
        st.metric("Expiring Soon (7d)", expiring_soon)


def _render_add_form() -> None:
    """Render the add access rule form."""
    with st.expander("Add New Rule", expanded=False):
        with st.form("add_rule_form"):
            st.subheader("New Access Rule")
            group_name = st.text_input("Group Name*", placeholder="e.g., Group_A")
            customer_ids_input = st.text_input(
                "Customer IDs*",
                placeholder="e.g., 100,101,102 or 100-110",
                help="Enter comma-separated IDs or ranges (e.g., 1,2,5-10).",
            )
            access_type = st.radio("Access Type*", ["INCLUDE", "EXCLUDE"], horizontal=True)
            effective_date = st.date_input("Effective Date*", value=date.today())
            expiration_date = st.date_input("Expiration Date", value=None)
            notes = st.text_area("Notes")
            submit = st.form_submit_button("Save", type="primary")

            if submit:
                errors: List[str] = []
                if not group_name:
                    errors.append("Group name is required.")
                ids_valid, ids_value = parse_customer_ids(customer_ids_input)
                if not ids_valid:
                    errors.append(str(ids_value))
                date_valid, date_error = validate_dates(effective_date, expiration_date)
                if not date_valid:
                    errors.append(date_error or "Invalid dates.")
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    if add_access_rule(
                        group_name=group_name.strip(),
                        customer_ids=ids_value if isinstance(ids_value, list) else [],
                        access_type=access_type,
                        effective_date=effective_date,
                        expiration_date=expiration_date,
                        notes=notes,
                    ):
                        st.success("Access rule added successfully.")
                        st.rerun()
                    else:
                        st.error("Failed to add access rule.")


def _render_edit_form(rule: Dict[str, object]) -> None:
    """Render the edit form for a selected rule."""
    with st.expander("Edit Selected Rule", expanded=False):
        with st.form("edit_rule_form"):
            st.subheader(f"Edit Rule {rule.get('id')}")
            group_name = st.text_input(
                "Group Name*",
                value=str(rule.get("group_name") or ""),
            )
            customer_ids = _normalize_ids(rule.get("customer_ids"))
            customer_ids_str = ", ".join(map(str, customer_ids))
            effective_value = rule.get("effective_date") or date.today()
            expiration_value = rule.get("expiration_date")
            if hasattr(effective_value, "date"):
                effective_value = effective_value.date()
            if hasattr(expiration_value, "date"):
                expiration_value = expiration_value.date()
            customer_ids_input = st.text_input("Customer IDs*", value=customer_ids_str)
            access_type = st.radio(
                "Access Type*",
                ["INCLUDE", "EXCLUDE"],
                index=0 if rule.get("access_type") == "INCLUDE" else 1,
                horizontal=True,
            )
            effective_date = st.date_input(
                "Effective Date*",
                value=effective_value,
            )
            expiration_date = st.date_input(
                "Expiration Date",
                value=expiration_value,
            )
            notes = st.text_area("Notes", value=rule.get("notes") or "")
            submit = st.form_submit_button("Update", type="primary")

            if submit:
                errors: List[str] = []
                if not group_name:
                    errors.append("Group name is required.")
                ids_valid, ids_value = parse_customer_ids(customer_ids_input)
                if not ids_valid:
                    errors.append(str(ids_value))
                date_valid, date_error = validate_dates(effective_date, expiration_date)
                if not date_valid:
                    errors.append(date_error or "Invalid dates.")
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    if update_access_rule(
                        rule_id=int(rule.get("id")),
                        group_name=group_name.strip(),
                        customer_ids=ids_value if isinstance(ids_value, list) else [],
                        access_type=access_type,
                        effective_date=effective_date,
                        expiration_date=expiration_date,
                        notes=notes,
                    ):
                        st.success("Access rule updated successfully.")
                        st.rerun()
                    else:
                        st.error("Failed to update access rule.")


def _render_actions(rules: List[Dict[str, object]]) -> None:
    """Render action controls for existing rules."""
    if not rules:
        return
    st.subheader("Actions")
    ids = [rule["id"] for rule in rules]
    selected_id = st.selectbox("Select rule", ids)
    selected_rule = next((rule for rule in rules if rule["id"] == selected_id), None)
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Expire", type="secondary"):
            if expire_access_rule(int(selected_id)):
                st.success("Rule expired successfully.")
                st.rerun()
            else:
                st.error("Unable to expire rule.")
    with col2:
        if st.button("Delete", type="secondary"):
            if delete_access_rule(int(selected_id)):
                st.success("Rule deleted successfully.")
                st.rerun()
            else:
                st.warning("Rules can only be deleted after expiration.")
    with col3:
        st.caption("Edit the rule below.")
    if selected_rule:
        _render_edit_form(selected_rule)


def _normalize_ids(value: object) -> List[int]:
    """Normalize customer IDs into a list of integers."""
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    try:
        return [int(item) for item in list(value)]
    except TypeError:
        return []


def render_page() -> None:
    """Render the Group Access Management page."""
    settings = get_settings()
    st.set_page_config(
        page_title=f"{settings.app_title} - Group Access",
        page_icon=settings.page_icon,
        layout="wide",
    )
    st.title("Group Access Management")
    if not check_admin_access():
        st.error("Access denied")
        st.stop()

    filters = _build_filters()
    rules = get_access_rules(filters)
    df = _rules_to_dataframe(rules)

    _render_metrics(df)
    _render_add_form()

    st.subheader("Current Access Rules")
    if df.is_empty():
        st.info("No access rules found for the selected filters.")
    else:
        display_cols = [
            "id",
            "group_name",
            "customer_ids_display",
            "access_type",
            "effective_date",
            "expiration_date",
            "notes",
        ]
        st.dataframe(
            df.select([col for col in display_cols if col in df.columns]),
            use_container_width=True,
            hide_index=True,
        )
    _render_actions(rules)


render_page()
