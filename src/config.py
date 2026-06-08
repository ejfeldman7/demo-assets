"""
Configuration module for Unity Catalog Image Gallery.

Contains constants, environment variable handling, and configuration settings.
"""

import os
from typing import Optional

# Database configuration  
DEFAULT_SCHEMA = "example" # Update to the location for your Lakebase database
TABLE_NAME = "image_predictions" # Update to the location for your Lakebase table
PATH_COLUMN = "path" # Update to column in your table that contains the path to the image
VOLUME_BASE_PATH = "/Volumes/demos/image_app/images" # Update to the location for your Unity Catalog volume

# UI configuration
DEFAULT_ITEMS_PER_PAGE = 24

# Connection settings
TOKEN_REFRESH_INTERVAL = 900  # 15 minutes in seconds
CONNECTION_POOL_MIN_SIZE = 2
CONNECTION_POOL_MAX_SIZE = 10

# UI configuration
DEFAULT_ITEMS_PER_PAGE = 24
ITEMS_PER_PAGE_OPTIONS = [12, 24, 48]
GRID_COLUMNS_PER_ROW = 4
IMAGE_THUMBNAIL_SIZE = (200, 200)

# API configuration
REQUEST_TIMEOUT = 10  # seconds
API_FILES_PATH = "/api/2.0/fs/files/"


def get_databricks_host() -> Optional[str]:
    """Get Databricks workspace host URL."""
    return os.getenv('DATABRICKS_HOST', '')


def get_pg_connection_params() -> dict:
    """Get PostgreSQL connection parameters from environment variables."""
    return {
        'dbname': os.getenv('PGDATABASE'),
        'user': os.getenv('PGUSER'),
        'host': os.getenv('PGHOST'),
        'port': os.getenv('PGPORT'),
        'sslmode': os.getenv('PGSSLMODE', 'require'),
        'application_name': os.getenv('PGAPPNAME')
    }


def validate_required_env_vars() -> list:
    """Validate that required environment variables are set."""
    required_vars = [
        'DATABRICKS_HOST',
        'PGDATABASE',
        'PGUSER',
        'PGHOST',
        'PGPORT'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    return missing_vars


def get_all_env_vars() -> dict:
    """Get all environment variables for debugging."""
    return {k: v for k, v in os.environ.items() if k.startswith(('PG', 'DATABRICKS_', 'STREAMLIT_'))}


