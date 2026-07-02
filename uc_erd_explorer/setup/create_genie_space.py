"""Create (or update, idempotently) the Genie Space backing the ERD viewer's popup chat.

DATA MODELING FIRST: this Genie Space is built ONLY on 3 narrow, denormalized views --
table_summary, column_inventory, fk_edges -- in the scoped metadata schema created by
setup/create_scoped_views.py. Those views already hard-filter to the approved catalog
list via their own WHERE clause; Genie never sees system.information_schema or any raw
per-catalog information_schema directly. The access boundary lives in the UC objects
(views + grants), not in Genie instructions -- instructions here are just usage guidance
on top of that boundary, not the boundary itself.

Run setup/create_scoped_views.py FIRST -- this script assumes those views already exist.

Usage:
  uv run --with databricks-sdk python setup/create_scoped_views.py \
      --warehouse-id <your-warehouse-id> --catalogs megacorp --metadata-location megacorp.erd_meta
  uv run --with databricks-sdk python setup/create_genie_space.py \
      --warehouse-id <your-warehouse-id> --catalogs megacorp --metadata-location megacorp.erd_meta

Idempotency: finds an existing managed space via GET /api/2.0/genie/spaces, matching on
a stable marker in the description (NOT the title, which changes if the catalog list
changes) -- so re-running this after changing ERD_CATALOGS updates the same space in
place instead of creating a duplicate. This works identically whether run locally or as
a Databricks job task (job compute is ephemeral, so a local state file would not survive
between job runs -- this script intentionally has no local-file dependency).
IMPORTANT: if you change the catalog list later (ERD_CATALOGS / --catalogs), re-run BOTH
this script and create_scoped_views.py -- the graph API picks up a changed ERD_CATALOGS
live, but these views and the Genie Space's table list are saved configuration, not live.
"""
import argparse
import json
import os
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from genie_space_builder import GenieSpaceBuilder

# The 3 denormalized views curated into the space -- deliberately narrow per Databricks
# guidance that Genie Spaces perform best when small/focused. The 5 "scoped_*" raw
# mirror views and "primary_keys" exist only as internal plumbing these are built on.
CURATED_VIEWS = ["table_summary", "column_inventory", "fk_edges"]

# Column-level metadata for the 3 curated views. These views have a fixed schema
# regardless of which catalogs are in scope (see create_scoped_views.py), so this is
# generic, reusable guidance -- not specific to any one deployment's catalog/table names.
# enable_format_assistance/enable_entity_matching mirror what Databricks' own Genie
# Space quality checks recommend for text/identifier columns; low-cardinality enum-ish
# columns (table_type) skip entity matching since there's nothing to "match" against.
COLUMN_CONFIGS = {
    "table_summary": [
        ("table_catalog", None, True),
        ("table_schema", None, True),
        ("table_name", None, True),
        ("table_type", "The object type, e.g. TABLE or VIEW.", False),
        ("column_count", "Total number of columns in the table.", False),
        ("primary_key_column_count", "The number of columns in this table that are designated as part of the primary key constraint.", False),
        ("outgoing_foreign_key_count", "The number of foreign key constraints where this table holds the FK column pointing to another table's primary key.", False),
        ("incoming_foreign_key_count", "The number of foreign key constraints from other tables that reference this table's primary key.", False),
    ],
    "column_inventory": [
        ("table_catalog", None, True),
        ("table_schema", None, True),
        ("table_name", None, True),
        ("column_name", None, True),
        ("full_data_type", "The full SQL data type of the column, e.g. STRING, BIGINT, TIMESTAMP, or DECIMAL(18,2).", False),
        ("ordinal_position", "The column's 1-based position within its table.", False),
        ("is_primary_key", "Boolean flag indicating whether this column is part of the table's primary key constraint.", False),
        ("is_foreign_key", "Boolean flag indicating whether this column participates in a foreign key constraint referencing another table.", False),
    ],
    "fk_edges": [
        ("fk_catalog", None, True),
        ("fk_schema", None, True),
        ("fk_table", None, True),
        ("fk_column", None, True),
        ("pk_catalog", None, True),
        ("pk_schema", None, True),
        ("pk_table", None, True),
        ("pk_column", None, True),
        ("constraint_name", "The name of the foreign key constraint as declared in Unity Catalog metadata.", False),
    ],
}


