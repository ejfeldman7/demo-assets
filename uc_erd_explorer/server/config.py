"""
Configuration for dual-mode authentication (local dev vs Databricks Apps).

Local dev:  WorkspaceClient(profile=DATABRICKS_PROFILE or your CLI's DEFAULT profile)
Deployed:   WorkspaceClient()  — auto-injected service-principal credentials
"""
import os
from typing import List, Optional

from databricks.sdk import WorkspaceClient

# Detect environment: Databricks Apps sets DATABRICKS_APP_NAME.
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))

# Which catalogs the ERD graph is allowed to show. Comma-separated via env var so a
# deployment can be scoped without a code change; defaults to just the demo catalog.
# NOTE: this does NOT change what the Genie Space can see -- that stays hard-scoped to
# its own dedicated views (see setup/create_scoped_views.py) regardless of this setting.
DEFAULT_CATALOGS = ["megacorp"]


def get_catalogs() -> List[str]:
    """Resolve the catalog allow-list for the ERD graph from ERD_CATALOGS env var."""
    raw = os.environ.get("ERD_CATALOGS")
    if not raw:
        return list(DEFAULT_CATALOGS)
    catalogs = [c.strip() for c in raw.split(",") if c.strip()]
    return catalogs or list(DEFAULT_CATALOGS)


def get_metadata_location() -> tuple[str, str]:
    """Resolve (catalog, schema) where the scoped ERD metadata views live, from
    ERD_METADATA_LOCATION ("catalog.schema"). Defaults to "<first catalog>.erd_meta" so a
    deployment only has to set ERD_CATALOGS to get a sensible default. These views (not
    system.information_schema directly) are the Genie Space's actual data source --
    see setup/create_scoped_views.py and setup/create_genie_space.py.
    """
    raw = os.environ.get("ERD_METADATA_LOCATION")
    if raw and "." in raw:
        catalog, schema = raw.split(".", 1)
        return catalog.strip(), schema.strip()
    return get_catalogs()[0], "erd_meta"


def get_workspace_client() -> WorkspaceClient:
    """Get an authenticated WorkspaceClient for the current environment."""
    if IS_DATABRICKS_APP:
        # Remote: uses auto-injected service-principal credentials.
        return WorkspaceClient()
    # Local: uses a Databricks CLI profile (your ~/.databrickscfg DEFAULT profile unless
    # DATABRICKS_PROFILE names a different one).
    profile = os.environ.get("DATABRICKS_PROFILE")
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def get_warehouse_id() -> Optional[str]:
    """Resolve the SQL warehouse id used for information_schema queries. No hardcoded
    fallback on purpose -- a warehouse id from a different workspace would silently be
    wrong rather than failing clearly. Set DATABRICKS_WAREHOUSE_ID (see README.md)."""
    return os.environ.get("DATABRICKS_WAREHOUSE_ID")


def get_genie_space_id() -> Optional[str]:
    """Resolve the Genie Space id from GENIE_SPACE_ID env var, set by the DAB/notebook
    deploy flow once setup/create_genie_space.py has run (see README.md)."""
    return os.environ.get("GENIE_SPACE_ID") or None
