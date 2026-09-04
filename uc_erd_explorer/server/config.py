"""
Configuration for dual-mode authentication (local dev vs Databricks Apps).

Local dev:  WorkspaceClient(profile=DATABRICKS_PROFILE or your CLI's DEFAULT profile)
Deployed:   WorkspaceClient()  — auto-injected service-principal credentials
"""
import contextvars
import logging
import os
import threading
import time
from functools import lru_cache
from typing import List, Optional, Tuple

from databricks.sdk import WorkspaceClient

logger = logging.getLogger("erd")

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
# see each other's user. Read by get_query_client()/get_user_cache_key(). The graph
# routes run build_graph off the event loop via asyncio.to_thread, which COPIES the
# current context into the worker thread, and build_graph's own query fan-out re-copies
# the context into each pool thread (see graph.py's _submit_query) -- so the identity is
# threaded through explicitly wherever a query actually executes, never read from a bare
# global.
_user_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("erd_user_token", default=None)
_user_key: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("erd_user_key", default=None)
# The forwarded user email (x-forwarded-email), kept separately from the cache key so the
# admin gate can match it against ERD_ADMIN_EMAILS. Databricks Apps forward it in both
# auth modes; absent in local dev.
_user_email: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("erd_user_email", default=None)


def get_auth_mode() -> str:
    """Resolve the auth mode from ERD_AUTH_MODE. Anything other than an explicit
    on-behalf-of-user value (incl. unset) is treated as service-principal, so the safe
    default is never accidentally OBO."""
    raw = (os.environ.get("ERD_AUTH_MODE") or "").strip().lower()
    return _AUTH_OBO if raw in (_AUTH_OBO, "obo", "user", "on-behalf-of-user") else _AUTH_SP


def set_user_context(token: Optional[str], email: Optional[str]) -> Tuple[object, object, object]:
    """Record the forwarded user token + identity for the current request; returns reset
    tokens for reset_user_context() to restore in a finally block."""
    key = email or (token[:16] if token else None)
    return _user_token.set(token), _user_key.set(key), _user_email.set(email)


def reset_user_context(tokens: Tuple[object, object, object]) -> None:
    _user_token.reset(tokens[0])
    _user_key.reset(tokens[1])
    _user_email.reset(tokens[2])


def get_user_email() -> Optional[str]:
    """The forwarded email of the logged-in user for this request, or None (local dev)."""
    return _user_email.get()


def get_admin_emails() -> set[str]:
    """Lowercased allow-list from ERD_ADMIN_EMAILS (comma-separated). An empty result set
    means no restriction -> admin actions are open. "*" is the explicit open sentinel (the
    deploy-time default, since Databricks Apps reject an empty-string env value), and a
    bare empty string means the same; set specific emails to lock admin down."""
    raw = (os.environ.get("ERD_ADMIN_EMAILS") or "").strip()
    if raw in ("", "*"):
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


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


_SOURCE_SNAPSHOT = "snapshot"
_SOURCE_LIVE = "information_schema"


def get_metadata_source() -> str:
    """Where the ERD graph reads its metadata from, via ERD_METADATA_SOURCE:

    - "information_schema" (default): query system.information_schema live on every cache
      miss -- the original behavior.
    - "snapshot": read the pre-materialized Delta tables in the metadata location
      (erd_snapshot_*, built weekly by setup/build_erd_snapshot.py / the
      refresh_erd_snapshot job), so the expensive information_schema joins (esp. the
      5-table FK join) never run on the request path. graph.py falls back to live
      automatically if the snapshot tables aren't present yet (fresh deploy before the
      first refresh), so enabling this is safe even before the job has run.

    Anything other than an explicit snapshot value (incl. unset) is treated as live."""
    raw = (os.environ.get("ERD_METADATA_SOURCE") or "").strip().lower()
    return _SOURCE_SNAPSHOT if raw in (_SOURCE_SNAPSHOT, "delta", "materialized") else _SOURCE_LIVE


@lru_cache(maxsize=1)
def get_workspace_client() -> WorkspaceClient:
    """Get an authenticated WorkspaceClient for the current environment -- always the
    app's own identity (service principal in Apps, your CLI profile locally). Used for
    deployment-level lookups (e.g. workspace name), and for the metadata queries in
    service-principal mode (get_query_client() returns this), NOT for the per-user OBO
    queries -- those build a fresh per-request client.

    Cached (lru_cache) so the app/SP client -- which resolves auth/config -- is built
    ONCE per process rather than on every query: build_graph fans out ~6 queries per
    load, and the SDK client is safe to share across the query threadpool. The app's own
    auth source is fixed for the process's life, so there's nothing to invalidate. (The
    OBO path in get_query_client() deliberately does NOT use this -- it must mint a client
    per user token.)"""
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


