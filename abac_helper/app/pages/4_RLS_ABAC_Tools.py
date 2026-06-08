from __future__ import annotations

from typing import List, Optional

import streamlit as st

from config.settings import get_settings, qualify_table
from utils.auth import check_admin_access
from utils.rls_abac_manager import (
    PropagationAction,
    apply_propagation,
    build_propagation_plan,
)


def _escape_sql_string(value: str) -> str:
    """Escape a string for SQL literals."""
    return value.replace("'", "''")


def _render_function_builder() -> None:
    """Render the function creation form."""
    settings = get_settings()
    st.subheader("Create Access Filter Function")
    with st.form("create_access_function"):
        catalog = st.text_input("Catalog", value=settings.catalog)
        schema = st.text_input("Schema", value=settings.schema)
        function_name = st.text_input("Function Name", value="customer_access_filter")
        access_table = st.text_input(
            "Access Table",
            value=qualify_table(settings.access_table, settings),
            help="Fully qualified table that contains group access rules.",
        )
        group_check = st.selectbox("Group Check Function", ["is_member"])
        submit = st.form_submit_button("Create Function", type="primary")

    if submit and catalog and schema and function_name:
        function_fqn = f"{catalog}.{schema}.{function_name}"
        sql = f"""
            CREATE OR REPLACE FUNCTION {function_fqn}(customer_id INT)
            RETURN EXISTS (
                SELECT 1
                FROM {access_table} gca
                WHERE {group_check}(gca.group_name)
                  AND (gca.effective_date IS NULL OR gca.effective_date <= current_date())
                  AND (gca.expiration_date IS NULL OR gca.expiration_date > current_date())
                  AND (
                    (gca.customer_ids IS NULL) OR
                    (gca.access_type = 'INCLUDE' AND array_contains(gca.customer_ids, customer_id)) OR
                    (gca.access_type = 'EXCLUDE' AND NOT array_contains(gca.customer_ids, customer_id))
                  )
            )
        """
        st.code(sql.strip())
        from utils.db_connection import execute_update

        try:
            with st.spinner("Creating function..."):
                execute_update(sql)
            st.success("Function created.")
        except Exception as exc:
            st.error("Failed to create function.")
            st.exception(exc)


def _render_policy_builder() -> None:
    """Render the policy creation form."""
    settings = get_settings()
    st.subheader("Create Tag-Based Row Filter Policy")
    with st.form("create_policy"):
        catalog = st.text_input("Catalog", value=settings.catalog, key="policy_catalog")
        schema = st.text_input("Schema", value=settings.schema, key="policy_schema")
        policy_name = st.text_input("Policy Name", value="customer_access_policy")
        comment = st.text_input(
            "Policy Comment",
            value="Apply customer_id row filtering to tagged columns",
        )
        function_fqn = st.text_input(
            "Filter Function",
            value=f"{settings.catalog}.{settings.schema}.customer_access_filter",
        )
        principal = st.text_input("Principal", value="account users")
        tag_name = st.text_input("Tag Name", value="secure_contracts")
        tag_value = st.text_input("Tag Value", value="true")
        submit = st.form_submit_button("Generate SQL", type="primary")

    if submit and catalog and schema and policy_name:
        policy_fqn = f"{catalog}.{schema}.{policy_name}"
        sql = f"""
            CREATE OR REPLACE POLICY {policy_fqn}
            ON SCHEMA {catalog}.{schema}
            COMMENT '{_escape_sql_string(comment)}'
            ROW FILTER {function_fqn}
            TO `{_escape_sql_string(principal)}`
            FOR TABLES
            MATCH COLUMNS hasTagValue('{_escape_sql_string(tag_name)}', '{_escape_sql_string(tag_value)}') AS cust_col
            USING COLUMNS (cust_col)
        """
        st.code(sql.strip())
        if st.button("Create Policy"):
            from utils.db_connection import execute_update

            execute_update(sql)
            st.success("Policy created.")


def _render_propagation() -> None:
    """Render the tag propagation helper."""
    st.subheader("Propagate Tags to Columns")
    with st.form("propagate_tags"):
        parent_tag = st.text_input("Parent Tag Name", value="secure_contracts")
        required_parent_tag = st.text_input("Parent Tag Value", value="true")
        column_name = st.text_input("Column Name", value="customer_id")
        column_tag_name = st.text_input("Column Tag Name", value="secure_contracts")
        column_tag_value = st.text_input("Column Tag Value", value="true")
        target_catalog = st.text_input("Target Catalog (optional)")
        target_schema = st.text_input("Target Schema (optional)")
        dry_run = st.checkbox("Dry Run", value=True)
        submit = st.form_submit_button("Build Plan", type="primary")

    if submit:
        actions = build_propagation_plan(
            parent_tag_name=parent_tag,
            required_parent_tag=required_parent_tag,
            column_name=column_name,
            column_tag_name=column_tag_name,
            column_tag_value=column_tag_value,
            target_catalog=target_catalog or None,
            target_schema=target_schema or None,
        )
        _render_actions(actions, dry_run)


def _render_actions(actions: List[PropagationAction], dry_run: bool) -> None:
    """Render planned actions and allow execution."""
    st.markdown(f"Planned actions: {len(actions)}")
    if not actions:
        st.info("No columns matched the propagation criteria.")
        return
    st.dataframe(
        [
            {
                "table": action.table_fqn,
                "column": action.column_name,
                "tag": f"{action.tag_name}={action.tag_value}",
            }
            for action in actions
        ],
        use_container_width=True,
        hide_index=True,
    )
    with st.expander("SQL Preview"):
        for action in actions[:50]:
            st.code(action.sql)
        if len(actions) > 50:
            st.caption("Preview limited to first 50 actions.")
    if dry_run:
        st.info("Dry run enabled. No changes applied.")
        return
    if st.button("Apply Tags"):
        apply_propagation(actions)
        st.success(f"Applied {len(actions)} tag updates.")


def render_page() -> None:
    """Render the RLS and ABAC tooling page."""
    settings = get_settings()
    st.set_page_config(
        page_title=f"{settings.app_title} - RLS & ABAC Tools",
        page_icon=settings.page_icon,
        layout="wide",
    )
    st.title("RLS & ABAC Tools")
    if not check_admin_access():
        st.error("Access denied")
        st.stop()

    st.markdown(
        "Use this page to create row filter functions, apply tag-based policies, and "
        "propagate governed tags to matching columns."
    )
    _render_function_builder()
    st.divider()
    _render_policy_builder()
    st.divider()
    _render_propagation()


render_page()
