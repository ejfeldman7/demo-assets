"""Create a second synthetic demo catalog ("logistics") that cross-links to the megacorp
demo catalog (see create_megacorp_demo.py) -- for exercising the ERD viewer's
multi-catalog rendering over a real cross-catalog foreign key
(logistics.shipping.shipments -> megacorp.erp.sales_orders), not just multiple schemas
within one catalog.

setup/logistics_schema.sql is a plain, readable .sql file with "logistics" (this
catalog) and "megacorp" (the catalog it cross-links to) as independent literal
placeholders -- this script substitutes both at run time, so the same file builds either
pairing:
  - prod:  logistics    -> megacorp
  - test:  logistics_ts -> megacorp_ts
without a test-environment catalog ever accidentally referencing a prod one.

Usage:
  uv run --with databricks-sdk python setup/create_logistics_demo.py \
      --warehouse-id <your-warehouse-id> --profile <your-profile> \
      [--catalog logistics] [--megacorp-catalog megacorp]
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


def substitute_catalogs(sql_text: str, catalog: str, megacorp_catalog: str, include_create_catalog: bool) -> str:
    """Replace the two literal placeholders -- "logistics." (this catalog) and
    "megacorp." (the catalog it cross-links to) -- independently, so prod and test can
    each get their own consistently-paired pair of catalogs without cross-wiring. Only
    touches "<name>." (a three-part-identifier qualifier) and the two "CREATE CATALOG IF
    NOT EXISTS <name>" statements themselves -- deliberately narrow so it never touches
    prose in comments/descriptions that happen to mention either name."""
    sql_text = re.sub(r"\blogistics\.", f"{catalog}.", sql_text)
    sql_text = re.sub(r"\bmegacorp\.", f"{megacorp_catalog}.", sql_text)
    sql_text = re.sub(
        r"CREATE CATALOG IF NOT EXISTS logistics\b",
        f"CREATE CATALOG IF NOT EXISTS {catalog}",
        sql_text,
    )
    if not include_create_catalog:
        # Drop the CREATE CATALOG statement entirely when the target catalog already
        # exists -- see create_megacorp_demo.py's substitute_catalog() for why (a
        # deployer who only has rights on an existing catalog, not metastore-level
        # CREATE CATALOG, shouldn't need the latter just to add schemas/tables).
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


def create_logistics_demo(w: WorkspaceClient, warehouse_id: str, catalog: str, megacorp_catalog: str) -> None:
    """Create the synthetic logistics demo data in `catalog`, cross-linked to
    `megacorp_catalog` -- creating `catalog` itself only if it doesn't already exist
    (see module docstring). `megacorp_catalog` must already exist with its demo schema
    (see create_megacorp_demo.py) since logistics's FKs reference it directly -- fails
    loudly rather than emitting FK constraints against tables that don't exist yet."""
    catalog = _validated(catalog)
    megacorp_catalog = _validated(megacorp_catalog)
    if not catalog_exists(w, warehouse_id, megacorp_catalog):
        raise SystemExit(
            f"{megacorp_catalog} does not exist yet -- run create_megacorp_demo.py "
            f"--catalog {megacorp_catalog} first (logistics's FKs reference it directly)."
        )
    exists = catalog_exists(w, warehouse_id, catalog)
    print(
        f"Target catalog: {catalog} "
        f"({'already exists -- using as-is' if exists else 'does not exist -- will create it'}), "
        f"cross-linked to {megacorp_catalog}"
    )

    with open(os.path.join(SETUP_DIR, "logistics_schema.sql")) as f:
        schema_sql = substitute_catalogs(f.read(), catalog, megacorp_catalog, include_create_catalog=not exists)
    run_statements(w, warehouse_id, split_statements(schema_sql))
    print(f"\nDemo catalog ready in {catalog} (cross-linked to {megacorp_catalog}).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute) or your CLI's DEFAULT profile.")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="logistics", help='Target catalog for the logistics demo data. Created if it doesn\'t exist; used as-is (no CREATE CATALOG) if it does. Default: "logistics".')
    parser.add_argument("--megacorp-catalog", default="megacorp", help='The megacorp-demo catalog this one cross-links to via foreign keys -- must already exist. Default: "megacorp". Pass "megacorp_ts" when building the test-environment pairing.')
    args = parser.parse_args()

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    try:
        create_logistics_demo(w, args.warehouse_id, args.catalog, args.megacorp_catalog)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
