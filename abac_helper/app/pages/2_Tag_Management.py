from __future__ import annotations

from typing import List, Optional

import polars as pl
import streamlit as st

from config.settings import get_settings
from utils.auth import check_admin_access
from utils.tag_manager import (
    apply_column_tag,
    apply_table_tag,
    get_catalogs,
    get_column_tags,
    get_table_columns,
    get_schemas,
    get_table_tags,
    get_tables,
    remove_table_tag,
)


def _select_hierarchy() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Render the catalog/schema/table selectors and return chosen values."""
    st.sidebar.header("Browse Objects")
    catalogs = get_catalogs()
    selected_catalog = st.sidebar.selectbox("Catalog", catalogs) if catalogs else None
    selected_schema = None
    selected_table = None
    if selected_catalog:
        schemas = get_schemas(selected_catalog)
        selected_schema = st.sidebar.selectbox("Schema", schemas) if schemas else None
        if selected_schema:
            tables = get_tables(selected_catalog, selected_schema)
            selected_table = st.sidebar.selectbox("Table", tables) if tables else None
    return selected_catalog, selected_schema, selected_table


def _render_table_tags(catalog: str, schema: str, table: str) -> None:
    """Render the table tag section."""
    st.markdown("### Table Tags")
    table_tags = get_table_tags(catalog, schema, table)
    if table_tags:
        for tag in table_tags:
            col1, col2, col3 = st.columns([3, 3, 1])
            with col1:
                st.text(tag.get("tag_name", ""))
            with col2:
                st.text(tag.get("tag_value", ""))
            with col3:
                if st.button("Remove", key=f"remove_{tag.get('tag_name')}"):
                    success, msg = remove_table_tag(catalog, schema, table, str(tag.get("tag_name", "")))
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
    else:
        st.info("No tags applied to this table.")

    st.markdown("### Apply New Table Tag")
    with st.form("apply_table_tag"):
        tag_name = st.text_input("Tag Name", placeholder="e.g., secure_contracts")
        tag_value = st.text_input("Tag Value", placeholder="e.g., pii")
        if st.form_submit_button("Apply Tag", type="primary"):
            if tag_name and tag_value:
                success, msg = apply_table_tag(catalog, schema, table, tag_name, tag_value)
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.error("Both tag name and value are required.")


def _render_column_tags(catalog: str, schema: str, table: str) -> None:
    """Render the column tag section."""
    st.markdown("### Column Tags")
    column_tags = get_column_tags(catalog, schema, table)
    columns = get_table_columns(catalog, schema, table)
    if column_tags:
        df = pl.DataFrame(column_tags)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No column tags found.")

    with st.expander("Apply Column Tag", expanded=False):
        with st.form("apply_column_tag"):
            column_name = st.selectbox("Column Name", columns) if columns else ""
            col_tag_name = st.text_input("Tag Name")
            col_tag_value = st.text_input("Tag Value")
            if st.form_submit_button("Apply Column Tag", type="primary"):
                if column_name and col_tag_name and col_tag_value:
                    success, msg = apply_column_tag(
                        catalog, schema, table, column_name, col_tag_name, col_tag_value
                    )
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.error("Column name, tag name, and tag value are required.")


def render_page() -> None:
    """Render the Tag Management page."""
    settings = get_settings()
    st.set_page_config(
        page_title=f"{settings.app_title} - Tag Management",
        page_icon=settings.page_icon,
        layout="wide",
    )
    st.title("Tag Management")
    if not check_admin_access():
        st.error("Access denied")
        st.stop()

    selected_catalog, selected_schema, selected_table = _select_hierarchy()

    if selected_catalog and selected_schema and selected_table:
        st.subheader(f"Tags for {selected_catalog}.{selected_schema}.{selected_table}")
        _render_table_tags(selected_catalog, selected_schema, selected_table)
        _render_column_tags(selected_catalog, selected_schema, selected_table)
    else:
        st.info("Select a table from the sidebar to manage tags.")


render_page()
