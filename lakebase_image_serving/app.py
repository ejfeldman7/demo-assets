"""
Unity Catalog Image Gallery - Main Application

A Streamlit application for displaying images from Unity Catalog volumes
using Lakebase PostgreSQL sync for metadata management.
"""

import streamlit as st

# Import modules
from src.config import validate_required_env_vars
from src.database import check_database_connection
from src.ui_components import (
    display_image_selector,
    display_setup_instructions
)


def main():
    """Main application entry point."""
    # Configure page
    st.set_page_config(
        page_title="Unity Catalog Image Gallery",
        page_icon="🖼️",
        layout="wide"
    )
    
    # App header
    st.title("🖼️ Unity Catalog Image Gallery")
    st.markdown("*AI Image Predictions Browser powered by Lakebase*")
    st.markdown("---")
    
    # Validate environment variables
    missing_vars = validate_required_env_vars()
    if missing_vars:
        st.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        st.info("""
        **Required Environment Variables:**
        - `DATABRICKS_HOST`: Your Databricks workspace URL
        - `PGDATABASE`: PostgreSQL database name
        - `PGUSER`: PostgreSQL username
        - `PGHOST`: PostgreSQL host
        - `PGPORT`: PostgreSQL port
        """)
        st.stop()
    
    # Check database connection
    if not check_database_connection():
        display_setup_instructions()
        st.stop()
    
    # Main image selector interface
    display_image_selector()


if __name__ == "__main__":
    main() 