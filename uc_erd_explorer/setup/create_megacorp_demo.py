"""Create the synthetic megacorp demo data -- structure only, no rows -- in a target
catalog, handling three cases:

  1. No --catalog given            -> use/create "megacorp" (the historical default).
  2. --catalog given, doesn't exist -> create it, then create the schema/tables in it.
  3. --catalog given, already exists -> use it as-is (skip CREATE CATALOG entirely) and
     just create the schemas/tables inside it. This matters because CREATE CATALOG
     requires metastore-level privilege that a deployer pointing at an existing catalog
     they don't own may not have (and shouldn't need) -- CREATE SCHEMA/TABLE within an
     existing catalog is a much lower bar.

setup/megacorp_schema.sql (and, if --with-metadata, setup/megacorp_demo_metadata.sql) are
kept as plain, readable .sql files with "megacorp" as a literal placeholder -- this script
substitutes the target catalog name into them at run time rather than requiring the SQL
files themselves to be Python string templates.

Usage:
  uv run --with databricks-sdk python setup/create_megacorp_demo.py \
      --warehouse-id <your-warehouse-id> --profile <your-profile> [--catalog my_catalog] \
      [--with-metadata]
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
    """Replace the literal "megacorp" catalog placeholder with the target catalog name.
    Only touches "megacorp." (used as a three-part-identifier qualifier) and the
    "CREATE CATALOG IF NOT EXISTS megacorp" statement itself -- deliberately narrow so it
    never touches prose in comments/descriptions that happen to mention "megacorp"."""
    sql_text = re.sub(r"\bmegacorp\.", f"{catalog}.", sql_text)
    sql_text = re.sub(
        r"CREATE CATALOG IF NOT EXISTS megacorp\b",
        f"CREATE CATALOG IF NOT EXISTS {catalog}",
        sql_text,
    )
    if not include_create_catalog:
        # Drop the CREATE CATALOG statement entirely when the target catalog already
        # exists -- running it anyway would be a harmless no-op on privilege grounds
        # *if* the caller has metastore-level CREATE CATALOG rights, but a deployer who
        # was only granted rights on an existing catalog (the common case) may not have
        # that privilege at all, and shouldn't need it just to add schemas/tables to a
        # catalog they already have access to.
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


def create_megacorp_demo(w: WorkspaceClient, warehouse_id: str, catalog: str, with_metadata: bool = False) -> None:
    """Create the synthetic megacorp demo data in `catalog` -- creating the catalog
    itself only if it doesn't already exist (see module docstring). Shared by both the
    CLI entry point below and the notebook install route, so the two can't drift apart.
    """
    catalog = _validated(catalog)
    exists = catalog_exists(w, warehouse_id, catalog)
    print(f"Target catalog: {catalog} ({'already exists -- using as-is' if exists else 'does not exist -- will create it'})")

    with open(os.path.join(SETUP_DIR, "megacorp_schema.sql")) as f:
        schema_sql = substitute_catalog(f.read(), catalog, include_create_catalog=not exists)
    run_statements(w, warehouse_id, split_statements(schema_sql))
    print(f"\nDemo catalog ready in {catalog}.")

    if with_metadata:
        add_demo_metadata(w, warehouse_id, catalog)


def add_demo_metadata(w: WorkspaceClient, warehouse_id: str, catalog: str) -> None:
    """Layer the illustrative comments/tags onto `catalog`'s demo data. Independent of
    create_megacorp_demo() on purpose -- callable on its own against a catalog that
    already has the demo structure from a previous run, without re-running that."""
    catalog = _validated(catalog)
    with open(os.path.join(SETUP_DIR, "megacorp_demo_metadata.sql")) as f:
        metadata_sql = substitute_catalog(f.read(), catalog, include_create_catalog=False)
    run_statements(w, warehouse_id, split_statements(metadata_sql))
    print(f"\nDemo metadata added in {catalog}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute) or your CLI's DEFAULT profile.")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="megacorp", help='Target catalog for the demo data. Created if it doesn\'t exist; used as-is (no CREATE CATALOG) if it does. Default: "megacorp".')
    parser.add_argument("--with-metadata", action="store_true", help="Also layer illustrative comments/tags onto the demo data (setup/megacorp_demo_metadata.sql). Independent opt-in -- off by default.")
    parser.add_argument("--metadata-only", action="store_true", help="Skip schema/table creation entirely and only run the metadata enrichment -- for adding it to a catalog that already has the demo structure from a previous run. Overrides --with-metadata.")
    args = parser.parse_args()

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    try:
        if args.metadata_only:
            add_demo_metadata(w, args.warehouse_id, args.catalog)
        else:
            create_megacorp_demo(w, args.warehouse_id, args.catalog, with_metadata=args.with_metadata)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
