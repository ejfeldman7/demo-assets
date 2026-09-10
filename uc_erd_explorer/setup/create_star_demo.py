"""Create a synthetic DIMENSIONAL (star-schema) demo catalog ("retail_star") for the ERD
viewer -- a small retail-sales star with conformed dimensions, two facts sharing them, and
a promotion/product bridge. Named with fact_/dim_/bridge_ conventions so the Star layout
mode classifies every table on naming and centers fact_sales automatically.

This complements the normalized megacorp/logistics demo (see create_megacorp_demo.py):
that one has no dim_/fact_ names and exercises the classifier's structural fallback; this
one is an explicit star. Structure only, no rows.

setup/star_demo_schema.sql uses "retail_star" as a single literal catalog placeholder that
this script substitutes at run time, so the same file can build the catalog under any name
(e.g. retail_star_ts for the Prod/Test toggle's test catalogs).

Usage:
  uv run --with databricks-sdk python setup/create_star_demo.py \
      --warehouse-id <your-warehouse-id> --profile <your-profile> [--catalog retail_star]
"""
import argparse
import os
import re
import sys

from databricks.sdk import WorkspaceClient

sys.path.insert(0, os.path.dirname(__file__))
from run_ddl import split_statements  # noqa: E402

SETUP_DIR = os.path.dirname(__file__)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def substitute_catalog(sql_text: str, catalog: str, include_create_catalog: bool) -> str:
    """Replace the "retail_star" placeholder -- both the "retail_star." qualifier on every
    three-part identifier and the "CREATE CATALOG IF NOT EXISTS retail_star" statement. Kept
    narrow (the qualifier and the CREATE statement) so it never rewrites prose in comments
    that merely mention the name."""
    sql_text = re.sub(r"\bretail_star\.", f"{catalog}.", sql_text)
    sql_text = re.sub(
        r"CREATE CATALOG IF NOT EXISTS retail_star\b",
        f"CREATE CATALOG IF NOT EXISTS {catalog}",
        sql_text,
    )
    if not include_create_catalog:
        # Drop CREATE CATALOG entirely when the target already exists, so a deployer who has
        # rights only on an existing catalog (not metastore-level CREATE CATALOG) can still
        # add these schemas/tables -- same rationale as create_megacorp_demo.py.
        sql_text = re.sub(rf"CREATE CATALOG IF NOT EXISTS {re.escape(catalog)}[^;]*;", "", sql_text)
    return sql_text


def catalog_exists(w: WorkspaceClient, warehouse_id: str, catalog: str) -> bool:
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"SELECT 1 FROM system.information_schema.catalogs WHERE catalog_name = '{catalog}'",
        wait_timeout="30s",
    )
    return bool(resp.result and resp.result.data_array)


def run_statements(w: WorkspaceClient, warehouse_id: str, statements: list[str]) -> None:
    for i, stmt in enumerate(statements, 1):
        label = stmt.strip().splitlines()[0][:70]
        print(f"[{i}/{len(statements)}] {label}...", end=" ", flush=True)
        resp = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=stmt, wait_timeout="50s")
        if resp.status.state.value != "SUCCEEDED":
            print(f"FAILED: {resp.status.error}")
            print(f"Statement was:\n{stmt}")
            sys.exit(1)
        print("ok")


def _validated(catalog: str) -> str:
    catalog = catalog.strip()
    if not _IDENTIFIER_RE.match(catalog):
        raise ValueError(f"Invalid catalog name: {catalog!r} (must be a plain identifier)")
    return catalog


def create_star_demo(w: WorkspaceClient, warehouse_id: str, catalog: str) -> None:
    """Create the synthetic star-schema demo data in `catalog` -- creating the catalog
    itself only if it doesn't already exist (see module docstring)."""
    catalog = _validated(catalog)
    exists = catalog_exists(w, warehouse_id, catalog)
    print(
        f"Target catalog: {catalog} "
        f"({'already exists -- using as-is' if exists else 'does not exist -- will create it'})"
    )

    with open(os.path.join(SETUP_DIR, "star_demo_schema.sql")) as f:
        schema_sql = substitute_catalog(f.read(), catalog, include_create_catalog=not exists)
    run_statements(w, warehouse_id, split_statements(schema_sql))
    print(f"\nStar-schema demo catalog ready in {catalog}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute) or your CLI's DEFAULT profile.")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="retail_star", help='Target catalog for the star-schema demo data. Created if it doesn\'t exist; used as-is (no CREATE CATALOG) if it does. Default: "retail_star".')
    args = parser.parse_args()

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    try:
        create_star_demo(w, args.warehouse_id, args.catalog)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
