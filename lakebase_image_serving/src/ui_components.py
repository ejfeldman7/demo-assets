"""
UI components module for Unity Catalog Image Gallery.

Contains Streamlit UI components and layout functions for the gallery interface.
"""

import os
import streamlit as st
import matplotlib.pyplot as plt
from PIL import Image

from .config import (
    DEFAULT_SCHEMA, TABLE_NAME, VOLUME_BASE_PATH
)
from .database import (
    get_all_image_paths,
    get_distinct_labels, get_distinct_label_details, get_score_range
)
from .image_service import image_service


def clean_file_path_for_display(file_path: str) -> str:
    """Clean file path for better display in dropdown - remove dbfs:/ prefix."""
    if file_path.startswith('dbfs:/Volumes/'):
        return file_path.replace('dbfs:', '')
    return file_path


def display_filtering_controls():
    """Display filtering controls and return filter values."""
    st.subheader("🔍 Filter Images")
    
    # Get available filter options from database
    try:
        labels = get_distinct_labels()
        score_min, score_max = get_score_range()
    except Exception as e:
        st.error(f"❌ Error loading filter options: {str(e)}")
        return None, None, None, None
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Label filter
        selected_label = st.selectbox(
            "Filter by Label:",
            options=[None] + labels,
            format_func=lambda x: "All Labels" if x is None else str(x),
            key="label_filter"
        )
        
        # Label Detail filter (dependent on selected label)
        if selected_label:
            label_details = get_distinct_label_details(selected_label)
            selected_label_detail = st.selectbox(
                "Filter by Label Detail:",
                options=[None] + label_details,
                format_func=lambda x: "All Details" if x is None else str(x),
                key="label_detail_filter"
            )
        else:
            label_details = get_distinct_label_details()
            selected_label_detail = st.selectbox(
                "Filter by Label Detail:",
                options=[None] + label_details,
                format_func=lambda x: "All Details" if x is None else str(x),
                key="label_detail_filter"
            )
    
    with col2:
        # Score range filter
        st.write("**Score Range:**")
        score_range = st.slider(
            "Select score range:",
            min_value=float(score_min),
            max_value=float(score_max),
            value=(float(score_min), float(score_max)),
            step=0.01,
            key="score_range_filter"
        )
        
        min_score, max_score = score_range
        st.caption(f"Selected range: {min_score:.2f} - {max_score:.2f}")
    
    return selected_label, selected_label_detail, min_score, max_score


def display_image_selector() -> None:
    """Display dual dropdown image selector interface with filtering."""
    st.subheader("🔍 AI Image Predictions Browser")
    st.markdown("Browse and compare AI-predicted images with advanced filtering capabilities.")
    
    # Display filtering controls
    selected_label, selected_label_detail, min_score, max_score = display_filtering_controls()
    
    if selected_label or selected_label_detail or min_score or max_score:
        st.markdown("---")
    
    # Get filtered image paths
    try:
        all_paths = get_all_image_paths(
            label=selected_label,
            label_detail=selected_label_detail,
            min_score=min_score if min_score else None,
            max_score=max_score if max_score else None
        )
        
        if not all_paths:
            st.warning("⚠️ No images found matching the selected filters.")
            return
        
        st.success(f"✅ Found {len(all_paths)} images matching filters")
        
        # Clean paths for display (remove dbfs:/ prefix)
        display_paths = [clean_file_path_for_display(path) for path in all_paths]
        
        # Create two columns for side-by-side dropdowns
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**🔍 Image 1**")
            selected_index_1 = st.selectbox(
                "Choose first image:",
                range(len(display_paths)),
                format_func=lambda x: os.path.basename(display_paths[x]),
                key="image_selector_1",
                index=None,
                placeholder="Select an image..."
            )
            
            if selected_index_1 is not None:
                selected_path_1 = all_paths[selected_index_1]
                display_selected_image(selected_path_1, "Image 1")
        
        with col2:
            st.markdown("**🔍 Image 2**")
            selected_index_2 = st.selectbox(
                "Choose second image:",
                range(len(display_paths)),
                format_func=lambda x: os.path.basename(display_paths[x]),
                key="image_selector_2", 
                index=None,
                placeholder="Select an image..."
            )
            
            if selected_index_2 is not None:
                selected_path_2 = all_paths[selected_index_2]
                display_selected_image(selected_path_2, "Image 2")
                
    except Exception as e:
        st.error(f"❌ Error loading image paths: {str(e)}")


def show_image_with_matplotlib(file_path: str) -> None:
    """
    Loads and displays an image using matplotlib.
    Args:
        file_path (str): Path to the image file.
    """
    try:
        # For Unity Catalog volumes, we need to use the workspace client to download
        if file_path.startswith('/Volumes/'):
            # Use the existing image service to download the file
            pil_image = image_service.load_image_from_volume(f"dbfs:{file_path}")
            if pil_image is None:
                st.error(f"Failed to load image: {file_path}")
                return
        else:
            # Direct file access (fallback)
            if not os.path.exists(file_path):
                st.error(f"File not found: {file_path}")
                return
            pil_image = Image.open(file_path)
        
        # Create matplotlib figure
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.imshow(pil_image)
        ax.axis('off')
        plt.tight_layout()
        
        # Display in Streamlit
        st.pyplot(fig)
        plt.close(fig)
        
    except Exception as e:
        st.error(f"Error loading image: {e}")


def display_selected_image(file_path: str, label: str) -> None:
    """Display a single selected image with matplotlib visualization."""
    st.markdown(f"**{label}: {os.path.basename(file_path)}**")
    
    # Show clean path for user reference
    clean_path = clean_file_path_for_display(file_path)
    st.caption(f"📁 {clean_path}")
    
    # Display image using matplotlib
    show_image_with_matplotlib(clean_path)
    
    # Show file details in an expander
    with st.expander(f"📋 {label} Details"):
        st.write(f"**Filename:** {os.path.basename(file_path)}")
        st.write(f"**Path:** `{clean_path}`")


def display_setup_instructions() -> None:
    """Display setup requirements when database connection fails."""
    st.error("❌ Cannot connect to the database or the required table doesn't exist.")
    
    st.info(f"""
    **Setup Requirements:**
    1. Ensure your Lakebase connection is properly configured
    2. Verify the `{DEFAULT_SCHEMA}.{TABLE_NAME}` table exists in your PostgreSQL sync
    3. Check that your DATABRICKS_HOST environment variable is set
    4. Ensure you have access to the Unity Catalog volume: `{VOLUME_BASE_PATH}`

    **Current Configuration:**
    - Looking for table: `{DEFAULT_SCHEMA}.{TABLE_NAME}`
    - Expected volume path: `{VOLUME_BASE_PATH}`
    
    **Environment Variables Needed:**
    - `DATABRICKS_HOST`: Your Databricks workspace URL
    - `PGDATABASE`: PostgreSQL database name
    - `PGUSER`: PostgreSQL username
    - `PGHOST`: PostgreSQL host
    - `PGPORT`: PostgreSQL port (usually 5432)
    
    **Troubleshooting:**
    - Verify the correct schema name matches your Lakebase database structure
    - Ensure you've added the Lakebase database as a resource in the Databricks Apps UI
    - Check that the image_predictions table is syncing properly from Unity Catalog
    """)
