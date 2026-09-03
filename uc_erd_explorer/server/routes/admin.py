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

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import (
    get_admin_emails,
    get_metadata_source,
    get_snapshot_job_id,
    get_user_email,
    get_workspace_client,
)
from ..graph import get_snapshot_freshness
from .graph import _capture_user


def _require_admin() -> None:
    """Gate state-changing admin actions (the refresh job spends compute). If
    ERD_ADMIN_EMAILS is set, the caller's forwarded email must be on it; if it's unset,
    admin stays open -- the internal-demo default where app access == trust. Set
    ERD_ADMIN_EMAILS on any shared/multi-role/customer deployment to lock this down.
    Runs after _capture_user (the router-level dependency), which populates the email."""
    allow = get_admin_emails()
    if not allow:
        return
    email = (get_user_email() or "").lower()
    if email not in allow:
        raise HTTPException(
            status_code=403,
            detail="You're not authorized to trigger a snapshot refresh.",
        )

# Same per-request identity capture the graph router uses: in on-behalf-of-user mode the
# snapshot-freshness read goes through get_query_client(), which needs the forwarded user
# token from this dependency -- without it, get_snapshot_freshness would always come back
# empty (token missing -> raise -> swallowed) and the panel would wrongly show "never built".
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(_capture_user)])


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


@router.post("/refresh-snapshot", dependencies=[Depends(_require_admin)])
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
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=500,
            detail=f"ERD_SNAPSHOT_JOB_ID is not a valid job id: {job_id!r} (deployment misconfig).",
        )
    client = get_workspace_client()

    def _existing_active_run():
        # Reuse an already-running refresh instead of stacking another: the button is a
        # shared, non-idempotent action (rapid clicks or two users would otherwise fire
        # multiple concurrent job runs). Return the first active run if one exists.
        return next(iter(client.jobs.list_runs(job_id=jid, active_only=True)), None)

    try:
        existing = await asyncio.to_thread(_existing_active_run)
        if existing is not None:
            return {"run_id": existing.run_id, "already_running": True}
        run = await asyncio.to_thread(client.jobs.run_now, job_id=jid)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "PERMISSION" in msg.upper() or "not authorized" in msg.lower():
            raise HTTPException(
                status_code=403,
                detail="The app's service principal isn't allowed to run the refresh job. "
                "Grant it CAN_MANAGE_RUN on the refresh_erd_snapshot job (see README).",
            )
        raise HTTPException(status_code=500, detail=f"Failed to start refresh: {msg}")
    return {"run_id": run.run_id, "already_running": False}


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
