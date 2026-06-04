"""
Lakebase Connection Helper for Brand Manager Forecasting Intelligence.

Provides connection pooling and helper functions for interacting with
the Lakebase PostgreSQL database using native Postgres auth.

Gracefully degrades if Lakebase is unavailable — query helpers return empty
DataFrames and inserts return None so the rest of the app keeps running.
"""

import json
import os
import logging
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.pool
import psycopg2.extras
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LAKEBASE_HOST = os.environ.get("LAKEBASE_HOST", "")
LAKEBASE_DB = os.environ.get("LAKEBASE_DB", "")
LAKEBASE_PORT = int(os.environ.get("LAKEBASE_PORT", "5432"))
LAKEBASE_USER = os.environ.get("PGUSER", "app_principal")
LAKEBASE_PASSWORD = os.environ.get("PGPASSWORD", "")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
LAKEBASE_AVAILABLE: bool = False


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return the connection pool, creating it if needed."""
    global _pool
    if _pool is not None:
        return _pool

    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=5,
        host=LAKEBASE_HOST,
        port=LAKEBASE_PORT,
        database=LAKEBASE_DB,
        user=LAKEBASE_USER,
        password=LAKEBASE_PASSWORD,
        sslmode="require",
        connect_timeout=15,
    )
    logger.info("Lakebase connection pool created for user=%s host=%s db=%s",
                LAKEBASE_USER, LAKEBASE_HOST, LAKEBASE_DB)
    return _pool


def _init_pool():
    """Try to establish the pool at startup. Sets LAKEBASE_AVAILABLE."""
    global LAKEBASE_AVAILABLE
    if not LAKEBASE_PASSWORD:
        logger.warning("PGPASSWORD not set — Lakebase disabled.")
        return
    try:
        pool = _get_pool()
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        finally:
            pool.putconn(conn)
        LAKEBASE_AVAILABLE = True
        logger.info("Lakebase connection verified — available.")
    except Exception as e:
        LAKEBASE_AVAILABLE = False
        logger.warning("Lakebase unavailable (app will run without persistence): %s", e)


@contextmanager
def get_connection():
    """Context manager that yields a psycopg2 connection from the pool."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# Query helpers (graceful degradation)
# ---------------------------------------------------------------------------

def execute_query(sql: str, params: tuple | list | None = None) -> pd.DataFrame:
    """Execute a SELECT query and return a pandas DataFrame."""
    if not LAKEBASE_AVAILABLE:
        return pd.DataFrame()
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                if not rows:
                    return pd.DataFrame()
                return pd.DataFrame(rows)
    except Exception as e:
        logger.error("Query failed: %s | SQL: %s", e, sql[:200])
        return pd.DataFrame()


def execute_insert(sql: str, params: tuple | list | None = None) -> Optional[Any]:
    """Execute an INSERT/UPDATE/DELETE and return the first column of the first
    returned row (useful for RETURNING clauses), or None."""
    if not LAKEBASE_AVAILABLE:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                try:
                    row = cur.fetchone()
                    return row[0] if row else None
                except psycopg2.ProgrammingError:
                    return None
    except Exception as e:
        logger.error("Insert/update failed: %s | SQL: %s", e, sql[:200])
        return None


