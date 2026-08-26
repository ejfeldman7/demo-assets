"""
Configuration for dual-mode authentication (local dev vs Databricks Apps).

Local dev:  WorkspaceClient(profile=DATABRICKS_PROFILE or your CLI's DEFAULT profile)
Deployed:   WorkspaceClient()  — auto-injected service-principal credentials
"""
import contextvars
import os
from functools import lru_cache
from typing import List, Optional, Tuple

from databricks.sdk import WorkspaceClient

# Detect environment: Databricks Apps sets DATABRICKS_APP_NAME.
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))

# --- authentication mode (service principal vs on-behalf-of-user) --------------
#
# Default is "service_principal": the app queries information_schema as its own SP,
# bounded by ERD_CATALOGS (the original, unchanged behavior). Set ERD_AUTH_MODE to
# "on_behalf_of_user" (deploy-time flag) to instead run every metadata query as the
# LOGGED-IN USER, via the token Databricks Apps forwards in the x-forwarded-access-token
# header -- so the graph is filtered by that user's own UC privileges (intersected with
# ERD_CATALOGS, which still applies as the upper-bound allow-list). OBO additionally
# requires the app to be granted the "sql" user authorization scope (see README/DAB).
_AUTH_OBO = "on_behalf_of_user"
_AUTH_SP = "service_principal"

# Per-request identity captured from the forwarded headers by the routes' dependency
# (see server/routes/graph.py). ContextVars (not globals) so concurrent requests never
# see each other's user. Read by get_query_client()/get_user_cache_key() during the same
# request task. NOTE: this relies on the query being issued within the request's own
# async task (the graph routes call build_graph inline) -- if a handler is ever moved to
# a worker thread, the token must be threaded explicitly instead.
_user_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("erd_user_token", default=None)
_user_key: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("erd_user_key", default=None)


def get_auth_mode() -> str:
    """Resolve the auth mode from ERD_AUTH_MODE. Anything other than an explicit
    on-behalf-of-user value (incl. unset) is treated as service-principal, so the safe
    default is never accidentally OBO."""
    raw = (os.environ.get("ERD_AUTH_MODE") or "").strip().lower()
    return _AUTH_OBO if raw in (_AUTH_OBO, "obo", "user", "on-behalf-of-user") else _AUTH_SP


def set_user_context(token: Optional[str], email: Optional[str]) -> Tuple[object, object]:
    """Record the forwarded user token + identity for the current request; returns reset
    tokens for reset_user_context() to restore in a finally block."""
    key = email or (token[:16] if token else None)
    return _user_token.set(token), _user_key.set(key)


def reset_user_context(tokens: Tuple[object, object]) -> None:
    _user_token.reset(tokens[0])
    _user_key.reset(tokens[1])


def get_user_cache_key() -> str:
    """Cache discriminator so OBO results are never shared across users (each user sees a
    privilege-filtered graph). Empty string in SP mode -> the cache stays shared, exactly
    as before."""
    if IS_DATABRICKS_APP and get_auth_mode() == _AUTH_OBO:
        return _user_key.get() or "anonymous"
    return ""


def _workspace_host_url() -> str:
    """Workspace URL for building an OBO WorkspaceClient. In Apps, DATABRICKS_HOST is the
    bare hostname (no scheme) -- add https://."""
    host = os.environ.get("DATABRICKS_HOST", "")
    if host and not host.startswith("http"):
        host = f"https://{host}"
    return host


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
    """Get an authenticated WorkspaceClient for the current environment -- always the
    app's own identity (service principal in Apps, your CLI profile locally). Used for
    deployment-level lookups (e.g. workspace name), NOT for the user-scoped metadata
    queries -- those go through get_query_client()."""
    if IS_DATABRICKS_APP:
        # Remote: uses auto-injected service-principal credentials.
        return WorkspaceClient()
    # Local: uses a Databricks CLI profile (your ~/.databrickscfg DEFAULT profile unless
    # DATABRICKS_PROFILE names a different one).
    profile = os.environ.get("DATABRICKS_PROFILE")
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def get_query_client() -> WorkspaceClient:
    """The client the ERD metadata queries run through -- this is where SP vs OBO is
    decided. In on-behalf-of-user mode (deployed), returns a client built from the
    logged-in user's forwarded token so information_schema is filtered by THEIR
    privileges; otherwise returns the app's own client (SP / local profile), preserving
    the original behavior exactly.

    Raises in OBO mode when no forwarded token is present (rather than silently falling
    back to the SP, which would over-expose data a scoped user shouldn't see) -- e.g. the
    app is missing the 'sql' user scope, or was reached outside the Apps proxy."""
    if IS_DATABRICKS_APP and get_auth_mode() == _AUTH_OBO:
        token = _user_token.get()
        if not token:
            raise RuntimeError(
                "on_behalf_of_user auth is enabled but this request carried no "
                "x-forwarded-access-token. Confirm the app has the 'sql' user "
                "authorization scope and is accessed through the Databricks Apps proxy."
            )
        # auth_type="pat" pins the client to JUST this bearer token. Without it the SDK
        # ALSO discovers the App's injected service-principal OAuth env
        # (DATABRICKS_CLIENT_ID/SECRET) and aborts with "more than one authorization
        # method configured: oauth and pat" -- so the forwarded user token must be forced
        # as the sole credential.
        return WorkspaceClient(host=_workspace_host_url(), token=token, auth_type="pat")
    return get_workspace_client()


