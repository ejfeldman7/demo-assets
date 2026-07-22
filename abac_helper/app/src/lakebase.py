"""
lakebase.py — Lakebase (Postgres) connection + schema for the permission snapshot.

The Permission Explorer reads a pre-computed, denormalised snapshot from Lakebase
instead of fanning out hundreds of workspace/UC API calls per user click. This
module owns:
  - the connection (OAuth token minted via the SDK, used as the PG password),
  - the DDL for the snapshot tables,
  - the upsert/replace routines the nightly job calls,
  - the read query the Explorer uses.

Env config (set in app.yaml / the job):
  LAKEBASE_INSTANCE  — Lakebase instance name (default: account-intel-board)
  LAKEBASE_HOST      — PG endpoint host
  LAKEBASE_DB        — database name (default: databricks_postgres)
  LAKEBASE_USER      — PG user (the identity running: SP in job / user locally)
  LAKEBASE_SCHEMA    — schema to hold the tables (default: abac)
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager

import psycopg
from databricks.sdk import WorkspaceClient

log = logging.getLogger(__name__)

INSTANCE = os.environ.get("LAKEBASE_INSTANCE", "abac-helper")
HOST = os.environ.get("LAKEBASE_HOST", "ep-spring-surf-d1yujy8r.database.us-west-2.cloud.databricks.com")
DB = os.environ.get("LAKEBASE_DB", "databricks_postgres")
USER = os.environ.get("LAKEBASE_USER", "")   # resolved at runtime if empty
SCHEMA = os.environ.get("LAKEBASE_SCHEMA", "abac")


def _pg_password(w: WorkspaceClient) -> str:
    """Mint a short-lived Lakebase credential (used as the Postgres password)."""
    return w.database.generate_database_credential(
        instance_names=[INSTANCE], request_id=str(uuid.uuid4())
    ).token


def _resolve_user(w: WorkspaceClient) -> str:
    """PG user = the running identity's login (SP app-id in job, email locally)."""
    if USER:
        return USER
    try:
        me = w.current_user.me()
        return me.user_name or ""
    except Exception as exc:
        log.warning("Could not resolve current user for Lakebase: %s", exc)
        return ""


