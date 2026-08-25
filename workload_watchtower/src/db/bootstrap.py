"""
bootstrap.py — one-time Lakebase setup for Workload Watchtower.

Run ONCE as the Lakebase schema owner (locally, with your CLI profile). It:
  1. prints the Postgres identity/role model (so identity wiring is verifiable, not guessed),
  2. creates the schema + tables (schema.sql, schema name from LAKEBASE_SCHEMA),
  3. seeds the default governance rules,
  4. optionally seeds your IT/on-call roster from a JSON file (--members).

The app's service principal is federated + granted separately — see setup/setup.sh
(`databricks postgres create-role`) and setup/grant_app_sp_pg.py. Do NOT create the SP's
Postgres role here with a manual CREATE ROLE: that role is non-federated and the Lakebase
OAuth gateway rejects its token.

Usage:
  # source your config first so LAKEBASE_* / schema are set
  set -a && . setup/config.env && set +a
  python -m db.bootstrap [--members setup/it_members.json] [--skip-seed]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from databricks.sdk import WorkspaceClient

import lakebase

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bootstrap")

# Default governance rules. These are generic and safe to ship — tune thresholds, severity,
# and action per environment in the app's Rules tab after setup.
# (name, workload_type, metric, threshold, severity, action)
SEED_RULES = [
    ("Long-running query (30m)", "query", "elapsed_sec", 1800, "warning", "card_email"),
    ("Very long query (2h)", "query", "elapsed_sec", 7200, "critical", "card_email"),
    ("Long-running job (60m)", "job_run", "elapsed_sec", 3600, "warning", "card_email"),
    ("Long pipeline update (60m)", "pipeline", "elapsed_sec", 3600, "warning", "card"),
    ("Costly workload ($50 est.)", "query", "est_cost_usd", 50, "critical", "card_email"),
    # Governance: a user ran a session-level SET STATEMENT_TIMEOUT, overriding the workspace/warehouse
    # guardrail (session scope wins). Any such SET is flagged. Presence-based (threshold unused).
    # https://docs.databricks.com/aws/en/sql/language-manual/parameters/statement_timeout
    ("Session STATEMENT_TIMEOUT override", "timeout_override", "session_override", 0, "warning", "card_email"),
]


def inspect(w: WorkspaceClient) -> None:
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user, current_database(), version()")
        cu, db, ver = cur.fetchone()
        log.info("connected as=%s db=%s", cu, db)
        log.info("server=%s", ver.split(" on ")[0])
        cur.execute("SELECT schema_name FROM information_schema.schemata ORDER BY 1")
        log.info("schemas: %s", [r[0] for r in cur.fetchall()])


def _load_members(path: str | None) -> list[tuple[str, str, str]]:
    """Load the assignable roster from a JSON file: [{"name","email","role"}, ...].
    Roles: admin | triager | oncall. Returns [] if no file is given (roster optional —
    cards then start unassigned and can be assigned in the app)."""
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    return [(m["name"], m["email"], m.get("role", "triager")) for m in data]


def seed(w: WorkspaceClient, members: list[tuple[str, str, str]]) -> None:
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        for name, email, role in members:
            cur.execute(
                "INSERT INTO it_members (name, email, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (email) DO NOTHING",
                (name, email, role),
            )
        for name, wt, metric, thr, sev, action in SEED_RULES:
            cur.execute(
                "INSERT INTO rules (name, workload_type, metric, threshold, severity, action) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
                (name, wt, metric, thr, sev, action),
            )
        cur.execute("SELECT count(*) FROM it_members")
        m = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM rules")
        r = cur.fetchone()[0]
        log.info("seeded: %d members, %d rules", m, r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", metavar="PATH",
                    help="JSON file with the assignable roster (see setup/it_members.example.json)")
    ap.add_argument("--skip-seed", action="store_true", help="create schema/tables only, no rules/roster")
    args = ap.parse_args()

    w = WorkspaceClient()
    inspect(w)
    lakebase.bootstrap_schema(w)
    if not args.skip_seed:
        seed(w, _load_members(args.members))
    log.info("bootstrap complete.")


if __name__ == "__main__":
    main()
