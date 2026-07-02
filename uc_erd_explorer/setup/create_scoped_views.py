"""Create scoped Unity Catalog views over system.information_schema, hard-filtered to
the approved catalog allow-list, plus denormalized ERD helper views.

This is the actual access boundary for the Genie Space: Genie is built ONLY on these
views (never on system.information_schema directly), so what it can see is enforced by
the view's own WHERE clause + normal UC grants on this schema -- not by Genie
instructions, which are soft guidance a determined prompt could talk around.

Views created in {metadata_catalog}.{metadata_schema} (see config.get_metadata_location,
env var ERD_METADATA_LOCATION="catalog.schema", default "<first ERD_CATALOGS entry>.erd_meta"):

  Internal (not exposed to Genie directly -- other views are built on these):
    scoped_tables, scoped_columns, scoped_table_constraints,
    scoped_key_column_usage, scoped_referential_constraints

  Denormalized (these 4 are what actually get curated into the Genie Space):
    primary_keys      -- one row per PK column
    fk_edges          -- one row per FK->PK relationship (the join done once, here)
    column_inventory  -- every column + is_primary_key/is_foreign_key flags
    table_summary     -- one row per table w/ column/PK/outgoing-FK/incoming-FK counts

Usage:
  uv run --with databricks-sdk python setup/create_scoped_views.py \
      --profile <your-profile> --warehouse-id <your-warehouse-id> \
      --catalogs megacorp --metadata-location megacorp.erd_meta
"""
import argparse
import os
import re

from databricks.sdk import WorkspaceClient

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_identifiers(values, kind):
    bad = [v for v in values if not _IDENTIFIER_RE.match(v)]
    if bad:
        raise ValueError(f"Invalid {kind} name(s): {bad}")
    return values


def _in_clause(values):
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def build_statements(catalogs: list, metadata_catalog: str, metadata_schema: str) -> list:
    catalogs = _validate_identifiers(catalogs, "catalog")
    _validate_identifiers([metadata_catalog, metadata_schema], "metadata catalog/schema")
    if not catalogs:
        raise ValueError("At least one catalog is required")

    cat_in = _in_clause(catalogs)
    loc = f"{metadata_catalog}.{metadata_schema}"
    catalog_list_str = ", ".join(catalogs)
    # Exclude the metadata schema's own housekeeping views from the "what tables exist"
    # results -- without this, this schema's 9 views would show up as fake business
    # tables in table_summary/scoped_tables every time it's (re)created.
    excluded_schemas = "'information_schema', '{}'".format(metadata_schema)

    stmts = []

    stmts.append(
        f"CREATE SCHEMA IF NOT EXISTS {loc} "
        f"COMMENT 'Scoped ERD metadata views for the Interactive ERD Viewer -- "
        f"hard-scoped to catalogs: {catalog_list_str}. Not business data.'"
    )

    # --- internal scoped views (1:1 filtered mirrors of system.information_schema) ---

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.scoped_tables COMMENT 'Tables in the approved catalogs only ({catalog_list_str}). Internal -- see table_summary.' AS
SELECT table_catalog, table_schema, table_name, table_type, comment
FROM system.information_schema.tables
WHERE table_catalog IN {cat_in}
  AND table_schema NOT IN ({excluded_schemas})
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.scoped_columns COMMENT 'Columns in the approved catalogs only ({catalog_list_str}). Internal -- see column_inventory.' AS
SELECT table_catalog, table_schema, table_name, column_name, ordinal_position,
       full_data_type, is_nullable, comment
FROM system.information_schema.columns
WHERE table_catalog IN {cat_in}
  AND table_schema NOT IN ({excluded_schemas})
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.scoped_table_constraints COMMENT 'PRIMARY/FOREIGN KEY constraints in the approved catalogs only. Internal -- see fk_edges/primary_keys.' AS
SELECT constraint_catalog, constraint_schema, constraint_name, constraint_type,
       table_catalog, table_schema, table_name
FROM system.information_schema.table_constraints
WHERE table_catalog IN {cat_in}
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.scoped_key_column_usage COMMENT 'Columns participating in constraints, approved catalogs only. Internal -- see fk_edges/primary_keys.' AS
SELECT constraint_catalog, constraint_schema, constraint_name,
       table_catalog, table_schema, table_name, column_name,
       ordinal_position, position_in_unique_constraint
FROM system.information_schema.key_column_usage
WHERE table_catalog IN {cat_in}
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.scoped_referential_constraints COMMENT 'FK constraint -> referenced PK constraint links, approved catalogs only. Internal -- see fk_edges.' AS
SELECT constraint_catalog, constraint_schema, constraint_name,
       unique_constraint_catalog, unique_constraint_schema, unique_constraint_name