def column_configs_for(view: str) -> list:
    """Build data_sources.tables[].column_configs for a curated view from COLUMN_CONFIGS."""
    configs = []
    for column_name, description, entity_match in COLUMN_CONFIGS[view]:
        entry = {
            "column_name": column_name,
            "enable_format_assistance": True,
        }
        if entity_match:
            entry["enable_entity_matching"] = True
        if description:
            entry["description"] = [description]
        configs.append(entry)
    # The API requires column_configs sorted by column_name (discovered via a real
    # InvalidParameterValue response, not documented).
    return sorted(configs, key=lambda c: c["column_name"])


def join_specs_for_views(loc: str) -> list:
    """The 3 joins that stitch the curated views together -- always valid regardless of
    catalog scope, since the views' column names never change."""
    return [
        {
            "left": {"identifier": f"{loc}.table_summary", "alias": "ts"},
            "right": {"identifier": f"{loc}.column_inventory", "alias": "ci"},
            "sql": [
                "`ts`.`table_catalog` = `ci`.`table_catalog` AND `ts`.`table_schema` = `ci`.`table_schema` "
                "AND `ts`.`table_name` = `ci`.`table_name`",
                "--rt=FROM_RELATIONSHIP_TYPE_ONE_TO_MANY--",
            ],
            "comment": ["Join table_summary to column_inventory on the three-part table identifier (catalog, schema, name)."],
        },
        {
            "left": {"identifier": f"{loc}.column_inventory", "alias": "ci"},
            "right": {"identifier": f"{loc}.fk_edges", "alias": "fk"},
            "sql": [
                "`ci`.`table_catalog` = `fk`.`fk_catalog` AND `ci`.`table_schema` = `fk`.`fk_schema` "
                "AND `ci`.`table_name` = `fk`.`fk_table` AND `ci`.`column_name` = `fk`.`fk_column`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "comment": ["Join column_inventory to fk_edges matching on the FK-side table and column identifiers."],
        },
        {
            "left": {"identifier": f"{loc}.table_summary", "alias": "ts"},
            "right": {"identifier": f"{loc}.fk_edges", "alias": "fk"},
            "sql": [
                "`ts`.`table_catalog` = `fk`.`fk_catalog` AND `ts`.`table_schema` = `fk`.`fk_schema` "
                "AND `ts`.`table_name` = `fk`.`fk_table`",
                "--rt=FROM_RELATIONSHIP_TYPE_ONE_TO_MANY--",
            ],
            "comment": ["Join table_summary to fk_edges matching on the FK-side table identifier (fk_catalog, fk_schema, fk_table)."],
        },
    ]


def sql_snippets_for_views(catalogs: list) -> dict:
    """Reusable filter/expression/measure snippets. The catalog-only filter is only
    emitted when there's exactly one configured catalog to name -- with zero (unscoped)
    or multiple catalogs there's no single literal worth hardcoding a snippet for."""
    filters = []
    if len(catalogs) == 1:
        catalog = catalogs[0]
        filters.append({
            "sql": f"table_catalog = '{catalog}'",
            "display_name": f"{catalog} Catalog Only",
            "synonyms": [f"{catalog} only", f"limit to {catalog}", f"{catalog} catalog"],
        })
    filters += [
        {
            "sql": "is_foreign_key = TRUE",
            "display_name": "Foreign Key Columns Only",
            "synonyms": ["foreign keys only", "FK columns", "only foreign key columns"],
        },
        {
            "sql": "is_primary_key = TRUE",
            "display_name": "Primary Key Columns Only",
            "synonyms": ["primary keys only", "PK columns", "only primary key columns"],
        },
        {
            "sql": "outgoing_foreign_key_count = 0",
            "display_name": "Tables Without Foreign Keys",
            "synonyms": ["no foreign keys", "isolated tables", "tables without FK", "standalone tables"],
        },
        {
            "sql": "outgoing_foreign_key_count > 0",
            "display_name": "Tables With Foreign Keys",
            "synonyms": ["has foreign keys", "tables with FK", "tables with relationships"],
        },
    ]
    expressions = [
        {
            "alias": "full_table_identifier",
            "sql": "CONCAT(table_catalog, '.', table_schema, '.', table_name)",
            "display_name": "Full Table Identifier",
        },
        {
            "alias": "fk_full_table_identifier",
            "sql": "CONCAT(fk_catalog, '.', fk_schema, '.', fk_table)",
            "display_name": "FK Full Table Identifier",
        },
        {
            "alias": "pk_full_table_identifier",
            "sql": "CONCAT(pk_catalog, '.', pk_schema, '.', pk_table)",
            "display_name": "PK Full Table Identifier",
        },
    ]
    measures = [
        {"alias": "total_tables", "sql": "COUNT(DISTINCT table_name)", "display_name": "Total Tables"},
        {"alias": "total_columns", "sql": "COUNT(DISTINCT column_name)", "display_name": "Total Columns"},
        {"alias": "total_fk_relationships", "sql": "COUNT(*)", "display_name": "Total FK Relationships"},
    ]
    return {"filters": filters, "expressions": expressions, "measures": measures}

# Stable marker embedded in the description, used to find "our" space across re-runs
# regardless of title (title includes the catalog list, which can change).
MANAGED_MARKER = "[erd-explorer-managed]"

INSTRUCTIONS_TEMPLATE = """\
You are the schema/ERD assistant for the interactive ERD (entity-relationship diagram)
viewer app. Your ONLY data sources are 3 views, each already hard-scoped to these
catalogs: {catalog_list}. You have no access to anything else -- not system.information_schema,
not any catalog's own information_schema, not any business/data tables.

- table_summary: one row per table (table_catalog, table_schema, table_name, table_type,
  column_count, primary_key_column_count, outgoing_foreign_key_count,
  incoming_foreign_key_count). Use this for "what tables exist", "which tables have no
  foreign keys" (outgoing_foreign_key_count = 0), or general table-level questions.
- column_inventory: one row per column (table_catalog, table_schema, table_name,
  column_name, full_data_type, ordinal_position, is_primary_key, is_foreign_key). Use
  this for "what columns does X have" or "what is the primary key of X".
- fk_edges: one row per foreign-key relationship (fk_catalog, fk_schema, fk_table,
  fk_column -> pk_catalog, pk_schema, pk_table, pk_column, constraint_name). Use this for
  "what references X", "what does X join to", or "what are the join paths between
  tables". The direction is always fk_* -> pk_* (the fk_* table holds the foreign key,
  the pk_* table is what it points to).

Rules:
- Only answer using these 3 views. Do not attempt to query anything else, and do not
  claim knowledge of any catalog other than {catalog_list} -- you have no visibility
  into them at all.
- Prefer fk_edges over guessing relationships from table/column names alone. If asked
  about a relationship with no matching row in fk_edges, say there is no *declared*
  foreign key for it -- note that a real relationship may still exist without being
  declared as a formal constraint (Unity Catalog PK/FK constraints are optional and
  frequently absent even when the relationship is real).
- These views contain zero business data or row-level values -- only schema/structure
  metadata (table/column/constraint names and counts). If asked about actual data, say
  that's out of scope for this assistant.
- If asked about a catalog not in {catalog_list}, say it isn't included in this space.
"""


def example_sql_for_views(catalogs: list, loc: str) -> list:
    """Catalog-agnostic example (question, sql, usage_guidance) triples against the 3
    curated views -- teaches the query pattern without assuming any particular
    customer's table names. 10-15 examples is the sweet spot for Genie accuracy."""
    examples = []
    if catalogs:
        example_catalog = catalogs[0]
        examples.append((
            f"Show all tables in the {example_catalog} catalog.",
            f"SELECT table_schema, table_name, table_type FROM {loc}.table_summary "
            f"WHERE table_catalog = '{example_catalog}' ORDER BY table_schema, table_name;",
            "Enumerate all tables in a catalog, grouped by schema. Helpful for browsing the overall structure of the data model.",
        ))
    else:
        examples.append((
            "Show all tables and which catalog they're in.",
            f"SELECT table_catalog, table_schema, table_name, table_type FROM {loc}.table_summary "
            f"ORDER BY table_catalog, table_schema, table_name;",
            "Enumerate every table this deployment can see, grouped by catalog and schema.",
        ))
    examples += [
        (
            "Which tables have no foreign key relationships?",
            f"SELECT table_catalog, table_schema, table_name FROM {loc}.table_summary "
            f"WHERE outgoing_foreign_key_count = 0 ORDER BY table_catalog, table_schema, table_name;",
            "Identify isolated tables with no outgoing foreign key constraints -- useful for finding leaf or standalone entities in the ERD.",
        ),
        (
            "Which tables have incoming foreign keys (are referenced by other tables)?",
            f"SELECT table_catalog, table_schema, table_name, incoming_foreign_key_count FROM {loc}.table_summary "
            f"WHERE incoming_foreign_key_count > 0 ORDER BY incoming_foreign_key_count DESC;",
            "Find the most-referenced ('parent') tables in the schema.",
        ),
        (
            "Show the columns and data types for a given schema.table, including which are primary/foreign keys.",
            f"SELECT column_name, full_data_type, is_primary_key, is_foreign_key FROM {loc}.column_inventory "
            f"WHERE table_schema = 'SCHEMA_NAME' AND table_name = 'TABLE_NAME' ORDER BY ordinal_position;",
            "Inspect the columns, data types, and key roles for a specific table. Replace SCHEMA_NAME and TABLE_NAME with the target table's schema and name.",
        ),
        (
            "Show all foreign key columns for a given table.",
            f"SELECT column_name, full_data_type, ordinal_position FROM {loc}.column_inventory "
            f"WHERE table_schema = 'SCHEMA_NAME' AND table_name = 'TABLE_NAME' AND is_foreign_key = TRUE ORDER BY ordinal_position;",
            "List just the FK columns of a specific table, in declaration order.",
        ),
        (
            "List all primary key columns across all tables.",
            f"SELECT table_catalog, table_schema, table_name, column_name FROM {loc}.column_inventory "
            f"WHERE is_primary_key = TRUE ORDER BY table_catalog, table_schema, table_name, column_name;",
            "Get a full inventory of every declared primary key column.",
        ),
        (
            "What foreign keys reference a given table (who points at it)?",
            f"SELECT fk_catalog, fk_schema, fk_table, fk_column FROM {loc}.fk_edges "
            f"WHERE pk_schema = 'SCHEMA_NAME' AND pk_table = 'TABLE_NAME';",
            "Find all tables that hold a foreign key pointing at a given table (its dependents). Replace SCHEMA_NAME and TABLE_NAME with the target parent table.",
        ),
        (
            "What foreign keys does a given table declare (what does it point to)?",
            f"SELECT fk_column, pk_catalog, pk_schema, pk_table, pk_column, constraint_name FROM {loc}.fk_edges "
            f"WHERE fk_schema = 'SCHEMA_NAME' AND fk_table = 'TABLE_NAME';",
            "Find every table a given table declares a foreign key toward (its dependencies). Replace SCHEMA_NAME and TABLE_NAME with the target table.",
        ),
        (
            "Which tables does a given table join to (both directions)?",
            f"SELECT * FROM {loc}.fk_edges "
            f"WHERE (fk_schema = 'SCHEMA_NAME' AND fk_table = 'TABLE_NAME') "
            f"   OR (pk_schema = 'SCHEMA_NAME' AND pk_table = 'TABLE_NAME');",
            "Get every FK relationship a table participates in, whether it's the FK side or the PK side. Replace SCHEMA_NAME and TABLE_NAME with the target table.",
        ),
        (
            "Find all tables that have both primary keys and outgoing foreign keys.",
            f"SELECT table_catalog, table_schema, table_name, primary_key_column_count, outgoing_foreign_key_count FROM {loc}.table_summary "
            f"WHERE primary_key_column_count > 0 AND outgoing_foreign_key_count > 0 ORDER BY table_schema, table_name;",
            "Find well-formed entity tables that both declare a primary key and reference other tables.",
        ),
        (
            "Which tables have the most columns?",
            f"SELECT table_catalog, table_schema, table_name, column_count FROM {loc}.table_summary "
            f"ORDER BY column_count DESC LIMIT 20;",
            "Surface the widest tables in the schema -- often the most complex entities.",
        ),
        (
            "List all parent-child (foreign-key) relationships across the approved catalogs.",
            f"SELECT fk_catalog, fk_schema, fk_table, fk_column, pk_catalog, pk_schema, pk_table, pk_column "
            f"FROM {loc}.fk_edges ORDER BY pk_catalog, pk_schema, pk_table;",
            "Get the full ERD edge list -- every declared foreign-key relationship, ordered by parent table.",
        ),
    ]
    return examples


def benchmarks_for_views(catalogs: list, loc: str) -> list:
    """(question, expected_sql) pairs for Genie's automated benchmarks feature.
    response_format="SQL" (not the builder's default "TEXT") is required here -- an
    earlier version of this script avoided benchmarks because "TEXT" answers were
    rejected by the API; "SQL" answers work fine."""
    benchmarks = [
        (
            "Which tables have no foreign keys?",
            f"SELECT table_catalog, table_schema, table_name FROM {loc}.table_summary "
            f"WHERE outgoing_foreign_key_count = 0 ORDER BY table_catalog, table_schema, table_name;",
        ),
        (
            "What are the primary key columns for a given table?",
            f"SELECT column_name, full_data_type, ordinal_position FROM {loc}.column_inventory "
            f"WHERE table_schema = 'SCHEMA_NAME' AND table_name = 'TABLE_NAME' AND is_primary_key = TRUE ORDER BY ordinal_position;",
        ),
        (
            "Which tables have both incoming and outgoing foreign keys (junction or bridge tables)?",
            f"SELECT table_catalog, table_schema, table_name, incoming_foreign_key_count, outgoing_foreign_key_count FROM {loc}.table_summary "
            f"WHERE incoming_foreign_key_count > 0 AND outgoing_foreign_key_count > 0 ORDER BY table_schema, table_name;",
        ),
        (
            "Which tables have the most incoming foreign key references (most referenced tables)?",
            f"SELECT table_catalog, table_schema, table_name, incoming_foreign_key_count FROM {loc}.table_summary "
            f"ORDER BY incoming_foreign_key_count DESC LIMIT 20;",
        ),
        (
            "How many tables exist in each schema?",
            f"SELECT table_catalog, table_schema, COUNT(*) AS table_count FROM {loc}.table_summary "
            f"GROUP BY table_catalog, table_schema ORDER BY table_catalog, table_schema;",
        ),
    ]
    if catalogs:
        example_catalog = catalogs[0]
        benchmarks.insert(0, (
            f"How many tables are in the {example_catalog} catalog?",
            f"SELECT COUNT(*) FROM {loc}.table_summary WHERE table_catalog = '{example_catalog}';",
        ))
    return benchmarks


def find_managed_space_id(w) -> str | None:
    """Find an existing space we manage, via its stable description marker."""
    page_token = None
    while True:
        query = {"page_size": 100}
        if page_token:
            query["page_token"] = page_token
        resp = w.api_client.do(method="GET", path="/api/2.0/genie/spaces", query=query)
        for space in resp.get("spaces", []):
            if MANAGED_MARKER in (space.get("description") or ""):
                return space["space_id"]
        page_token = resp.get("next_page_token")
        if not page_token:
            return None


def resolve_catalogs(arg_value: str) -> list:
    """[] means unscoped mode (deliberate: no --catalogs / ERD_CATALOGS given) -- per
    user decision, an unscoped ERD_CATALOGS means an unscoped Genie Space too, not a
    silent fallback to a demo catalog."""
    if arg_value:
        return [c.strip() for c in arg_value.split(",") if c.strip()]
    env_value = os.environ.get("ERD_CATALOGS")
    if env_value:
        return [c.strip() for c in env_value.split(",") if c.strip()]
    return []


def resolve_metadata_location(arg_value: str, catalogs: list) -> str:
    raw = arg_value or os.environ.get("ERD_METADATA_LOCATION", "")
    if raw and "." in raw:
        return raw
    if catalogs:
        return f"{catalogs[0]}.erd_meta"
    raise SystemExit(
        "--metadata-location (or ERD_METADATA_LOCATION) is required when --catalogs is "
        "empty (unscoped mode) -- there's no catalog to default the metadata views into."
    )


def build_serialized_space(catalogs: list, loc: str, warehouse_id: str) -> GenieSpaceBuilder:
    catalog_list = ", ".join(catalogs) if catalogs else "ALL catalogs visible to this deployment"
    builder = GenieSpaceBuilder(
        title=f"ERD Schema Assistant ({catalog_list})",
        description=(
            f"{MANAGED_MARKER} Answers structural/ERD questions about {catalog_list} "
            f"using 3 narrow, pre-scoped views in {loc} (table_summary, column_inventory, "
            "fk_edges) -- not system.information_schema directly. The view definitions "
            "are this space's actual access boundary."
        ),
        warehouse_id=warehouse_id,
    )
    for view in CURATED_VIEWS:
        builder.add_table(f"{loc}.{view}", column_configs=column_configs_for(view))
    builder.set_instructions(INSTRUCTIONS_TEMPLATE.format(catalog_list=catalog_list))

    # Every ID-keyed collection in serialized_space must be sorted ascending by id --
    # a real, undocumented API requirement (InvalidParameterValue: "must be sorted by
    # id") discovered by actually calling the API, not from the builder's own
    # id-uniqueness validation, which doesn't check ordering. Generate+sort ids up
    # front per collection, independently, rather than relying on insertion order.
    def _sorted_ids(n: int) -> list[str]:
        return sorted(uuid4().hex for _ in range(n))

    # Join specs teach Genie how the 3 curated views relate to each other, so it can
    # answer questions that span more than one of them without guessing a join.
    join_specs = join_specs_for_views(loc)
    for item_id, join_spec in zip(_sorted_ids(len(join_specs)), join_specs):
        builder.add_join_spec(join_spec, item_id=item_id)

    # Reusable filter/expression/measure snippets -- Genie surfaces these as building
    # blocks when composing SQL, which measurably improves answer quality/consistency.
    snippets = sql_snippets_for_views(catalogs)
    for item_id, f in zip(_sorted_ids(len(snippets["filters"])), snippets["filters"]):
        builder.add_filter(f.pop("sql"), display_name=f.pop("display_name", ""), item_id=item_id, **f)
    for item_id, e in zip(_sorted_ids(len(snippets["expressions"])), snippets["expressions"]):
        builder.add_expression(e.pop("alias"), e.pop("sql"), display_name=e.pop("display_name", ""), item_id=item_id, **e)
    for item_id, m in zip(_sorted_ids(len(snippets["measures"])), snippets["measures"]):
        builder.add_measure(m.pop("alias"), m.pop("sql"), display_name=m.pop("display_name", ""), item_id=item_id, **m)

    examples = example_sql_for_views(catalogs, loc)
    for item_id, (question, sql, guidance) in zip(_sorted_ids(len(examples)), examples):
        builder.add_example_sql(question, sql, description=guidance, item_id=item_id)

    benchmarks = benchmarks_for_views(catalogs, loc)
    for item_id, (question, sql) in zip(_sorted_ids(len(benchmarks)), benchmarks):
        builder.add_benchmark(question, sql, response_format="SQL", item_id=item_id)

    builder.validate()
    return builder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute).")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalogs", default="", help="Comma-separated. Defaults to ERD_CATALOGS env var, then unscoped (all catalogs visible to this deployment).")
    parser.add_argument("--metadata-location", default="", help='"catalog.schema" where the scoped views live (see create_scoped_views.py). Defaults to ERD_METADATA_LOCATION env var, then "<first catalog>.erd_meta".')
    parser.add_argument(
        "--grant-to-app",
        default="",
        help="Databricks App name (e.g. erd-explorer-dev). If set, look up that app's "
        "service principal and grant it CAN_RUN on the Genie Space -- the app resource "
        "must already exist (i.e. run this after `databricks bundle deploy`, before/after "
        "`databricks bundle run <app>` is fine either way).",
    )
    args = parser.parse_args()

    catalogs = resolve_catalogs(args.catalogs)
    loc = resolve_metadata_location(args.metadata_location, catalogs)
    print(f"Scoping Genie Space to catalogs: {catalogs or 'ALL (unscoped)'}")
    print(f"Reading curated views from: {loc}")

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    builder = build_serialized_space(catalogs, loc, args.warehouse_id)

    existing_space_id = find_managed_space_id(w)

    if existing_space_id:
        print(f"Updating existing Genie Space {existing_space_id}...")
        body = {
            "title": builder.title,
            "description": builder.description,
            "warehouse_id": builder.warehouse_id,
            "serialized_space": builder.to_json(),
        }
        w.api_client.do(method="PATCH", path=f"/api/2.0/genie/spaces/{existing_space_id}", body=body)
        space_id = existing_space_id
    else:
        me = w.current_user.me().user_name
        parent_path = f"/Workspace/Users/{me}/erd-explorer-genie"
        w.workspace.mkdirs(parent_path)
        print(f"Creating new Genie Space under {parent_path}...")
        body = {
            "title": builder.title,
            "description": builder.description,
            "parent_path": parent_path,
            "warehouse_id": builder.warehouse_id,
            "serialized_space": builder.to_json(),
        }
        resp = w.api_client.do(method="POST", path="/api/2.0/genie/spaces", body=body)
        space_id = resp.get("space_id") or resp.get("id")
        if not space_id:
            raise RuntimeError(f"Could not find space_id in response: {json.dumps(resp)}")

    print(f"Genie Space ready: space_id={space_id}")

    if args.grant_to_app:
        # Genie Spaces are workspace objects with their own ACL, separate from the UC
        # grants on the scoped views -- the app's SP needs both. Creating a space places
        # it under the CREATOR's personal folder by default, so the app's own SP has no
        # access to it until explicitly granted here.
        app = w.apps.get(name=args.grant_to_app)
        sp_client_id = app.service_principal_client_id
        print(f"Granting CAN_RUN on the Genie Space to {args.grant_to_app}'s service principal ({sp_client_id})...")
        w.api_client.do(
            method="PATCH",
            path=f"/api/2.0/permissions/genie/{space_id}",
            body={"access_control_list": [{"service_principal_name": sp_client_id, "permission_level": "CAN_RUN"}]},
        )
        print("Granted.")


if __name__ == "__main__":
    main()
