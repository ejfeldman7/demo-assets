#!/usr/bin/env python3
"""Run a .sql file statement-by-statement against the <DATABRICKS_PROFILE> warehouse.

Usage: python3 scripts/run_sql.py sql/01_create_data.sql [--profile <DATABRICKS_PROFILE>] [--warehouse <id>]
"""
import argparse, os, re, sys
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

DEFAULT_WAREHOUSE = os.environ.get("WAREHOUSE_ID", "<WAREHOUSE_ID>")

def split_statements(sql_text: str):
    # strip line comments, then split on semicolons (no inner semicolons in our DDL)
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sql_file")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_PROFILE"))
    ap.add_argument("--warehouse", default=DEFAULT_WAREHOUSE)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    stmts = split_statements(open(args.sql_file).read())
    print(f"Running {len(stmts)} statements from {args.sql_file} on warehouse {args.warehouse}\n")
    for i, stmt in enumerate(stmts, 1):
        preview = re.sub(r"\s+", " ", stmt)[:90]
        r = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse, statement=stmt, wait_timeout="50s")
        state = r.status.state
        if state == StatementState.SUCCEEDED:
            print(f"[{i:>2}/{len(stmts)}] OK   {preview}")
        else:
            msg = r.status.error.message if r.status and r.status.error else state
            print(f"[{i:>2}/{len(stmts)}] FAIL {preview}\n      -> {msg}")
            sys.exit(1)
    print("\nAll statements succeeded.")

if __name__ == "__main__":
    main()
