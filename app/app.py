from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from utils.access_manager import get_access_rules
from utils.auth import check_admin_access
from utils.setup_utils import AutoSetupManager
from utils.tag_manager import get_catalogs


def _render_metrics() -> None:
    """Render the summary metrics for the landing page."""
    settings = get_settings()
    rules = get_access_rules({"status": "active"})
    total_rules = len(rules)
    catalogs = get_catalogs()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Active Access Rules", total_rules)
    with col2:
        st.metric("Catalogs Available", len(catalogs))
    with col3:
        st.metric("Admin Group", settings.admin_group)


def main() -> None:
    """Render the main landing page."""
    settings = get_settings()
    st.set_page_config(
        page_title=settings.app_title,
        page_icon=settings.page_icon,
        layout="wide",
    )
    st.title(f"{settings.page_icon} {settings.app_title}")
    st.caption("Manage Unity Catalog access rules and governed tags.")

    if not check_admin_access():
        st.error("Access denied")
        st.warning(
            f"You must be a member of the '{settings.admin_group}' group to use this app."
        )
        st.info("Contact your Databricks administrator to request access.")
        st.stop()

    setup_status = AutoSetupManager(settings).ensure_setup_complete()
    if not setup_status.setup_complete:
        st.warning("Setup is incomplete. Some features may not be available.")
        if setup_status.errors:
            st.error("Setup errors:")
            for error in setup_status.errors:
                st.write(f"- {error}")

    st.markdown(
        """
        Welcome to the Unity Catalog Access Management application.

        Use this app to:
        - Manage group access to customer data
        - Apply and remove governed tags
        - Review audit logs and compliance reporting
        """
    )
    _render_metrics()
    st.markdown("Select a page from the sidebar to get started.")


if __name__ == "__main__":
    main()
