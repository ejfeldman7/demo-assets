"""
lakebase.py — Lakebase (Postgres) connection for Workload Watchtower.

Adapted from the abac-helper pattern: the Postgres password is a short-lived OAuth
credential minted via the Databricks SDK, so no static DB password is stored. The
poller (job SP) owns the schema and does DDL + writes; the app SP reads/writes triage
state within the schema.

`apps` is an Autoscaling Lakebase project, so credentials are minted per-ENDPOINT via
w.postgres.generate_database_credential(endpoint=...), not the legacy instance-name API.

Env config (set in the job env / app.yaml):
  LAKEBASE_ENDPOINT  — Autoscaling endpoint path         (default: projects/apps/branches/production/endpoints/primary)
  LAKEBASE_HOST      — PG read-write endpoint host
  LAKEBASE_DB        — database name                     (default: databricks_postgres)
  LAKEBASE_SCHEMA    — schema holding Watchtower tables  (default: watchtower)
  LAKEBASE_USER      — PG user; resolved to running identity if empty
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from databricks.sdk import WorkspaceClient

log = logging.getLogger(__name__)

# Autoscaling endpoint path, e.g. projects/<project>/branches/production/endpoints/primary.
# Required — no default (set via config.env locally / task parameters on the poller job).
ENDPOINT = os.environ["LAKEBASE_ENDPOINT"]
HOST = os.environ["LAKEBASE_HOST"]
DB = os.environ.get("LAKEBASE_DB", "databricks_postgres")
SCHEMA = os.environ.get("LAKEBASE_SCHEMA", "watchtower")
USER = os.environ.get("LAKEBASE_USER", "")

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _pg_password(w: WorkspaceClient) -> str:
    """Mint a short-lived Lakebase credential (Autoscaling endpoint API)."""
    return w.postgres.generate_database_credential(endpoint=ENDPOINT).token


def _resolve_user(w: WorkspaceClient) -> str:
    """PG user = the running identity (SP application-id in a job, email locally)."""
    if USER:
        return USER
    me = w.current_user.me()
    return me.user_name or ""


@contextmanager
def connect(w: WorkspaceClient, *, autocommit: bool = True):
    """Yield a psycopg connection to Lakebase with search_path set to the schema."""
    conn = psycopg.connect(
        host=HOST,
        port=5432,
        dbname=DB,
        user=_resolve_user(w),
        password=_pg_password(w),
        sslmode="require",
        connect_timeout=30,
        autocommit=autocommit,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {SCHEMA}")
        yield conn
    finally:
        conn.close()


def bootstrap_schema(w: WorkspaceClient) -> None:
    """Create the Watchtower schema + tables if absent. Run once by the schema owner."""
    ddl = _SCHEMA_SQL.read_text().replace("{schema}", SCHEMA)
    with connect(w) as conn, conn.cursor() as cur:
        cur.execute(ddl)
    log.info("Lakebase schema '%s' ensured on endpoint '%s'", SCHEMA, ENDPOINT)
