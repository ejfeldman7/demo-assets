"""Admin routes for the metadata-snapshot panel.

The ERD graph can read from a weekly-materialized snapshot (erd_snapshot_* tables) instead
of live system.information_schema -- see server/graph.py and setup/build_erd_snapshot.py.
These endpoints let an operator see the snapshot's freshness and trigger an on-demand
rebuild by running the refresh_erd_snapshot job (Jobs run-now), so a refresh is observable
(it returns a run id + page URL to watch) rather than fire-and-forget.

The job is triggered as the APP's own service principal (get_workspace_client), not the
logged-in OBO user -- a metadata refresh is a deployment action, not a per-user one. The
app SP therefore needs CAN_MANAGE_RUN on the job; if it doesn't, run-now returns a
permission error, which we surface as an actionable 403 rather than a generic 500.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query

from ..config import get_metadata_source, get_snapshot_job_id, get_workspace_client
from ..graph import get_snapshot_freshness

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/snapshot-status")
async def snapshot_status():
    """Current metadata-source mode + snapshot freshness, for the admin panel. Never
    errors on a missing snapshot -- freshness is simply null then."""
    freshness = await asyncio.to_thread(get_snapshot_freshness)
    return {
        # What ERD_METADATA_SOURCE is set to ("snapshot" or "information_schema").
        "source_mode": get_metadata_source(),
        # Whether a refresh job is wired up (id templated in by the DAB).
        "job_configured": get_snapshot_job_id() is not None,
        # {refreshed_at, catalogs} from erd_snapshot_meta, or null if never built.
        "snapshot": freshness,
    }


@router.post("/refresh-snapshot")
async def refresh_snapshot():
    """Trigger the refresh_erd_snapshot job via Jobs run-now. Returns the run id + page
    URL so the caller can poll /refresh-snapshot/status and link out to the run."""
    job_id = get_snapshot_job_id()
    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="No snapshot refresh job is configured (ERD_SNAPSHOT_JOB_ID unset). "
            "This deployment can't trigger a refresh from the app.",
        )
    client = get_workspace_client()
    try:
        run = await asyncio.to_thread(client.jobs.run_now, job_id=int(job_id))
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "PERMISSION" in msg.upper() or "not authorized" in msg.lower():
            raise HTTPException(
                status_code=403,
                detail="The app's service principal isn't allowed to run the refresh job. "
                "Grant it CAN_MANAGE_RUN on the refresh_erd_snapshot job (see README).",
            )
        raise HTTPException(status_code=500, detail=f"Failed to start refresh: {msg}")
    return {"run_id": run.run_id}


@router.get("/refresh-snapshot/status")
async def refresh_snapshot_status(run_id: int = Query(...)):
    """Poll a refresh run's state so the panel can show in-progress / succeeded / failed
    and link to the run page."""
    client = get_workspace_client()
    try:
        run = await asyncio.to_thread(client.jobs.get_run, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to read run status: {e}")
    state = run.state
    return {
        "run_id": run_id,
        "life_cycle_state": state.life_cycle_state.value if state and state.life_cycle_state else None,
        "result_state": state.result_state.value if state and state.result_state else None,
        "run_page_url": run.run_page_url,
    }
