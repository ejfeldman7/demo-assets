"""
db.py — Lakebase Postgres module for the Dashboard Intake Builder.

Environment variables (auto-injected when the Lakebase resource is linked in app.yaml):
  PGHOST, PGDATABASE, PGUSER, PGPORT
Plus LAKEBASE_INSTANCE (set explicitly in app.yaml) — the Lakebase instance name
used for OAuth credential generation, distinct from PGDATABASE (the Postgres
database name inside that instance).

Auth: pooled psycopg connections (psycopg_pool.ConnectionPool). The pool mints a
fresh Lakebase OAuth credential (WorkspaceClient().database.generate_database_credential)
every time it opens a new physical connection — no shared/global token cache, no
manual refresh loop. Connections are recycled every 45 minutes (max_lifetime=2700s),
safely ahead of the ~1 hour token expiry.
"""

import os
import uuid
import json

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from databricks.sdk import WorkspaceClient

# ---------------------------------------------------------------------------
# Connection pool (created lazily on first use, not at import time, so a
# missing env var surfaces inside init_db()'s try/except in app.py rather
# than crashing the module import).
# ---------------------------------------------------------------------------
_ws_client: WorkspaceClient | None = None
_pool: ConnectionPool | None = None


def _get_ws() -> WorkspaceClient:
    global _ws_client
    if _ws_client is None:
        _ws_client = WorkspaceClient()
    return _ws_client


class _OAuthConnection(psycopg.Connection):
    """psycopg Connection that mints a fresh Lakebase OAuth token on every physical connect."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        instance_name = os.environ.get("LAKEBASE_INSTANCE", os.environ["PGDATABASE"])
        cred = _get_ws().database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[instance_name],
        )
        kwargs["password"] = cred.token
        return super().connect(conninfo, **kwargs)


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        conninfo = (
            f"host={os.environ['PGHOST']} "
            f"dbname={os.environ.get('PGDATABASE', 'databricks_postgres')} "
            f"user={os.environ['PGUSER']} "
            f"port={os.environ.get('PGPORT', 5432)} "
            "sslmode=require "
            "options='-c search_path=dashboard_intake_builder'"
        )
        _pool = ConnectionPool(
            conninfo=conninfo,
            connection_class=_OAuthConnection,
            min_size=1,
            max_size=10,
            max_lifetime=2700,
            open=True,
            timeout=30.0,
        )
    return _pool


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables if they do not already exist."""
    ddl = """
        CREATE SCHEMA IF NOT EXISTS dashboard_intake_builder;
        SET search_path TO dashboard_intake_builder;

        CREATE TABLE IF NOT EXISTS conversations (
            id            SERIAL PRIMARY KEY,
            session_id    VARCHAR(100) UNIQUE NOT NULL,
            report_name   VARCHAR(255),
            biz_question  TEXT,
            description   TEXT,
            key_metrics   TEXT,
            dimensions    TEXT,
            time_period   VARCHAR(50),
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          SERIAL PRIMARY KEY,
            session_id  VARCHAR(100) NOT NULL,
            role        VARCHAR(20)  NOT NULL,
            content     TEXT         NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dashboard_versions (
            id             SERIAL PRIMARY KEY,
            session_id     VARCHAR(100),
            report_name    VARCHAR(255),
            dashboard_id   VARCHAR(100),
            dashboard_url  TEXT,
            version_num    INT  NOT NULL DEFAULT 1,
            dashboard_json TEXT NOT NULL,
            description    TEXT,
            status         VARCHAR(50) DEFAULT 'published',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Maps session_id -> requesting user. Kept as its own table (rather than
        -- a created_by column on the tables above) because ALTER TABLE requires
        -- owning those tables, which isn't guaranteed for a pre-existing schema —
        -- a brand-new table the app creates itself has no such dependency.
        CREATE TABLE IF NOT EXISTS session_owners (
            session_id  VARCHAR(100) PRIMARY KEY,
            created_by  VARCHAR(255) NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """
    with _get_pool().connection() as conn:
        conn.execute(ddl)


