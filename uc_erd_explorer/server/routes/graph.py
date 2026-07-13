"""API routes for the ERD graph and the catalog/schema picker."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import get_catalogs, get_test_catalog_suffix, get_workspace_name
from ..graph import build_graph, list_catalog_schemas

router = APIRouter(tags=["graph"])

_ENV_QUERY = Query(
    default="prod",
    pattern="^(prod|test)$",
    description="Which environment's catalogs to query -- 'prod' (default) uses the "
    "configured ERD_CATALOGS as-is, 'test' appends the configured test-catalog suffix "
    "(see /api/config's test_catalog_suffix) to each one. Only meaningful for a scoped "
    "deployment -- see /api/config's test_available.",
)


@router.get("/graph")
async def get_graph(
    pairs: Optional[str] = Query(
        default=None,
        description="Comma-separated catalog.schema pairs to narrow to (e.g. "
        "'megacorp.erp,megacorp.factory'), matching the frontend's catalog/schema tree "
        "picker. Omit to include everything in scope (the ERD_CATALOGS allow-list, or "
        "every visible catalog if ERD_CATALOGS is unset).",
    ),
    env: str = _ENV_QUERY,
):
    """Return {nodes, edges} for the deployment's configured catalog allow-list,
    optionally narrowed to specific catalog.schema pairs."""
    parsed = None
    if pairs:
        parsed = []
        for pair in pairs.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "." not in pair:
                raise HTTPException(status_code=400, detail=f"Invalid catalog.schema pair: '{pair}'")
            catalog, schema = pair.split(".", 1)
            parsed.append((catalog.strip(), schema.strip()))
    try:
        return build_graph(parsed, env)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema-tree")
async def get_schema_tree(env: str = _ENV_QUERY):
    """Enumerate catalog -> [schema, ...] for the frontend's catalog/schema tree picker,
    without fetching the full graph."""
    try:
        return {"catalogs": list_catalog_schemas(env), "unscoped": get_catalogs() is None}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_config():
    """Expose which catalogs this deployment is scoped to, and which workspace it's
    running in, for the frontend."""
    catalogs = get_catalogs()
    try:
        workspace = get_workspace_name()
    except Exception:  # noqa: BLE001
        workspace = None
    return {
        "catalogs": catalogs,
        "unscoped": catalogs is None,
        "workspace": workspace,
        # Prod/Test toggle only makes sense for a scoped deployment -- an unscoped one
        # has no defined catalog list to append the test suffix to.
        "test_available": catalogs is not None,
        "test_catalog_suffix": get_test_catalog_suffix(),
    }