# Must stay identical to setup/create_genie_space.py's MANAGED_MARKER: that script embeds
# this string in the Genie Space description, and the app finds the space by it below.
# A drift between the two silently breaks auto-discovery, so a test asserts they match.
GENIE_MANAGED_MARKER = "[erd-explorer-managed]"

# Auto-discovery cache. A found id is stable, so cache it long; a miss is cached briefly so
# the app picks up a space created by `setup_genie_space` within about a minute, without
# re-listing on every /api/genie/ask in the meantime.
_GENIE_DISCOVERY_TTL = 3600.0
_GENIE_DISCOVERY_MISS_TTL = 60.0
_genie_space_cache = {"id": None, "ts": 0.0, "resolved": False}
_genie_cache_lock = threading.Lock()


def _discover_managed_genie_space() -> Optional[str]:
    """List Genie spaces as the app's own identity (never the OBO user -- Genie always runs
    as the SP) and return the id of the one this deployment manages, matched by
    GENIE_MANAGED_MARKER in its description -- the marker setup/create_genie_space.py embeds.
    The app SP sees the space because that same setup step grants it CAN_RUN. Best-effort:
    returns None on any error (missing permission, API hiccup) so discovery failing just
    reads as 'not configured yet', never a 500."""
    try:
        w = get_workspace_client()
        page_token = None
        while True:
            query = {"page_size": 100}
            if page_token:
                query["page_token"] = page_token
            resp = w.api_client.do(method="GET", path="/api/2.0/genie/spaces", query=query)
            for space in resp.get("spaces", []):
                if GENIE_MANAGED_MARKER in (space.get("description") or ""):
                    return space.get("space_id") or space.get("id")
            page_token = resp.get("next_page_token")
            if not page_token:
                return None
    except Exception as e:  # noqa: BLE001
        logger.warning("Genie space auto-discovery failed (treating as not configured): %s", e)
        return None


def resolve_genie_space_id() -> Optional[str]:
    """The Genie Space the chat should talk to. An explicit GENIE_SPACE_ID env always wins
    (and short-circuits before any API call); otherwise the app auto-discovers the space the
    setup job created, matched by its managed marker -- so a deployment only has to run
    `databricks bundle run setup_genie_space`, with no copy-the-id-and-redeploy step and
    nothing to reset on the next `bundle deploy`. Returns None when neither is available
    (chat then shows the friendly "not configured" message).

    BLOCKING on a cache miss (it lists Genie spaces), so callers must run it off the event
    loop -- see server/routes/genie.py (asyncio.to_thread). Result is cached (see the TTLs)."""
    explicit = get_genie_space_id()
    if explicit:
        return explicit
    now = time.time()
    with _genie_cache_lock:
        if _genie_space_cache["resolved"]:
            ttl = _GENIE_DISCOVERY_TTL if _genie_space_cache["id"] else _GENIE_DISCOVERY_MISS_TTL
            if now - _genie_space_cache["ts"] < ttl:
                return _genie_space_cache["id"]
    # Discover outside the lock so a slow list doesn't serialize concurrent asks; a rare
    # duplicate lookup under a cold cache is harmless.
    discovered = _discover_managed_genie_space()
    with _genie_cache_lock:
        _genie_space_cache.update(id=discovered, ts=time.time(), resolved=True)
    if discovered:
        logger.info("Auto-discovered managed Genie Space: %s", discovered)
    return discovered


def get_snapshot_job_id() -> Optional[str]:
    """Job id of the refresh_erd_snapshot job, from ERD_SNAPSHOT_JOB_ID (templated by the
    DAB from the job resource's id). Powers the admin "Refresh snapshot now" control,
    which triggers this job via the Jobs API. None when unset (e.g. deployed without the
    job, or run locally) -- the admin control then reports the refresh action as
    unavailable rather than erroring."""
    raw = (os.environ.get("ERD_SNAPSHOT_JOB_ID") or "").strip()
    return raw or None


def get_cache_ttl_seconds() -> int:
    """How long /api/graph results are cached in-memory before re-querying
    information_schema. Configurable via ERD_CACHE_TTL_SECONDS -- schema metadata changes
    rarely (objects deploy ~weekly), so the 3600s (1h) default trades a little staleness
    for far fewer warehouse round-trips on repeated loads/filters. This is the in-process
    live cache; the weekly-materialized-metadata path (planned) is the durable answer and
    supersedes this once in place."""
    raw = os.environ.get("ERD_CACHE_TTL_SECONDS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 3600


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
