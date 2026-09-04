"""Read-only insight endpoints: the deterministic schema-health audit and dbxmetagen
detection. Both reuse the graph router's per-request OBO identity capture (via _capture_user)
so they run as whichever identity the deployment queries as."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..audit import audit_graph
from ..config import get_catalogs
from ..graph import build_graph, _resolve_catalogs
from ..integrations import detect_dbxmetagen, fetch_fk_predictions
from ..ratelimit import graph_rate_limit
from .graph import _ENV_QUERY, _capture_user

router = APIRouter(tags=["insights"], dependencies=[Depends(_capture_user)])


def _parse_pairs(pairs: Optional[str]):
    """Parse the same comma-separated catalog.schema selection /api/graph accepts, so the
    audit runs over exactly the scope the user is looking at."""
    if not pairs:
        return None
    parsed = []
    for pair in pairs.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "." not in pair:
            raise HTTPException(status_code=400, detail=f"Invalid catalog.schema pair: '{pair}'")
        catalog, schema = pair.split(".", 1)
        parsed.append((catalog.strip(), schema.strip()))
    return parsed


@router.get("/audit", dependencies=[Depends(graph_rate_limit)])
async def get_audit(
    pairs: Optional[str] = Query(default=None, description="Same catalog.schema selection as /api/graph."),
    env: str = _ENV_QUERY,
):
    """Deterministic schema-health audit over the current graph scope. Builds (or reuses the
    cached) graph, then runs pure rule checks on it -- no extra queries, no LLM, no writes."""
    parsed = _parse_pairs(pairs)
    try:
        # build_graph is cached and does the warehouse I/O; run it off the event loop (carrying
        # the OBO identity via the copied context, same as /api/graph). audit_graph is pure.
        payload = await asyncio.to_thread(build_graph, parsed, env)
        return audit_graph(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/integrations/dbxmetagen")
async def get_dbxmetagen(env: str = _ENV_QUERY):
    """Whether dbxmetagen's output is present for the in-scope catalogs. Best-effort and
    read-only -- returns {present, location, tables_found, repo_url}; the frontend shows the
    richer-metadata status when present, or a recommendation to deploy dbxmetagen when not."""
    catalogs = _resolve_catalogs(get_catalogs(), env)
    # detect_dbxmetagen never raises (best-effort); still offloaded since it hits the warehouse.
    return await asyncio.to_thread(detect_dbxmetagen, catalogs)


@router.get("/integrations/dbxmetagen/fk-predictions", dependencies=[Depends(graph_rate_limit)])
async def get_dbxmetagen_fk_predictions(env: str = _ENV_QUERY):
    """dbxmetagen's confidence-scored FK predictions as overlay edges (read-only, best-effort).
    Returns {present, location, edges}; empty edges if dbxmetagen or its fk_predictions table
    isn't there. The frontend filters edges to on-diagram tables and renders them as a
    distinct, toggleable layer."""
    catalogs = _resolve_catalogs(get_catalogs(), env)
    return await asyncio.to_thread(fetch_fk_predictions, catalogs)