def _claim_session(conn, session_id: str, created_by: str) -> None:
    """Record (once) which user a session_id belongs to, for history scoping."""
    conn.execute(
        """
        INSERT INTO session_owners (session_id, created_by)
        VALUES (%s, %s)
        ON CONFLICT (session_id) DO NOTHING
        """,
        (session_id, created_by),
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
def save_conversation(
    session_id: str,
    report_name: str,
    biz_question: str,
    description: str,
    key_metrics: str,
    dimensions: str,
    time_period: str,
    created_by: str,
) -> None:
    with _get_pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (session_id, report_name, biz_question, description, key_metrics, dimensions, time_period)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                report_name  = EXCLUDED.report_name,
                biz_question = EXCLUDED.biz_question,
                description  = EXCLUDED.description,
                key_metrics  = EXCLUDED.key_metrics,
                dimensions   = EXCLUDED.dimensions,
                time_period  = EXCLUDED.time_period
            """,
            (session_id, report_name, biz_question, description, key_metrics, dimensions, time_period),
        )
        _claim_session(conn, session_id, created_by)


# ---------------------------------------------------------------------------
# Messages (conversation memory)
# ---------------------------------------------------------------------------
def save_message(session_id: str, role: str, content: str) -> None:
    with _get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content),
        )


def get_messages(session_id: str) -> list[dict]:
    """Return ordered list of {role, content} dicts for a session."""
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT role, content FROM messages WHERE session_id = %s ORDER BY created_at ASC",
                (session_id,),
            )
            rows = cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------------------------------------------------------------------------
# Dashboard version control
# ---------------------------------------------------------------------------
def save_dashboard_version(
    session_id: str,
    report_name: str,
    dashboard_id: str,
    dashboard_url: str,
    dashboard_json,
    description: str = "",
    created_by: str = "unknown",
) -> int:
    """Persist a dashboard JSON snapshot; returns the new version number.

    version_num is computed and inserted in a single statement, serialized per
    session_id with a transaction-scoped advisory lock, so two concurrent
    builds for the same session (e.g. a double-clicked Build button) can't
    race to the same version number.
    """
    if isinstance(dashboard_json, dict):
        dashboard_json = json.dumps(dashboard_json, indent=2)
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (session_id,))
            cur.execute(
                """
                INSERT INTO dashboard_versions
                    (session_id, report_name, dashboard_id, dashboard_url,
                     version_num, dashboard_json, description, status)
                SELECT %s, %s, %s, %s, COALESCE(MAX(version_num), 0) + 1, %s, %s, 'published'
                FROM dashboard_versions
                WHERE session_id = %s
                RETURNING version_num
                """,
                (session_id, report_name, dashboard_id, dashboard_url,
                 dashboard_json, description, session_id),
            )
            next_ver = cur.fetchone()[0]
            _claim_session(conn, session_id, created_by)
        return next_ver


def get_all_dashboard_versions(created_by: str) -> list[dict]:
    """Return this user's versions ordered by most-recent first.

    Scoped via session_owners (joined on session_id) — business users must not
    see each other's dashboard history, business questions, or serialized JSON.
    """
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT dv.id, dv.report_name, dv.version_num, dv.status,
                       dv.dashboard_url, dv.description,
                       TO_CHAR(dv.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
                FROM dashboard_versions dv
                JOIN session_owners so ON so.session_id = dv.session_id
                WHERE so.created_by = %s
                ORDER BY dv.created_at DESC
                """,
                (created_by,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_dashboard_json(version_id: int, created_by: str) -> str | None:
    """Return dashboard_json only if version_id's session belongs to created_by."""
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dv.dashboard_json
                FROM dashboard_versions dv
                JOIN session_owners so ON so.session_id = dv.session_id
                WHERE dv.id = %s AND so.created_by = %s
                """,
                (version_id, created_by),
            )
            row = cur.fetchone()
        return row[0] if row else None
