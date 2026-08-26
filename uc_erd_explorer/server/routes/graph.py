"""API routes for the ERD graph and the catalog/schema picker."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..config import (
    get_catalogs,
    get_test_catalog_suffix,
    get_workspace_name,
    reset_user_context,
    set_user_context,
)
from ..graph import build_graph, list_catalog_schemas


async def _capture_user(request: Request):
    """Capture the logged-in user's forwarded identity for the duration of the request,
    so get_query_client() can query information_schema as that user in on-behalf-of-user
    mode. Databricks Apps forward the user's token/identity in these headers; they are
    absent in service-principal mode and local dev, in which case this is a harmless
    no-op (the token contextvar stays None and the SP/profile path is used).

    A yield dependency runs its teardown after the response, resetting the contextvars so
    a token never leaks into a later request that reuses the same task."""
    token = request.headers.get("x-forwarded-access-token")
    email = request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-user")
    ctx = set_user_context(token, email)
    try:
        yield
    finally:
        reset_user_context(ctx)


router = APIRouter(tags=["graph"], dependencies=[Depends(_capture_user)])

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
        # build_graph does blocking, potentially slow warehouse I/O -- run it off the
        # event loop so one slow load doesn't stall every other concurrent request (this
        # handler is async). asyncio.to_thread copies the current context into the worker
        # thread, carrying the OBO user identity (_capture_user set it on this request's
        # context) through to get_query_client() -- see config.py / graph._submit_query.
        return await asyncio.to_thread(build_graph, parsed, env)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema-tree")
async def get_schema_tree(response: Response, env: str = _ENV_QUERY):
    """Enumerate catalog -> [schema, ...] for the frontend's catalog/schema tree picker,
    without fetching the full graph."""
    try:
        # Offloaded like /graph (blocking warehouse query, OBO context carried across).
        tree = await asyncio.to_thread(list_catalog_schemas, env)
        # `private` (never a shared/proxy cache): in on-behalf-of-user mode the tree is
        # privilege-filtered per user, so it must not be cached anywhere cross-user. Short
        # max-age still spares the browser a re-fetch on repeated env toggles in a session.
        response.headers["Cache-Control"] = "private, max-age=60"
        return {"catalogs": tree, "unscoped": get_catalogs() is None}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_config(response: Response):
    """Expose which catalogs this deployment is scoped to, and which workspace it's
    running in, for the frontend."""
    catalogs = get_catalogs()
    try:
        workspace = get_workspace_name()
    except Exception:  # noqa: BLE001
        workspace = None
    # Deployment-level, identical for every user -> safe to cache briefly in the browser.
    response.headers["Cache-Control"] = "private, max-age=300"
    return {
        "catalogs": catalogs,
        "unscoped": catalogs is None,
        "workspace": workspace,
        # Prod/Test toggle only makes sense for a scoped deployment -- an unscoped one
        # has no defined catalog list to append the test suffix to.
        "test_available": catalogs is not None,
        "test_catalog_suffix": get_test_catalog_suffix(),
    }