def execute_many(sql: str, params_list: list[tuple]) -> int:
    """Execute a statement for many parameter sets. Returns affected row count."""
    if not LAKEBASE_AVAILABLE:
        return 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, params_list)
                return cur.rowcount
    except Exception as e:
        logger.error("executemany failed: %s | SQL: %s", e, sql[:200])
        return 0


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS bi_genie_spaces (
        space_id     VARCHAR PRIMARY KEY,
        name         VARCHAR NOT NULL UNIQUE,
        display_name VARCHAR NOT NULL,
        description  TEXT NOT NULL,
        is_active    BOOLEAN DEFAULT TRUE,
        created_at   TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bi_report_schedules (
        schedule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        schedule_type VARCHAR NOT NULL,
        report_name VARCHAR NOT NULL,
        report_question TEXT,
        alert_template VARCHAR,
        alert_threshold DOUBLE PRECISION,
        alert_scope_json JSONB,
        alert_cooldown_days INT DEFAULT 1,
        last_alert_sent_at TIMESTAMPTZ,
        cron_expression VARCHAR NOT NULL,
        recipients TEXT[],
        databricks_job_id BIGINT,
        is_active BOOLEAN DEFAULT TRUE,
        created_by VARCHAR,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bi_report_audit_log (
        run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        schedule_id UUID REFERENCES bi_report_schedules(schedule_id) ON DELETE SET NULL,
        run_started_at TIMESTAMPTZ DEFAULT now(),
        run_completed_at TIMESTAMPTZ,
        status VARCHAR NOT NULL,
        agent_plan_json JSONB,
        alert_breached BOOLEAN,
        alert_breach_value DOUBLE PRECISION,
        report_volume_path TEXT,
        email_sent_to TEXT[],
        email_sent_at TIMESTAMPTZ,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bi_conversation_sessions (
        session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_email VARCHAR NOT NULL,
        topic_tag VARCHAR,
        first_message_at TIMESTAMPTZ DEFAULT now(),
        last_message_at TIMESTAMPTZ DEFAULT now(),
        message_count INT DEFAULT 0,
        is_active BOOLEAN DEFAULT TRUE,
        summary TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bi_conversation_messages (
        message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES bi_conversation_sessions(session_id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ DEFAULT now(),
        role VARCHAR NOT NULL,
        content TEXT,
        plan_json JSONB,
        genie_calls_json JSONB,
        key_entities_json JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bi_agent_memory (
        memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        schedule_id     UUID NOT NULL,
        agent_run_id    UUID NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        narrative       TEXT,
        watching        JSONB NOT NULL DEFAULT '[]',
        resolved        JSONB NOT NULL DEFAULT '[]',
        findings_json   JSONB NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_memory_schedule
        ON bi_agent_memory (schedule_id, created_at DESC)
    """,
]


MIGRATION_STATEMENTS = [
    "ALTER TABLE bi_report_schedules ADD COLUMN IF NOT EXISTS claimed_by TEXT",
    "ALTER TABLE bi_report_schedules ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
    "ALTER TABLE bi_report_schedules ADD COLUMN IF NOT EXISTS report_type TEXT NOT NULL DEFAULT 'qa'",
    "ALTER TABLE bi_report_schedules ADD COLUMN IF NOT EXISTS genie_space_ids TEXT[] DEFAULT NULL",
]


GENIE_SEED_STATEMENTS = [
    """
    INSERT INTO bi_genie_spaces (space_id, name, display_name, description)
    VALUES (%s, 'demand', 'Demand Forecast Genie',
            'Contains forecast accuracy data, revenue projections, ai_forecast() model results, '
            'confidence intervals, customer-SKU demand patterns, actual vs predicted comparisons, '
            'and regional demand analytics.')
    ON CONFLICT (space_id) DO NOTHING
    """,
    """
    INSERT INTO bi_genie_spaces (space_id, name, display_name, description)
    VALUES (%s, 'inventory', 'Inventory & Channel Genie',
            'Contains inventory levels, days of supply, stockout risks, channel distribution data, '
            'warehouse coverage, replenishment schedules, and supply chain metrics.')
    ON CONFLICT (space_id) DO NOTHING
    """,
]

_DEMAND_GENIE_SPACE_ID = os.environ.get("DEMAND_GENIE_SPACE_ID", "")
_INVENTORY_GENIE_SPACE_ID = os.environ.get("INVENTORY_GENIE_SPACE_ID", "")


def init_schema():
    """Create tables if they do not exist. Safe to call multiple times."""
    if not LAKEBASE_AVAILABLE:
        logger.info("Skipping schema init — Lakebase not available.")
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for ddl in DDL_STATEMENTS:
                    cur.execute(ddl)
                for migration in MIGRATION_STATEMENTS:
                    try:
                        cur.execute(migration)
                    except Exception:
                        pass  # Column may already exist
                # Seed genie spaces
                cur.execute(GENIE_SEED_STATEMENTS[0], (_DEMAND_GENIE_SPACE_ID,))
                cur.execute(GENIE_SEED_STATEMENTS[1], (_INVENTORY_GENIE_SPACE_ID,))
        logger.info("Lakebase schema initialised successfully.")
    except Exception as e:
        logger.warning("Schema init failed (Lakebase may be unavailable): %s", e)


# ---------------------------------------------------------------------------
# Genie space helpers
# ---------------------------------------------------------------------------

def sync_genie_spaces_from_workspace(workspace_client=None) -> int:
    """Discover Genie spaces via SDK and upsert into the registry.

    New spaces are inserted as is_active=TRUE.  Existing rows get their
    display_name and description updated but is_active is left untouched
    so admin toggles are preserved.

    Returns the number of spaces synced.
    """
    if not LAKEBASE_AVAILABLE:
        return 0
    try:
        if workspace_client is None:
            from databricks.sdk import WorkspaceClient
            workspace_client = WorkspaceClient()

        response = workspace_client.genie.list_spaces()
        spaces = response.spaces if response and response.spaces else []
        if not spaces:
            logger.info("No Genie spaces returned from workspace API.")
            return 0

        count = 0
        import re
        # Pre-load existing names to avoid UNIQUE constraint violations
        existing_names_df = execute_query("SELECT name FROM bi_genie_spaces")
        seen_names = set(existing_names_df["name"].tolist()) if not existing_names_df.empty else set()
        with get_connection() as conn:
            with conn.cursor() as cur:
                for s in spaces:
                    # Derive a short name from the title (lowercase, underscores)
                    base_name = re.sub(r'[^a-z0-9]+', '_', (s.title or "").lower()).strip('_') or s.space_id[:12]
                    short_name = base_name
                    # Ensure uniqueness within this sync batch
                    suffix = 2
                    while short_name in seen_names:
                        short_name = f"{base_name}_{suffix}"
                        suffix += 1
                    seen_names.add(short_name)
                    cur.execute(
                        """
                        INSERT INTO bi_genie_spaces (space_id, name, display_name, description)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (space_id) DO UPDATE
                            SET display_name = EXCLUDED.display_name,
                                description  = EXCLUDED.description
                        """,
                        (s.space_id, short_name, s.title or short_name, s.description or ""),
                    )
                    count += 1
        logger.info("Synced %d Genie spaces from workspace.", count)
        return count
    except Exception as e:
        logger.warning("Failed to sync Genie spaces from workspace: %s", e)
        return 0


def get_all_genies() -> pd.DataFrame:
    """Return ALL genie spaces (active and inactive) for admin UI."""
    df = execute_query(
        "SELECT space_id, name, display_name, description, is_active FROM bi_genie_spaces ORDER BY display_name"
    )
    return df


def set_genie_active(space_id: str, is_active: bool) -> None:
    """Toggle is_active for a genie space."""
    execute_insert(
        "UPDATE bi_genie_spaces SET is_active = %s WHERE space_id = %s",
        (is_active, space_id),
    )


def get_active_genies() -> pd.DataFrame:
    """Return all active genie spaces as a DataFrame."""
    df = execute_query(
        "SELECT space_id, name, display_name, description FROM bi_genie_spaces WHERE is_active = TRUE ORDER BY name"
    )
    if df.empty:
        # Fallback: return the two default genies from env vars so the app works without Lakebase
        return pd.DataFrame([
            {"space_id": _DEMAND_GENIE_SPACE_ID, "name": "demand",
             "display_name": "Demand Forecast Genie",
             "description": "Contains forecast accuracy data, revenue projections, ai_forecast() model results, "
                            "confidence intervals, customer-SKU demand patterns, actual vs predicted comparisons, "
                            "and regional demand analytics."},
            {"space_id": _INVENTORY_GENIE_SPACE_ID, "name": "inventory",
             "display_name": "Inventory & Channel Genie",
             "description": "Contains inventory levels, days of supply, stockout risks, channel distribution data, "
                            "warehouse coverage, replenishment schedules, and supply chain metrics."},
        ])
    return df


def get_genies_by_ids(space_ids: list[str]) -> pd.DataFrame:
    """Return genie spaces filtered by a list of space_ids."""
    if not space_ids:
        return get_active_genies()
    placeholders = ",".join(["%s"] * len(space_ids))
    df = execute_query(
        f"SELECT space_id, name, display_name, description FROM bi_genie_spaces WHERE space_id IN ({placeholders}) ORDER BY name",
        tuple(space_ids),
    )
    if df.empty:
        return get_active_genies()
    return df


# ---------------------------------------------------------------------------
# Agent memory helpers
# ---------------------------------------------------------------------------

def get_latest_memory(schedule_id: str) -> Optional[dict]:
    """Return most recent memory row for a schedule, or None."""
    if not LAKEBASE_AVAILABLE:
        return None
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM bi_agent_memory WHERE schedule_id = %s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (schedule_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error("get_latest_memory failed: %s", e)
        return None


def write_memory(
    schedule_id: str,
    run_id: str,
    narrative: str,
    watching: list,
    resolved: list,
    findings: list,
) -> None:
    """Append a new memory row. Never updates existing rows."""
    if not LAKEBASE_AVAILABLE:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bi_agent_memory "
                    "(schedule_id, agent_run_id, narrative, watching, resolved, findings_json) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        schedule_id,
                        run_id,
                        narrative,
                        json.dumps(watching),
                        json.dumps(resolved),
                        json.dumps(findings),
                    ),
                )
    except Exception as e:
        logger.error("write_memory failed: %s", e)


# ---------------------------------------------------------------------------
# Monitoring query helpers
# ---------------------------------------------------------------------------

def get_run_health_summary(start, end) -> pd.DataFrame:
    """Aggregate run KPIs for the monitoring dashboard."""
    return execute_query(
        """
        SELECT
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE status = 'success') AS success_count,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
            COUNT(*) FILTER (WHERE status = 'cooldown') AS cooldown_count,
            COUNT(*) FILTER (WHERE status = 'no_breach') AS no_breach_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'success')
                  / NULLIF(COUNT(*), 0), 1) AS success_rate,
            ROUND(EXTRACT(EPOCH FROM AVG(run_completed_at - run_started_at)), 1)
                  AS avg_duration_sec
        FROM bi_report_audit_log
        WHERE run_started_at >= %s AND run_started_at < %s
        """,
        (start, end),
    )


def get_run_timeline(start, end) -> pd.DataFrame:
    """Daily run counts grouped by status for the timeline chart."""
    return execute_query(
        """
        SELECT DATE(run_started_at) AS run_date, status, COUNT(*) AS run_count
        FROM bi_report_audit_log
        WHERE run_started_at >= %s AND run_started_at < %s
        GROUP BY DATE(run_started_at), status
        ORDER BY run_date
        """,
        (start, end),
    )


def get_alert_activity(start, end) -> pd.DataFrame:
    """All alert-type runs with schedule metadata."""
    return execute_query(
        """
        SELECT
            ral.run_id, ral.schedule_id, ral.run_started_at, ral.status,
            ral.alert_breached, ral.alert_breach_value,
            rs.report_name, rs.alert_template, rs.alert_threshold,
            rs.alert_cooldown_days, rs.last_alert_sent_at
        FROM bi_report_audit_log ral
        JOIN bi_report_schedules rs ON ral.schedule_id = rs.schedule_id
        WHERE rs.schedule_type = 'alert'
          AND ral.run_started_at >= %s AND ral.run_started_at < %s
        ORDER BY ral.run_started_at DESC
        """,
        (start, end),
    )


def get_alert_summary_by_template(start, end) -> pd.DataFrame:
    """Per-template breach and cooldown counts."""
    return execute_query(
        """
        SELECT
            rs.alert_template,
            COUNT(*) AS total_checks,
            COUNT(*) FILTER (WHERE ral.alert_breached = TRUE) AS breaches,
            COUNT(*) FILTER (WHERE ral.status = 'cooldown') AS cooldowns,
            MAX(ral.run_started_at) FILTER (WHERE ral.alert_breached = TRUE) AS last_breach_at
        FROM bi_report_audit_log ral
        JOIN bi_report_schedules rs ON ral.schedule_id = rs.schedule_id
        WHERE rs.schedule_type = 'alert'
          AND ral.run_started_at >= %s AND ral.run_started_at < %s
        GROUP BY rs.alert_template
        ORDER BY breaches DESC
        """,
        (start, end),
    )


def get_agent_memory_summary(start, end) -> pd.DataFrame:
    """Proactive agent memory rows with watching/resolved JSONB."""
    return execute_query(
        """
        SELECT
            m.memory_id, m.schedule_id, m.agent_run_id, m.created_at,
            m.narrative, m.watching, m.resolved, m.findings_json,
            rs.report_name
        FROM bi_agent_memory m
        LEFT JOIN bi_report_schedules rs ON m.schedule_id = rs.schedule_id
        WHERE m.created_at >= %s AND m.created_at < %s
        ORDER BY m.created_at DESC
        """,
        (start, end),
    )


def get_schedule_performance(start, end) -> pd.DataFrame:
    """Per-schedule success/failure/duration stats."""
    return execute_query(
        """
        SELECT
            rs.schedule_id, rs.report_name, rs.schedule_type, rs.report_type,
            rs.cron_expression, rs.is_active,
            COUNT(ral.run_id) AS total_runs,
            COUNT(ral.run_id) FILTER (WHERE ral.status = 'success') AS successes,
            COUNT(ral.run_id) FILTER (WHERE ral.status = 'failed') AS failures,
            MAX(ral.run_started_at) AS last_run_at,
            ROUND(EXTRACT(EPOCH FROM AVG(ral.run_completed_at - ral.run_started_at)), 1)
                  AS avg_duration_sec
        FROM bi_report_schedules rs
        LEFT JOIN bi_report_audit_log ral
            ON rs.schedule_id = ral.schedule_id
            AND ral.run_started_at >= %s AND ral.run_started_at < %s
        GROUP BY rs.schedule_id, rs.report_name, rs.schedule_type, rs.report_type,
                 rs.cron_expression, rs.is_active
        ORDER BY total_runs DESC
        """,
        (start, end),
    )


# Attempt connection on import — sets LAKEBASE_AVAILABLE for the rest of the app
_init_pool()
