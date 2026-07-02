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
    """Catalog-agnostic example Q+SQL pairs against the 3 curated views -- teaches the
    query pattern without assuming any particular customer's table names."""
    example_catalog = catalogs[0]
    return [
        (
            f"Show all tables in the {example_catalog} catalog.",
            f"SELECT table_schema, table_name, table_type FROM {loc}.table_summary "
            f"WHERE table_catalog = '{example_catalog}' ORDER BY table_schema, table_name;",
        ),
        (
            "Which tables have no foreign key relationships?",
            f"SELECT table_catalog, table_schema, table_name FROM {loc}.table_summary "
            f"WHERE outgoing_foreign_key_count = 0 ORDER BY table_catalog, table_schema, table_name;",
        ),
        (
            "Show the columns and data types for a given schema.table, including which are primary/foreign keys.",
            f"SELECT column_name, full_data_type, is_primary_key, is_foreign_key FROM {loc}.column_inventory "
            f"WHERE table_schema = 'SCHEMA_NAME' AND table_name = 'TABLE_NAME' ORDER BY ordinal_position;",
        ),
        (
            "What foreign keys reference a given table (who points at it)?",
            f"SELECT fk_catalog, fk_schema, fk_table, fk_column FROM {loc}.fk_edges "
            f"WHERE pk_schema = 'SCHEMA_NAME' AND pk_table = 'TABLE_NAME';",
        ),
        (
            "Which tables does a given table join to (both directions)?",
            f"SELECT * FROM {loc}.fk_edges "
            f"WHERE (fk_schema = 'SCHEMA_NAME' AND fk_table = 'TABLE_NAME') "
            f"   OR (pk_schema = 'SCHEMA_NAME' AND pk_table = 'TABLE_NAME');",
        ),
        (
            "List all parent-child (foreign-key) relationships across the approved catalogs.",
            f"SELECT fk_catalog, fk_schema, fk_table, fk_column, pk_catalog, pk_schema, pk_table, pk_column "
            f"FROM {loc}.fk_edges ORDER BY pk_catalog, pk_schema, pk_table;",
        ),
    ]


def benchmarks_for_views(catalogs: list, loc: str) -> list:
    """(question, expected_answer_shape) pairs for the validation checklist / Genie
    benchmarks feature -- adjust expected content per-catalog after first deploy."""
    example_catalog = catalogs[0]
    return [
        (
            f"How many tables are in the {example_catalog} catalog?",
            "A single number matching the row count of table_summary filtered to that catalog.",
        ),
        (
            "Which tables have no foreign keys?",
            "A list of tables from table_summary where outgoing_foreign_key_count = 0.",
        ),
        (
            "What is a foreign key relationship you can find?",
            "A fk_table.fk_column -> pk_table.pk_column pair sourced from fk_edges, not invented.",
        ),
    ]


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
    if arg_value:
        return [c.strip() for c in arg_value.split(",") if c.strip()]
    env_value = os.environ.get("ERD_CATALOGS")
    if env_value:
        return [c.strip() for c in env_value.split(",") if c.strip()]
    return ["megacorp"]


def resolve_metadata_location(arg_value: str, catalogs: list) -> str:
    raw = arg_value or os.environ.get("ERD_METADATA_LOCATION", "")
    if raw and "." in raw:
        return raw
    return f"{catalogs[0]}.erd_meta"


def build_serialized_space(catalogs: list, loc: str, warehouse_id: str) -> GenieSpaceBuilder:
    catalog_list = ", ".join(catalogs)
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
        builder.add_table(f"{loc}.{view}")
    builder.set_instructions(INSTRUCTIONS_TEMPLATE.format(catalog_list=catalog_list))

    examples = example_sql_for_views(catalogs, loc)
    sorted_ids = sorted(uuid4().hex for _ in examples)
    for item_id, (question, sql) in zip(sorted_ids, examples):
        builder.add_example_sql(question, sql, item_id=item_id)

    # NOTE: Genie's `benchmarks` feature has a server-side enum for answer format that
    # rejects the builder helper's own "TEXT" default (InvalidParameterValue) -- rather
    # than fight that, the benchmark questions live as manual validation steps in
    # TASKS.md / DEMO.md instead of as a formal Genie benchmark object.
    builder.validate()
    return builder


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute).")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalogs", default="", help="Comma-separated. Defaults to ERD_CATALOGS env var, then 'megacorp'.")
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
    print(f"Scoping Genie Space to catalogs: {catalogs}")
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
