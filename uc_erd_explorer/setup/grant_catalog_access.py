"""Grant the ERD Explorer app's service principal Unity Catalog access to the data it
needs: catalog-level grants (cascading to every schema/table inside, matching
ERD_CATALOGS being catalog-level scoping) for each configured data catalog, PLUS
per-schema grants enumerated from that catalog, plus a schema-specific grant for
wherever the Genie metadata views live.

The per-schema grants are not redundant busywork -- catalog-level grants need
catalog-level MANAGE privilege, which someone who can only create schemas within an
existing catalog (not manage the catalog itself) won't have. They'd own the schemas they
created, though, so schema-level grants on those still succeed even when the
catalog-level ones fail.

Requires the CALLER (whoever runs this) to already have grant-issuing rights -- if they
don't, each failing statement prints a clear permission error and the exact SQL for a
catalog/schema admin to run instead, rather than the whole run aborting partway through
or failing silently.

Shared by both deploy routes -- the CLI entry point below (which looks up the app's
service principal itself via the Apps API, mirroring create_genie_space.py's
--grant-to-app) and notebooks/install.py (which already has the service principal id
from creating/fetching the app) -- so grant logic can't drift between the two.

Usage:
  uv run --with databricks-sdk python setup/grant_catalog_access.py \
      --warehouse-id <your-warehouse-id> --profile <your-profile> \
      --app-name erd-explorer-dev --catalogs megacorp --metadata-location megacorp.erd_meta
"""
import argparse

from databricks.sdk import WorkspaceClient


def _run_grant(w: WorkspaceClient, warehouse_id: str, statement: str) -> None:
    print(f"{statement};  ...", end=" ", flush=True)
    try:
        resp = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=statement, wait_timeout="30s")
        if resp.status.state.value != "SUCCEEDED":
            raise RuntimeError(resp.status.error)
        print("ok")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED ({e}) -- ask a catalog admin to run this statement instead.")


def _list_schemas(w: WorkspaceClient, warehouse_id: str, catalog: str) -> list:
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"SELECT schema_name FROM system.information_schema.schemata WHERE catalog_name = '{catalog}' AND schema_name != 'information_schema'",
        wait_timeout="30s",
    )
    if resp.status.state.value != "SUCCEEDED":
        print(f"Could not enumerate schemas in {catalog} ({resp.status.error}) -- skipping schema-level grants for it.")
        return []
    return [row[0] for row in (resp.result.data_array or [])]


def grant_catalog_access(
    w: WorkspaceClient,
    warehouse_id: str,
    catalogs: list,
    metadata_catalog: str,
    metadata_schema: str,
    sp_client_id: str,
) -> None:
    """catalogs=[] (unscoped mode) skips the catalog-level data grants -- there's no
    fixed catalog list to grant on there, so the graph relies on whatever grants its
    service principal already has, same as any other unscoped deployment. The
    metadata-location grant is NOT skipped in that case, though: Genie's scoped views
    live at a specific (metadata_catalog, metadata_schema) regardless of whether the
    main graph is scoped or unscoped, and always need their own grant."""
    if catalogs:
        for cat in catalogs:
            _run_grant(w, warehouse_id, f"GRANT USE CATALOG ON CATALOG {cat} TO `{sp_client_id}`")
            _run_grant(w, warehouse_id, f"GRANT USE SCHEMA ON CATALOG {cat} TO `{sp_client_id}`")
            _run_grant(w, warehouse_id, f"GRANT SELECT ON CATALOG {cat} TO `{sp_client_id}`")
            # Fallback/supplement, not redundant busywork: the 3 catalog-level grants
            # above need catalog-level MANAGE privilege. Someone who only has rights to
            # create schemas within an existing catalog (not manage the catalog itself --
            # exactly the permission-constrained case this whole feature exists for) would
            # own the schemas they created but NOT have catalog-level grant rights, so
            # those statements fail while these per-schema ones (on schemas they own)
            # still succeed.
            for schema in _list_schemas(w, warehouse_id, cat):
                _run_grant(w, warehouse_id, f"GRANT USE SCHEMA ON SCHEMA {cat}.{schema} TO `{sp_client_id}`")
                _run_grant(w, warehouse_id, f"GRANT SELECT ON SCHEMA {cat}.{schema} TO `{sp_client_id}`")
    else:
        print("No catalogs given (unscoped mode) -- skipping catalog-level data grants;")
        print("the graph relies on the service principal's existing grants.")

    if metadata_catalog not in catalogs:
        _run_grant(w, warehouse_id, f"GRANT USE CATALOG ON CATALOG {metadata_catalog} TO `{sp_client_id}`")
    _run_grant(w, warehouse_id, f"GRANT USE SCHEMA ON SCHEMA {metadata_catalog}.{metadata_schema} TO `{sp_client_id}`")
    _run_grant(w, warehouse_id, f"GRANT SELECT ON SCHEMA {metadata_catalog}.{metadata_schema} TO `{sp_client_id}`")
    print(f"\nGrants attempted for service_principal_client_id = {sp_client_id}.")
    print("If any FAILED above, have a catalog admin run that exact statement, then reload the app.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None, help="Omit to use ambient auth (job/app compute) or your CLI's DEFAULT profile.")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--app-name", required=True, help="Databricks App name (e.g. erd-explorer-dev) -- its service principal is looked up via the Apps API.")
    parser.add_argument("--catalogs", default="", help="Comma-separated. Blank means unscoped mode (skips granting -- see grant_catalog_access()).")
    parser.add_argument("--metadata-location", required=True, help='"catalog.schema" where the scoped ERD metadata views live.')
    args = parser.parse_args()

    if "." not in args.metadata_location:
        raise SystemExit('--metadata-location must be "catalog.schema"')
    metadata_catalog, metadata_schema = args.metadata_location.split(".", 1)
    catalogs = [c.strip() for c in args.catalogs.split(",") if c.strip()]

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    app = w.apps.get(name=args.app_name)
    sp_client_id = app.service_principal_client_id

    grant_catalog_access(w, args.warehouse_id, catalogs, metadata_catalog, metadata_schema, sp_client_id)


if __name__ == "__main__":
    main()
