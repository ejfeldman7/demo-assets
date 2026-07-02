"""API route for the ERD graph."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import get_catalogs
from ..graph import build_graph

router = APIRouter(tags=["graph"])


@router.get("/graph")
async def get_graph(
    schemas: Optional[str] = Query(
        default=None,
        description="Comma-separated schema names to filter to (across whichever "
        "catalogs this deployment is scoped to via ERD_CATALOGS). Omit to include all "
        "schemas in scope.",
    )
):
    """Return {nodes, edges} for the deployment's configured catalog allow-list."""
    parsed = None
    if schemas:
        parsed = [s.strip() for s in schemas.split(",") if s.strip()]
    try:
        return build_graph(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_config():
    """Expose which catalogs this deployment is scoped to, for the frontend."""
    return {"catalogs": get_catalogs()}
