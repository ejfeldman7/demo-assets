"""Materialize the ERD metadata into physical Delta tables (a weekly-refreshed snapshot),
so the app can serve the graph WITHOUT running the expensive `system.information_schema`
joins (especially the 5-table FK join) on every load.

Writes six snapshot tables + a freshness marker into
{metadata_catalog}.{metadata_schema} (config.get_metadata_location, env
ERD_METADATA_LOCATION="catalog.schema", default "<first ERD_CATALOGS entry>.erd_meta") --
the SAME schema the Genie scoped views live in (setup/create_scoped_views.py):

  erd_snapshot_tables        -- (table_catalog, table_schema, table_name, comment)
  erd_snapshot_columns       -- (table_catalog, table_schema, table_name, column_name,
                                 full_data_type, ordinal_position, comment)
  erd_snapshot_primary_keys  -- (table_catalog, table_schema, table_name, column_name)
  erd_snapshot_foreign_keys  -- flattened FK->PK edges (the join done once, HERE)
  erd_snapshot_table_tags    -- (catalog_name, schema_name, table_name, tag_name, tag_value)
  erd_snapshot_column_tags   -- (+ column_name)
  erd_snapshot_meta          -- one row: refreshed_at, catalogs

Each table's columns EXACTLY match the corresponding server/graph.py `_query_*` SELECT, so
the app's snapshot read path is a straight swap of the FROM clause -- no reshaping. The
tables are scoped to the same catalog allow-list the app uses (or all visible catalogs in
unscoped mode) and exclude information_schema / the metadata schema itself / internal
`__`-prefixed catalogs, exactly like the live queries.

Run weekly (objects deploy ~weekly) via the refresh_erd_snapshot job (databricks.yml), or
manually:
  uv run --with databricks-sdk python setup/build_erd_snapshot.py \
      --profile <your-profile> --warehouse-id <your-warehouse-id> \
      --catalogs megacorp,logistics --metadata-location megacorp.erd_meta
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
    """Return a list of (label, sql, optional) statements. `optional=True` marks the tag
    snapshots, whose source system tables (table_tags/column_tags) don't exist on older
    metastores -- main() catches those failures and creates an empty table with the right
    columns instead, so the app's snapshot read still succeeds (and returns no tags),
    matching the live path's tolerant `try/except -> []` behavior.

    catalogs=[] means unscoped mode: snapshot every catalog visible to this job's
    credentials (UC privilege filtering still applies), mirroring the app's unscoped mode."""
    catalogs = _validate_identifiers(catalogs, "catalog")
    _validate_identifiers([metadata_catalog, metadata_schema], "metadata catalog/schema")

    loc = f"{metadata_catalog}.{metadata_schema}"
    catalog_list_str = ", ".join(catalogs) if catalogs else "ALL catalogs visible to this deployment"

    # Filters that mirror server/graph.py exactly. For the table/column/pk/tag sources the
    # catalog column is table_catalog / catalog_name; for the FK join it's constraint /
    # fk.table_catalog. Internal-schema exclusion drops information_schema, the metadata
    # schema itself (so its own snapshot tables never appear as "business tables"), and
    # `__`-prefixed internal catalogs.
    def cat_filter(col):
        return f"AND {col} IN {_in_clause(catalogs)}" if catalogs else ""

    def internal_excl(catalog_col, schema_col):
        return (
            f"{schema_col} != 'information_schema' "
            f"AND NOT ({catalog_col} = '{metadata_catalog}' AND {schema_col} = '{metadata_schema}') "
            f"AND substring({catalog_col}, 1, 2) != '__'"
        )

    stmts = []

    stmts.append((
        "schema",
        f"CREATE SCHEMA IF NOT EXISTS {loc} "
        f"COMMENT 'ERD metadata for the Interactive ERD Viewer (snapshot tables + Genie "
        f"views) -- scoped to catalogs: {catalog_list_str}. Not business data.'",
        False,
    ))

    stmts.append((
        "erd_snapshot_tables",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_tables
COMMENT 'ERD snapshot: tables in the approved catalogs ({catalog_list_str}). Refreshed by the refresh_erd_snapshot job.' AS
SELECT table_catalog, table_schema, table_name, comment
FROM system.information_schema.tables
WHERE {internal_excl("table_catalog", "table_schema")}
  {cat_filter("table_catalog")}
""".strip(),
        False,
    ))

    stmts.append((
        "erd_snapshot_columns",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_columns
COMMENT 'ERD snapshot: columns in the approved catalogs ({catalog_list_str}).' AS
SELECT table_catalog, table_schema, table_name, column_name, full_data_type, ordinal_position, comment
FROM system.information_schema.columns
WHERE {internal_excl("table_catalog", "table_schema")}
  {cat_filter("table_catalog")}
""".strip(),
        False,
    ))

    stmts.append((
        "erd_snapshot_primary_keys",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_primary_keys
COMMENT 'ERD snapshot: one row per primary-key column ({catalog_list_str}).' AS
SELECT kcu.table_catalog, kcu.table_schema, kcu.table_name, kcu.column_name
FROM system.information_schema.table_constraints tc
JOIN system.information_schema.key_column_usage kcu
  ON tc.constraint_catalog = kcu.constraint_catalog
 AND tc.constraint_schema  = kcu.constraint_schema
 AND tc.constraint_name    = kcu.constraint_name
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND {internal_excl("kcu.table_catalog", "kcu.table_schema")}
  {cat_filter("tc.constraint_catalog")}
""".strip(),
        False,
    ))

    # The FK edge list: the same 5-table join the app's _query_foreign_keys runs live --
    # done ONCE here so the app never pays it per request. Column names/order match
    # _query_foreign_keys exactly (incl. fkc.ordinal_position for multi-column FK grouping).
    stmts.append((
        "erd_snapshot_foreign_keys",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_foreign_keys
COMMENT 'ERD snapshot: flattened FK -> PK edges ({catalog_list_str}). The 5-table information_schema join, materialized.' AS
SELECT fk.table_catalog AS fk_catalog, fk.table_schema AS fk_schema, fk.table_name AS fk_table,
       fkc.column_name AS fk_column, fkc.ordinal_position,
       pk.table_catalog AS pk_catalog, pk.table_schema AS pk_schema, pk.table_name AS pk_table,
       pkc.column_name AS pk_column, ref.constraint_name
FROM system.information_schema.referential_constraints ref
JOIN system.information_schema.table_constraints fk
  ON ref.constraint_catalog=fk.constraint_catalog AND ref.constraint_schema=fk.constraint_schema
 AND ref.constraint_name=fk.constraint_name AND fk.constraint_type='FOREIGN KEY'
JOIN system.information_schema.key_column_usage fkc
  ON fk.constraint_catalog=fkc.constraint_catalog AND fk.constraint_schema=fkc.constraint_schema
 AND fk.constraint_name=fkc.constraint_name
JOIN system.information_schema.table_constraints pk
  ON ref.unique_constraint_catalog=pk.constraint_catalog AND ref.unique_constraint_schema=pk.constraint_schema
 AND ref.unique_constraint_name=pk.constraint_name AND pk.constraint_type='PRIMARY KEY'
JOIN system.information_schema.key_column_usage pkc
  ON pk.constraint_catalog=pkc.constraint_catalog AND pk.constraint_schema=pkc.constraint_schema
 AND pk.constraint_name=pkc.constraint_name AND fkc.position_in_unique_constraint=pkc.ordinal_position
WHERE 1=1
  {cat_filter("ref.constraint_catalog")}
""".strip(),
        False,
    ))

    stmts.append((
        "erd_snapshot_table_tags",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_table_tags
COMMENT 'ERD snapshot: table-level UC tags ({catalog_list_str}).' AS
SELECT catalog_name, schema_name, table_name, tag_name, tag_value
FROM system.information_schema.table_tags
WHERE {internal_excl("catalog_name", "schema_name")}
  {cat_filter("catalog_name")}
""".strip(),
        True,
    ))

    stmts.append((
        "erd_snapshot_column_tags",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_column_tags
COMMENT 'ERD snapshot: column-level UC tags ({catalog_list_str}).' AS
SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value
FROM system.information_schema.column_tags
WHERE {internal_excl("catalog_name", "schema_name")}
  {cat_filter("catalog_name")}
""".strip(),
        True,
    ))

    stmts.append((
        "erd_snapshot_meta",
        f"""
CREATE OR REPLACE TABLE {loc}.erd_snapshot_meta
COMMENT 'ERD snapshot freshness marker: when the snapshot was last rebuilt and for which catalogs.' AS
SELECT current_timestamp() AS refreshed_at, '{catalog_list_str}' AS catalogs
""".strip(),
        False,
    ))

    return stmts


