"""
auth.py — WorkspaceClient initialization for Permission Explorer.

Two-client strategy
-------------------
The app uses two separate WorkspaceClient identities:

1. get_client() — the visiting USER's identity.
   Built from the X-Forwarded-Access-Token header that Databricks Apps injects
   into every request.  Used for all permission/ACL reads so the results reflect
   what the logged-in user (typically a workspace admin) can see.

   The Databricks App runtime also sets DATABRICKS_CLIENT_ID / CLIENT_SECRET env
   vars for the SP's M2M OAuth.  The SDK raises "multiple auth methods" if both
   are present, so _build_user_client() briefly clears those env vars under a
   lock while constructing the client, then restores them.

2. get_sp_client() — the app SERVICE PRINCIPAL's identity (M2M OAuth).
   Used exclusively for SCIM operations (listing users and groups).  The user's
   forwarded token only has iam.current-user:read + iam.access-control:read
   scopes; the SCIM endpoint requires the 'scim' scope, which the SP (now a
   workspace admin) has.

Thread safety
-------------
Streamlit runs each browser session in its own thread.  _local (threading.local)
gives each session its own user client without cross-session leakage.  The SP
client is a process-level singleton — same credentials for every session.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from databricks.sdk import WorkspaceClient

# Per-session (per-thread) storage for the user-token client.
_local = threading.local()

# Held only during WorkspaceClient.__init__ to safely clear M2M env vars.
_init_lock = threading.Lock()

# Process-level SP singleton and its init lock.
_sp_client: Optional[WorkspaceClient] = None
_sp_lock = threading.Lock()


# ---------------------------------------------------------------------------
# User-token client
# ---------------------------------------------------------------------------

def set_user_token(token: str) -> None:
    """Store the visiting user's OAuth token for this session thread.

    Call once per Streamlit script run (top of app.py) before any API calls.
    Invalidates the cached client so a fresh one is built on the next call.
    """
    _local.user_token = token
    _local.client = None


def get_user_token() -> Optional[str]:
    """Return the current thread's user token, or None."""
    return getattr(_local, "user_token", None)


def _build_user_client(host: str, token: str) -> WorkspaceClient:
    """Build a WorkspaceClient with the user's token, isolated from M2M env vars."""
    m2m_keys = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")
    with _init_lock:
        saved = {k: os.environ.pop(k, None) for k in m2m_keys}
        try:
            return WorkspaceClient(host=host, token=token)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


def get_client() -> WorkspaceClient:
    """Return a user-token WorkspaceClient for the current session.

    Falls back to SDK default credentials (SP env vars or ~/.databrickscfg)
    when no user token is set — e.g. during local development.
    """
    token: Optional[str] = getattr(_local, "user_token", None)

    if token:
        if getattr(_local, "client", None) is None:
            host = os.environ.get("DATABRICKS_HOST", "")
            if host and not host.startswith("http"):
                host = f"https://{host}"
            _local.client = _build_user_client(host, token)
        return _local.client

    # Local dev fallback: SDK reads DATABRICKS_* env vars / ~/.databrickscfg.
    if getattr(_local, "client", None) is None:
        _local.client = WorkspaceClient()
    return _local.client


# ---------------------------------------------------------------------------
# SP client — used for SCIM only
# ---------------------------------------------------------------------------

def get_sp_client() -> WorkspaceClient:
    """Return the app service principal's WorkspaceClient.

    This client uses the M2M OAuth credentials (DATABRICKS_CLIENT_ID +
    DATABRICKS_CLIENT_SECRET) injected by the Databricks App runtime.  It is
    used only for SCIM calls (list users / list groups) because those require
    the 'scim' OAuth scope, which the user's forwarded token does not have.

    The SP is a workspace admin (added manually), so it has full SCIM access.
    """
    global _sp_client
    if _sp_client is None:
        with _sp_lock:
            if _sp_client is None:
                _sp_client = WorkspaceClient()  # reads DATABRICKS_* env vars
    return _sp_client
