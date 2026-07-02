"""
Configuration for dual-mode authentication (local dev vs Databricks Apps).

Local dev:  WorkspaceClient(profile=DATABRICKS_PROFILE or your CLI's DEFAULT profile)
Deployed:   WorkspaceClient()  — auto-injected service-principal credentials
"""
import os
from functools import lru_cache
from typing import List, Optional

from databricks.sdk import WorkspaceClient

# Detect environment: Databricks Apps sets DATABRICKS_APP_NAME.
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))


def get_catalogs() -> Optional[List[str]]:
    """Resolve the catalog allow-list for the ERD graph from ERD_CATALOGS env var.

    Returns None if ERD_CATALOGS is unset/empty -- an explicit, deliberate "unscoped"
    mode (not a silent fallback to a demo catalog): the graph then shows every catalog
    the app's own credentials can browse, and (per this same design) the Genie Space is
    ALSO built unscoped in that case -- see setup/create_scoped_views.py. Unity Catalog's
    own privilege filtering still applies either way; "unscoped" means "bounded by
    whatever this deployment's grants allow," not literally every catalog that exists.
    """
    raw = os.environ.get("ERD_CATALOGS")
    if not raw:
        return None
    catalogs = [c.strip() for c in raw.split(",") if c.strip()]
    return catalogs or None


def get_metadata_location() -> tuple[str, str]:
    """Resolve (catalog, schema) where the scoped ERD metadata views live, from
    ERD_METADATA_LOCATION ("catalog.schema"). Defaults to "<first ERD_CATALOGS
    entry>.erd_meta" so a scoped deployment only has to set ERD_CATALOGS. In unscoped mode
    (ERD_CATALOGS unset) there is no "first catalog" to default to, so
    ERD_METADATA_LOCATION becomes required -- raises rather than guessing a catalog to
    create views in. These views (not system.information_schema directly) are the Genie
    Space's actual data source -- see setup/create_scoped_views.py and
    setup/create_genie_space.py.
    """
    raw = os.environ.get("ERD_METADATA_LOCATION")
    if raw and "." in raw:
        catalog, schema = raw.split(".", 1)
        return catalog.strip(), schema.strip()
    catalogs = get_catalogs()
    if not catalogs:
        raise RuntimeError(
            "ERD_METADATA_LOCATION is required when ERD_CATALOGS is unset (unscoped "
            "mode) -- there's no catalog to default the metadata views into. Set "
            "ERD_METADATA_LOCATION=<catalog>.<schema>."
        )
    return catalogs[0], "erd_meta"


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


@lru_cache(maxsize=1)
def get_workspace_name() -> str:
    """Best-effort human-readable workspace identifier for the UI header, derived from
    the authenticated WorkspaceClient's host -- never hardcoded, so this reads correctly
    in every deployment. Cached since the host doesn't change for the life of the process."""
    host = (get_workspace_client().config.host or "").removeprefix("https://").removeprefix("http://").rstrip("/")
    for suffix in (".cloud.databricks.com", ".azuredatabricks.net", ".gcp.databricks.com"):
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return host or "workspace"
