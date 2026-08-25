"""
run_uc_ddl.py — create the Unity Catalog history schema + Delta tables + reconciliation view.

Reads src/db/uc_ddl.sql, substitutes {uc_schema} with $UC_SCHEMA (<catalog>.<schema>), and runs
each statement on the configured SQL warehouse via Statement Execution (no Spark needed).
Idempotent — every statement is CREATE ... IF NOT EXISTS / CREATE OR REPLACE.

Env (exported by setup/setup.sh):
  DATABRICKS_CONFIG_PROFILE, UC_SCHEMA, WT_WAREHOUSE_ID
"""

from __future__ import annotations

import os
import pathlib
import re

from databricks.sdk import WorkspaceClient

UC_SCHEMA = os.environ["UC_SCHEMA"]              # catalog.schema
WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
DDL_FILE = pathlib.Path(__file__).resolve().parent.parent / "src" / "db" / "uc_ddl.sql"

w = WorkspaceClient()


def run(stmt: str) -> None:
    resp = w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=WAREHOUSE_ID, wait_timeout="50s")
    state = resp.status.state.value if resp.status and resp.status.state else "?"
    if state != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else state
        raise RuntimeError(f"UC DDL failed ({state}): {err}\n{stmt[:200]}")


def statements(sql_text: str) -> list[str]:
    # Drop full-line SQL comments, then split on ';' (no semicolons appear inside literals here).
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def main() -> None:
    run(f"CREATE SCHEMA IF NOT EXISTS {UC_SCHEMA}")
    ddl = DDL_FILE.read_text().replace("{uc_schema}", UC_SCHEMA)
    for stmt in statements(ddl):
        run(stmt)
    print(f"UC history schema ready: {UC_SCHEMA} (workload_snapshots, alert_events, cost_reconciliation)")


if __name__ == "__main__":
    main()
