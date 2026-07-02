"""Execute a .sql file statement-by-statement via the Databricks SQL Statement Execution API.

Usage: uv run setup/run_ddl.py <path-to-sql-file> [--profile PROFILE] [--warehouse-id ID]
"""
import argparse
import re
import sys

from databricks.sdk import WorkspaceClient


def split_statements(sql_text: str) -> list[str]:
    statements = []
    for raw in sql_text.split(";"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sql_file")
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute) or your CLI's DEFAULT profile.")
    parser.add_argument("--warehouse-id", required=True)
    args = parser.parse_args()

    with open(args.sql_file) as f:
        sql_text = f.read()

    statements = split_statements(sql_text)
    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()

    for i, stmt in enumerate(statements, 1):
        first_line = re.split(r"\s+", stmt.strip(), maxsplit=6)
        label = " ".join(first_line[:6])
        print(f"[{i}/{len(statements)}] {label}...", end=" ", flush=True)
        resp = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id,
            statement=stmt,
            wait_timeout="50s",
        )
        status = resp.status
        if status.state.value != "SUCCEEDED":
            print(f"FAILED: {status.error}")
            print(f"Statement was:\n{stmt}")
            sys.exit(1)
        print("ok")

    print(f"\nAll {len(statements)} statements succeeded.")


if __name__ == "__main__":
    main()