@contextmanager
def connect(w: WorkspaceClient):
    """Yield a psycopg connection to Lakebase, search_path set to the abac schema."""
    conn = psycopg.connect(
        host=HOST, port=5432, dbname=DB,
        user=_resolve_user(w), password=_pg_password(w),
        sslmode="require", connect_timeout=30,
    )
    try:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(f"SET search_path TO {SCHEMA}")
        conn.commit()
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = [
    # Transitive group membership: one row per (principal, group) edge, any depth.
    """
    CREATE TABLE IF NOT EXISTS perm_identity_groups (
        principal_id   TEXT NOT NULL,
        principal_name TEXT,
        group_id       TEXT NOT NULL,
        group_name     TEXT,
        snapshot_ts    TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (principal_id, group_id)
    )
    """,
    # Resolve opaque group UUIDs (as they appear in information_schema grantee).
    """
    CREATE TABLE IF NOT EXISTS perm_group_uuid_map (
        group_id   TEXT PRIMARY KEY,
        group_name TEXT,
        snapshot_ts TIMESTAMPTZ NOT NULL
    )
    """,
    # Flat ACL rows across every object type (UC + workspace objects).
    """
    CREATE TABLE IF NOT EXISTS perm_object_acls (
        object_type  TEXT NOT NULL,
        object_name  TEXT NOT NULL,
        object_id    TEXT,
        grantee      TEXT NOT NULL,
        grantee_kind TEXT,
        permission   TEXT NOT NULL,
        snapshot_ts  TIMESTAMPTZ NOT NULL
    )
    """,
    # Snapshot run bookkeeping (drives the "last refreshed" badge).
    """
    CREATE TABLE IF NOT EXISTS perm_snapshot_runs (
        run_id      TEXT PRIMARY KEY,
        started_at  TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        status      TEXT,
        uc_rows     INTEGER,
        acl_rows    INTEGER,
        group_rows  INTEGER,
        error       TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_acls_grantee ON perm_object_acls (grantee)",
    "CREATE INDEX IF NOT EXISTS idx_groups_principal ON perm_identity_groups (principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_groups_principal_name ON perm_identity_groups (principal_name)",
]


def ensure_schema(conn) -> None:
    """Create all snapshot tables + indexes (idempotent)."""
    for stmt in _DDL:
        conn.execute(stmt)
    conn.commit()


# ---------------------------------------------------------------------------
# Writes (full-replace per snapshot — this is a rebuilt cache, not an edit log)
# ---------------------------------------------------------------------------

def replace_acls(conn, rows: list[dict], snapshot_ts) -> int:
    conn.execute("TRUNCATE perm_object_acls")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO perm_object_acls "
            "(object_type,object_name,object_id,grantee,grantee_kind,permission,snapshot_ts) "
            "VALUES (%(object_type)s,%(object_name)s,%(object_id)s,%(grantee)s,"
            "%(grantee_kind)s,%(permission)s,%(ts)s)",
            [{"object_type": r.get("object_type"), "object_name": r.get("object_name"),
              "object_id": r.get("object_id"), "grantee": r.get("grantee"),
              "grantee_kind": r.get("grantee_kind"), "permission": r.get("permission"),
              "ts": snapshot_ts} for r in rows],
        )
    conn.commit()
    return len(rows)


def replace_identity_groups(conn, rows: list[dict], snapshot_ts) -> int:
    # Full rebuild each run; TRUNCATE then bulk insert (no per-row upsert needed).
    conn.execute("TRUNCATE perm_identity_groups")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO perm_identity_groups "
            "(principal_id,principal_name,group_id,group_name,snapshot_ts) "
            "VALUES (%(principal_id)s,%(principal_name)s,%(group_id)s,%(group_name)s,%(ts)s)",
            [{**r, "ts": snapshot_ts} for r in rows],
        )
    conn.commit()
    return len(rows)


def replace_group_uuid_map(conn, group_name_by_id: dict[str, str], snapshot_ts) -> int:
    conn.execute("TRUNCATE perm_group_uuid_map")
    with conn.cursor() as cur:
        for gid, name in group_name_by_id.items():
            cur.execute(
                "INSERT INTO perm_group_uuid_map (group_id,group_name,snapshot_ts) "
                "VALUES (%s,%s,%s) ON CONFLICT (group_id) DO UPDATE SET "
                "group_name=EXCLUDED.group_name, snapshot_ts=EXCLUDED.snapshot_ts",
                (gid, name, snapshot_ts),
            )
    conn.commit()
    return len(group_name_by_id)


def record_run(conn, run_id, started_at, finished_at, status, uc_rows, acl_rows, group_rows, error=None) -> None:
    conn.execute(
        "INSERT INTO perm_snapshot_runs "
        "(run_id,started_at,finished_at,status,uc_rows,acl_rows,group_rows,error) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO UPDATE SET "
        "finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, uc_rows=EXCLUDED.uc_rows, "
        "acl_rows=EXCLUDED.acl_rows, group_rows=EXCLUDED.group_rows, error=EXCLUDED.error",
        (run_id, started_at, finished_at, status, uc_rows, acl_rows, group_rows, error),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reads (Permission Explorer)
# ---------------------------------------------------------------------------

def last_snapshot_ts(conn):
    """Timestamp of the most recent successful snapshot, or None."""
    row = conn.execute(
        "SELECT finished_at FROM perm_snapshot_runs WHERE status='success' "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def fetch_user_permissions(conn, user_identifiers: list[str]) -> list[dict]:
    """Return every ACL row granted to the user directly OR via any of their groups.

    user_identifiers = the user's own login/email/scim-id PLUS every group id and
    group name from perm_identity_groups. One indexed query, no API calls.
    """
    if not user_identifiers:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT object_type, object_name, grantee, grantee_kind, permission "
            "FROM perm_object_acls WHERE grantee = ANY(%s)",
            (user_identifiers,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_user_group_identifiers(conn, principal_id: str, principal_name: str) -> tuple[set[str], list[dict]]:
    """Return (set of group ids+names the user belongs to, group detail rows)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT group_id, group_name FROM perm_identity_groups "
            "WHERE principal_id=%s OR principal_name=%s",
            (principal_id, principal_name),
        )
        rows = cur.fetchall()
    ids: set[str] = set()
    detail: list[dict] = []
    for gid, gname in rows:
        if gid:
            ids.add(gid)
        if gname:
            ids.add(gname)
        detail.append({"group_id": gid, "group_name": gname})
    return ids, detail
