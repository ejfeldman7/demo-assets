"""
grant_app_sp_pg.py — grant the app service principal's Lakebase (Postgres) role the
privileges it needs on the Watchtower schema.

The role itself is created by the control-plane `databricks postgres create-role` command
(federated — see setup/setup.sh), which starts with default privileges only. This script,
run as the schema OWNER, GRANTs the schema/table/sequence privileges on top. The federated
role's Postgres name equals the SP's application (client) id — that's the identity binding.

Do NOT `CREATE ROLE` here: a manually created role is non-federated and the OAuth gateway
rejects its token.

Env (exported by setup/setup.sh):
  DATABRICKS_CONFIG_PROFILE, LAKEBASE_ENDPOINT, LAKEBASE_HOST, LAKEBASE_SCHEMA
Arg:
  --app-sp <application-id>   the app's SP client id (== its Postgres role name)
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from databricks.sdk import WorkspaceClient
from psycopg import sql

# reuse the app's Lakebase connection helper
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src" / "db"))
import lakebase  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("grant-app-sp")


def grant(w: WorkspaceClient, app_sp: str) -> None:
    role = sql.Identifier(app_sp)
    schema = sql.Identifier(lakebase.SCHEMA)
    with lakebase.connect(w) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_sp,))
        if cur.fetchone() is None:
            raise SystemExit(
                f"Postgres role '{app_sp}' not found. Federate the app SP first with "
                f"`databricks postgres create-role` (see setup/setup.sh), then re-run.")
        for stmt in (
            sql.SQL("GRANT USAGE ON SCHEMA {s} TO {r}"),
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {r}"),
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO {r}"),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {s} "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}"),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {s} "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {r}"),
        ):
            cur.execute(stmt.format(s=schema, r=role))
    log.info("granted app SP %s privileges on schema %s", app_sp, lakebase.SCHEMA)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-sp", required=True, help="app SP application (client) id")
    args = ap.parse_args()
    grant(WorkspaceClient(), args.app_sp)


if __name__ == "__main__":
    main()