# Empty-table fallback DDL (same columns as the CTAS) used when an optional tag source
# system table is unavailable on this metastore -- keeps the app's snapshot read working.
_EMPTY_TABLE_DDL = {
    "erd_snapshot_table_tags":
        "(catalog_name STRING, schema_name STRING, table_name STRING, tag_name STRING, tag_value STRING)",
    "erd_snapshot_column_tags":
        "(catalog_name STRING, schema_name STRING, table_name STRING, column_name STRING, tag_name STRING, tag_value STRING)",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job compute).")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalogs", default="")
    parser.add_argument("--metadata-location", default="", help='"catalog.schema", e.g. megacorp.erd_meta')
    args = parser.parse_args()

    catalogs = [c.strip() for c in (args.catalogs or os.environ.get("ERD_CATALOGS", "")).split(",") if c.strip()]
    loc_raw = args.metadata_location or os.environ.get("ERD_METADATA_LOCATION", "")
    if loc_raw and "." in loc_raw:
        metadata_catalog, metadata_schema = loc_raw.split(".", 1)
    elif catalogs:
        metadata_catalog, metadata_schema = catalogs[0], "erd_meta"
    else:
        raise SystemExit(
            "--metadata-location (or ERD_METADATA_LOCATION) is required when --catalogs "
            "is empty (unscoped mode) -- there's no catalog to default the snapshot into."
        )

    print(f"Snapshotting catalogs: {catalogs or 'ALL (unscoped)'}")
    print(f"Writing snapshot tables to: {metadata_catalog}.{metadata_schema}")

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    loc = f"{metadata_catalog}.{metadata_schema}"
    statements = build_statements(catalogs, metadata_catalog, metadata_schema)

    for i, (label, stmt, optional) in enumerate(statements, 1):
        print(f"[{i}/{len(statements)}] {label}...", end=" ", flush=True)
        resp = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id, statement=stmt, wait_timeout="50s"
        )
        if resp.status.state.value == "SUCCEEDED":
            print("ok")
            continue
        # An optional (tag) source can be missing on older metastores -- fall back to an
        # empty table with the right schema so the app's snapshot read still works.
        if optional and label in _EMPTY_TABLE_DDL:
            print(f"source unavailable ({resp.status.error.message if resp.status.error else 'error'}); creating empty table")
            empty = w.statement_execution.execute_statement(
                warehouse_id=args.warehouse_id,
                statement=f"CREATE OR REPLACE TABLE {loc}.{label} {_EMPTY_TABLE_DDL[label]}",
                wait_timeout="50s",
            )
            if empty.status.state.value != "SUCCEEDED":
                print(f"  FAILED creating empty table: {empty.status.error}")
                raise SystemExit(1)
            continue
        print(f"FAILED: {resp.status.error}\n{stmt}")
        raise SystemExit(1)

    print(f"\nAll {len(statements)} snapshot statements succeeded in {loc}.")


if __name__ == "__main__":
    main()
