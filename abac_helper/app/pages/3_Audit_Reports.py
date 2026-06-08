from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

import polars as pl
import streamlit as st

from config.settings import get_settings
from utils.access_manager import get_access_rules
from utils.audit_logger import get_audit_log
from utils.auth import check_admin_access
from utils.tag_manager import get_column_tag_coverage, get_table_tag_coverage, get_tag_options


def _build_access_summary(rule: dict) -> str:
    """Create a readable access summary for a rule."""
    access_type = str(rule.get("access_type", "")).upper()
    customer_ids = rule.get("customer_ids") or []
    customer_count = len(customer_ids) if isinstance(customer_ids, list) else 0
    if access_type == "INCLUDE":
        if not customer_ids:
            return "Access given as ADMIN, all customers available"
        return f"Access INCLUDED for {customer_count} customers, EXCLUDED for all others"
    if access_type == "EXCLUDE":
        if not customer_ids:
            return "Access EXCLUDED for all customers"
        return f"Access EXCLUDED for {customer_count} customers, PROVIDED for all others"
    return "Access rules not specified"


def _render_access_matrix() -> None:
    """Render access matrix views for current and recently expired rules."""
    st.subheader("Access Matrix")
    rules = get_access_rules()
    if not rules:
        st.info("No access rules available.")
        return
    df = pl.DataFrame(rules)
    for col_name in ("effective_date", "expiration_date"):
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Date))
    df = df.with_columns(
        pl.struct(["access_type", "customer_ids"])
        .map_elements(_build_access_summary, return_dtype=pl.Utf8)
        .alias("access_summary")
    )
    df = df.with_columns(
        pl.col("customer_ids")
        .map_elements(lambda ids: len(ids) if isinstance(ids, list) else 0, return_dtype=pl.Int64)
        .alias("customer_count")
    )
    today = date.today()
    current_df = df.filter(
        (pl.col("effective_date").is_null() | (pl.col("effective_date") <= today))
        & (pl.col("expiration_date").is_null() | (pl.col("expiration_date") > today))
    )
    expired_df = df.filter(
        pl.col("expiration_date").is_not_null()
        & (pl.col("expiration_date") >= today - timedelta(days=60))
        & (pl.col("expiration_date") <= today)
    )
    st.markdown("### Current Access Rules")
    if current_df.is_empty():
        st.info("No current access rules.")
    else:
        st.dataframe(
            current_df.select(
                [
                    "group_name",
                    "access_summary",
                    "access_type",
                    "customer_count",
                    "effective_date",
                    "expiration_date",
                    "notes",
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("### Expired in Last 60 Days")
    if expired_df.is_empty():
        st.info("No recently expired access rules.")
    else:
        st.dataframe(
            expired_df.select(
                [
                    "group_name",
                    "access_summary",
                    "access_type",
                    "customer_count",
                    "effective_date",
                    "expiration_date",
                    "notes",
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )



def _render_tag_coverage() -> None:
    """Render coverage statistics for tags."""
    settings = get_settings()
    st.subheader("Tag Coverage")
    with st.form("coverage_filters"):
        catalog = st.text_input("Catalog", value=settings.catalog)
        schema = st.text_input("Schema", value=settings.schema)
        options = get_tag_options(catalog, schema)
        option_labels = [f"{opt['tag_name']}={opt['tag_value']}" for opt in options]
        default_index = 0
        for idx, opt in enumerate(options):
            if opt["tag_name"] == "secure_contracts" and opt["tag_value"] == "true":
                default_index = idx
                break
        selection = st.selectbox(
            "Tag",
            option_labels if option_labels else ["secure_contracts=true"],
            index=default_index if option_labels else 0,
        )
        submit = st.form_submit_button("Update Coverage", type="primary")

    if selection:
        tag_name, tag_value = selection.split("=", maxsplit=1)
        table_stats = get_table_tag_coverage(catalog, schema, tag_name, tag_value)
        column_stats = get_column_tag_coverage(catalog, schema, tag_name, tag_value)
        table_percent = (
            (table_stats["tagged"] / table_stats["total"]) * 100 if table_stats["total"] else 0
        )
        column_percent = (
            (column_stats["tagged"] / column_stats["total"]) * 100 if column_stats["total"] else 0
        )

        st.markdown("### Table Coverage")
        st.metric("Tagged Tables", table_stats["tagged"])
        st.metric("Total Tables", table_stats["total"])
        st.metric("Coverage %", f"{table_percent:.1f}%")

        st.markdown("### Column Coverage")
        st.metric("Tagged Columns", column_stats["tagged"])
        st.metric("Total Columns", column_stats["total"])
        st.metric("Coverage %", f"{column_percent:.1f}%")


def _build_filters() -> Dict[str, object]:
    """Collect audit log filters from the sidebar."""
    st.sidebar.header("Filters")
    start_date = st.sidebar.date_input("Start Date", value=date.today() - timedelta(days=30))
    end_date = st.sidebar.date_input("End Date", value=date.today())
    action_filter = st.sidebar.multiselect(
        "Action Type",
        ["INSERT", "UPDATE", "DELETE", "EXPIRE", "TAG_APPLY", "TAG_REMOVE"],
    )
    user_filter = st.sidebar.text_input("User Email")
    object_filter = st.sidebar.selectbox(
        "Object Type",
        ["", "GROUP_ACCESS", "TABLE_TAG", "COLUMN_TAG"],
    )
    filters: Dict[str, object] = {
        "start_date": start_date,
        "end_date": end_date,
    }
    if action_filter:
        filters["action_type"] = action_filter
    if user_filter:
        filters["user"] = user_filter.strip()
    if object_filter:
        filters["object_type"] = object_filter
    return filters


def _render_change_history(filters: Dict[str, object]) -> None:
    """Render the change history tab."""
    st.subheader("Change History")
    audit_data = get_audit_log(filters)
    if not audit_data:
        st.info("No audit records found for the selected filters.")
        return
    df = pl.DataFrame(audit_data)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Changes", df.height)
    with col2:
        st.metric("Unique Users", df.select("user").n_unique())
    with col3:
        most_common = (
            df.select(pl.col("action_type").mode())
            .to_series()
            .to_list()
        )
        st.metric("Most Common Action", most_common[0] if most_common else "N/A")
    st.dataframe(df, use_container_width=True, hide_index=True)
    csv_data = df.write_csv()
    st.download_button(
        "Export to CSV",
        csv_data,
        "audit_log.csv",
        "text/csv",
        key="download-audit-csv",
    )


def render_page() -> None:
    """Render the Audit & Reports page."""
    settings = get_settings()
    st.set_page_config(
        page_title=f"{settings.app_title} - Audit & Reports",
        page_icon=settings.page_icon,
        layout="wide",
    )
    st.title("Audit & Reports")
    if not check_admin_access():
        st.error("Access denied")
        st.stop()

    filters = _build_filters()
    tab1, tab2, tab3 = st.tabs(
        ["Change History", "Access Matrix", "Tag Coverage"]
    )
    with tab1:
        _render_change_history(filters)
    with tab2:
        _render_access_matrix()
    with tab3:
        _render_tag_coverage()


render_page()