FROM system.information_schema.referential_constraints
WHERE constraint_catalog IN {cat_in}
""".strip())

    # --- denormalized ERD helper views (built on the scoped views above, never touch
    # system.information_schema again -- these are the ones curated into the Genie Space) ---

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.primary_keys COMMENT 'One row per primary-key column, approved catalogs only ({catalog_list_str}).' AS
SELECT kcu.table_catalog, kcu.table_schema, kcu.table_name, kcu.column_name, kcu.ordinal_position
FROM {loc}.scoped_table_constraints tc
JOIN {loc}.scoped_key_column_usage kcu
  ON tc.constraint_catalog = kcu.constraint_catalog AND tc.constraint_schema = kcu.constraint_schema
 AND tc.constraint_name = kcu.constraint_name
WHERE tc.constraint_type = 'PRIMARY KEY'
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.fk_edges COMMENT 'Denormalized FK -> PK edge list (which table/column references which table/column), approved catalogs only ({catalog_list_str}).' AS
SELECT fk.table_catalog AS fk_catalog, fk.table_schema AS fk_schema, fk.table_name AS fk_table,
       fkc.column_name AS fk_column,
       pk.table_catalog AS pk_catalog, pk.table_schema AS pk_schema, pk.table_name AS pk_table,
       pkc.column_name AS pk_column,
       ref.constraint_name
FROM {loc}.scoped_referential_constraints ref
JOIN {loc}.scoped_table_constraints fk
  ON ref.constraint_catalog = fk.constraint_catalog AND ref.constraint_schema = fk.constraint_schema
 AND ref.constraint_name = fk.constraint_name AND fk.constraint_type = 'FOREIGN KEY'
JOIN {loc}.scoped_key_column_usage fkc
  ON fk.constraint_catalog = fkc.constraint_catalog AND fk.constraint_schema = fkc.constraint_schema
 AND fk.constraint_name = fkc.constraint_name
JOIN {loc}.scoped_table_constraints pk
  ON ref.unique_constraint_catalog = pk.constraint_catalog AND ref.unique_constraint_schema = pk.constraint_schema
 AND ref.unique_constraint_name = pk.constraint_name AND pk.constraint_type = 'PRIMARY KEY'
JOIN {loc}.scoped_key_column_usage pkc
  ON pk.constraint_catalog = pkc.constraint_catalog AND pk.constraint_schema = pkc.constraint_schema
 AND pk.constraint_name = pkc.constraint_name AND fkc.position_in_unique_constraint = pkc.ordinal_position
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.column_inventory COMMENT 'Every column with is_primary_key/is_foreign_key flags, approved catalogs only ({catalog_list_str}).' AS
SELECT c.table_catalog, c.table_schema, c.table_name, c.column_name, c.full_data_type,
       c.ordinal_position,
       pk.column_name IS NOT NULL AS is_primary_key,
       fk.fk_column IS NOT NULL AS is_foreign_key
FROM {loc}.scoped_columns c
LEFT JOIN {loc}.primary_keys pk
  ON c.table_catalog = pk.table_catalog AND c.table_schema = pk.table_schema
 AND c.table_name = pk.table_name AND c.column_name = pk.column_name
LEFT JOIN (SELECT DISTINCT fk_catalog, fk_schema, fk_table, fk_column FROM {loc}.fk_edges) fk
  ON c.table_catalog = fk.fk_catalog AND c.table_schema = fk.fk_schema
 AND c.table_name = fk.fk_table AND c.column_name = fk.fk_column
""".strip())

    stmts.append(f"""
CREATE OR REPLACE VIEW {loc}.table_summary COMMENT 'One row per table with column/PK/FK counts, approved catalogs only ({catalog_list_str}). Filter outgoing_foreign_key_count = 0 for tables with no declared foreign keys.' AS
SELECT
  t.table_catalog, t.table_schema, t.table_name, t.table_type,
  (SELECT COUNT(*) FROM {loc}.scoped_columns c
    WHERE c.table_catalog = t.table_catalog AND c.table_schema = t.table_schema AND c.table_name = t.table_name
  ) AS column_count,
  (SELECT COUNT(*) FROM {loc}.primary_keys pk
    WHERE pk.table_catalog = t.table_catalog AND pk.table_schema = t.table_schema AND pk.table_name = t.table_name
  ) AS primary_key_column_count,
  (SELECT COUNT(DISTINCT constraint_name) FROM {loc}.fk_edges fk
    WHERE fk.fk_catalog = t.table_catalog AND fk.fk_schema = t.table_schema AND fk.fk_table = t.table_name
  ) AS outgoing_foreign_key_count,
  (SELECT COUNT(DISTINCT constraint_name) FROM {loc}.fk_edges fk
    WHERE fk.pk_catalog = t.table_catalog AND fk.pk_schema = t.table_schema AND fk.pk_table = t.table_name
  ) AS incoming_foreign_key_count
FROM {loc}.scoped_tables t
""".strip())

    return stmts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute).")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalogs", default="")
    parser.add_argument("--metadata-location", default="", help='"catalog.schema", e.g. megacorp.erd_meta')
    args = parser.parse_args()

    catalogs = [c.strip() for c in (args.catalogs or os.environ.get("ERD_CATALOGS", "")).split(",") if c.strip()]
    if not catalogs:
        catalogs = ["megacorp"]

    loc_raw = args.metadata_location or os.environ.get("ERD_METADATA_LOCATION", "")
    if loc_raw and "." in loc_raw:
        metadata_catalog, metadata_schema = loc_raw.split(".", 1)
    else:
        metadata_catalog, metadata_schema = catalogs[0], "erd_meta"

    print(f"Scoping views to catalogs: {catalogs}")
    print(f"Creating views in: {metadata_catalog}.{metadata_schema}")

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    statements = build_statements(catalogs, metadata_catalog, metadata_schema)

    for i, stmt in enumerate(statements, 1):
        label = stmt.strip().splitlines()[0][:70]
        print(f"[{i}/{len(statements)}] {label}...", end=" ", flush=True)
        resp = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id, statement=stmt, wait_timeout="50s"
        )
        if resp.status.state.value != "SUCCEEDED":
            print(f"FAILED: {resp.status.error}\n{stmt}")
            raise SystemExit(1)
        print("ok")

    print(f"\nAll {len(statements)} view statements succeeded in {metadata_catalog}.{metadata_schema}.")


if __name__ == "__main__":
    main()
