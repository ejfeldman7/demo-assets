"""
db.py — Lakebase (Postgres) connection pool for the Watchtower app.

`apps` is an Autoscaling Lakebase project, so credentials are minted per-ENDPOINT via
w.postgres.generate_database_credential(endpoint=...) — the same pattern the poller uses.
A fresh OAuth token is minted per new/recycled connection, so no background refresh is
needed; max_lifetime recycles before the 1h token expiry.

Dual-mode:
  - local: WorkspaceClient() uses the CLI profile (DATABRICKS_CONFIG_PROFILE),
           PG user = your email.
  - app:   WorkspaceClient() uses the injected app-SP creds, PG user = PGUSER
           (the app SP application id).
"""

from __future__ import annotations

import os

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg_pool import ConnectionPool

# Autoscaling endpoint path (e.g. projects/<project>/branches/production/endpoints/primary).
# Set in app.yaml; there is no sensible default, so this must be provided.
ENDPOINT = os.environ["ENDPOINT_NAME"]
# PGHOST is auto-injected when the Lakebase database is attached as an app resource; locally
# it comes from your config.env (LAKEBASE_HOST). No default — fail clearly if unset.
HOST = os.environ.get("PGHOST") or os.environ["LAKEBASE_HOST"]
DB = os.environ.get("PGDATABASE", "databricks_postgres")
PORT = os.environ.get("PGPORT", "5432")
SCHEMA = os.environ.get("LAKEBASE_SCHEMA", "watchtower")

w = WorkspaceClient()


def _pg_user() -> str:
    return os.environ.get("PGUSER") or (w.current_user.me().user_name or "")


class OAuthConnection(psycopg.Connection):
    """Mints a fresh Lakebase OAuth token as the Postgres password per connection."""

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs):
        token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
        kwargs["password"] = token
        kwargs.setdefault("autocommit", True)  # so `configure` SET leaves conn idle
        return super().connect(conninfo, **kwargs)


pool = ConnectionPool(
    conninfo=f"dbname={DB} user={_pg_user()} host={HOST} port={PORT} sslmode=require",
    connection_class=OAuthConnection,
    configure=lambda conn: conn.execute(f"SET search_path TO {SCHEMA}"),
    # Validate a connection before handing it out — Autoscaling can recycle/scale-to-zero
    # and drop pooled connections (AdminShutdown); check reconnects transparently.
    check=ConnectionPool.check_connection,
    min_size=1,
    max_size=5,
    max_lifetime=2700,  # recycle 15 min before the 1-hour token expires
    open=False,
)


def rows_to_dicts(cur) -> list[dict]:
    """Turn a cursor's result set into a list of dicts (JSON-friendly)."""
    cols = [c.name for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = {}
        for k, v in zip(cols, row):
            d[k] = v.isoformat() if hasattr(v, "isoformat") else v
        out.append(d)
    return out