def get_test_catalog_suffix() -> str:
    """Suffix appended to each configured (prod) catalog name to get its test-environment
    equivalent -- e.g. edp_customer -> edp_customer_ts when the frontend's Prod/Test
    toggle is set to "test". These are two distinct real Unity Catalog catalogs, not an
    alias, so this only matters when ERD_CATALOGS (a scoped deployment) is set -- see
    graph.py's _resolve_catalogs(). Configurable via ERD_TEST_CATALOG_SUFFIX since not
    every customer's test catalogs use "_ts"."""
    return os.environ.get("ERD_TEST_CATALOG_SUFFIX") or "_ts"


def get_warehouse_id() -> Optional[str]:
    """Resolve the SQL warehouse id used for information_schema queries. No hardcoded
    fallback on purpose -- a warehouse id from a different workspace would silently be
    wrong rather than failing clearly. Set DATABRICKS_WAREHOUSE_ID (see README.md)."""
    return os.environ.get("DATABRICKS_WAREHOUSE_ID")


def get_genie_space_id() -> Optional[str]:
    """Resolve the Genie Space id from GENIE_SPACE_ID env var, set by the DAB/notebook
    deploy flow once setup/create_genie_space.py has run (see README.md).

    Returns None (chat shows a friendly "not configured" message) when unset OR set to
    the "not-configured" sentinel. The sentinel exists because the Databricks Apps API
    rejects an env var whose value renders empty ("Must specify environment variable
    source using either `value` or `valueFrom`") -- so the DAB ships a non-empty
    placeholder rather than "", and this treats that placeholder as unset."""
    raw = os.environ.get("GENIE_SPACE_ID")
    if not raw or raw == "not-configured":
        return None
    return raw


def get_cache_ttl_seconds() -> int:
    """How long /api/graph results are cached in-memory before re-querying
    information_schema. Configurable via ERD_CACHE_TTL_SECONDS -- schema metadata
    changes rarely, so the 300s default trades a little staleness for far fewer
    warehouse round-trips on repeated loads/filters within a session."""
    raw = os.environ.get("ERD_CACHE_TTL_SECONDS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 300


def get_schema_collapse_threshold() -> Optional[int]:
    """Table count above which /api/graph defaults to one node per schema instead of one
    per table (see server/graph.py's schema-summary view) -- unreadable/slow flat layouts
    on catalogs with hundreds of tables. Configurable via ERD_SCHEMA_COLLAPSE_THRESHOLD;
    0 or an invalid value disables collapsing entirely (always render full detail)."""
    raw = os.environ.get("ERD_SCHEMA_COLLAPSE_THRESHOLD")
    if raw:
        try:
            value = int(raw)
            return value if value > 0 else None
        except ValueError:
            pass
    return 80


@lru_cache(maxsize=1)
def get_workspace_name() -> str:
    """Best-effort human-readable workspace identifier for the UI header, derived from
    the authenticated WorkspaceClient's host -- never hardcoded, so this reads correctly
    in every deployment. Cached since the host doesn't change for the life of the process."""
    host = (_workspace_host_url() or get_workspace_client().config.host or "").removeprefix("https://").removeprefix("http://").rstrip("/")
    for suffix in (".cloud.databricks.com", ".azuredatabricks.net", ".gcp.databricks.com"):
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return host or "workspace"
