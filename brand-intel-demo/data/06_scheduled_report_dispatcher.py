# Databricks notebook source
# MAGIC %md
# MAGIC # Scheduled Report Dispatcher
# MAGIC
# MAGIC Runs on an hourly cron. Checks which report schedules are due based on their
# MAGIC `cron_expression` and executes each one via `report_runner.run_schedule()`.
# MAGIC
# MAGIC **Modes:**
# MAGIC - If `schedule_id` widget is provided, runs only that schedule (for manual testing).
# MAGIC - If empty, evaluates all active schedules and runs any that are due.

# COMMAND ----------

# MAGIC %pip install croniter psycopg2-binary fpdf2 markdown
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
import os
import logging
import base64
from datetime import datetime, timezone
from databricks.sdk import WorkspaceClient

# Load Lakebase credentials from secrets before importing db module
w = WorkspaceClient()
_SCOPE = "lakebase-scope"

def _secret(key):
    raw = w.secrets.get_secret(_SCOPE, key).value
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        return raw

os.environ["PGPASSWORD"] = _secret("pgpassword")
os.environ["PGUSER"] = _secret("pguser")
os.environ["LAKEBASE_HOST"] = _secret("lakebase-host")
os.environ["LAKEBASE_DB"] = _secret("lakebase-db")

# Add the app's src/ directory to path so we can reuse existing utility modules
notebook_path = os.path.dirname(dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get())
workspace_dir = "/Workspace" + notebook_path.rsplit("/", 1)[0] + "/app/src"
sys.path.insert(0, workspace_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scheduled_report_dispatcher")

# COMMAND ----------

dbutils.widgets.text("schedule_id", "", "Schedule ID (blank = run all due)")
schedule_id_param = dbutils.widgets.get("schedule_id").strip()

if schedule_id_param:
    logger.info("Single-schedule mode: %s", schedule_id_param)
else:
    logger.info("Dispatcher mode: will evaluate all active schedules")

# COMMAND ----------

# Initialize Lakebase connection
import db
from db import execute_query, execute_insert

if not db.LAKEBASE_AVAILABLE:
    dbutils.notebook.exit("Lakebase unavailable — cannot run dispatcher.")

# COMMAND ----------

def claim_due_schedules(run_id):
    """Atomically claim due schedules to prevent double-send.

    Uses UPDATE ... RETURNING with a claim pattern:
    - Only claims rows where claimed_by is NULL or claim is stale (>30 min)
    - Returns the claimed rows as a list of dicts
    """
    from croniter import croniter

    # First, find due schedules
    schedules_df = execute_query(
        """
        SELECT schedule_id, cron_expression, updated_at, created_at, report_type
        FROM bi_report_schedules
        WHERE is_active = TRUE
          AND (claimed_by IS NULL OR claimed_at < now() - INTERVAL '30 minutes')
        """
    )

    if schedules_df.empty:
        logger.info("No active/unclaimed schedules found.")
        return []

    now = datetime.now(timezone.utc)
    due = []

    for _, row in schedules_df.iterrows():
        sid = str(row["schedule_id"])
        cron_expr = row["cron_expression"]
        last_run = row["updated_at"] or row["created_at"]
        never_run = row["updated_at"] == row["created_at"]
        report_type = row.get("report_type", "qa")

        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)

        try:
            is_due = False
            if never_run:
                is_due = True
                logger.info("Schedule %s is DUE (first run)", sid)
            else:
                next_fire = croniter(cron_expr, last_run).get_next(datetime)
                if next_fire.tzinfo is None:
                    next_fire = next_fire.replace(tzinfo=timezone.utc)
                if next_fire <= now:
                    is_due = True
                    logger.info("Schedule %s is DUE (next_fire=%s)", sid, next_fire)
                else:
                    logger.info("Schedule %s not yet due (next_fire=%s)", sid, next_fire)

            if is_due:
                # Atomic claim — only succeeds if still unclaimed
                claimed = execute_insert(
                    """
                    UPDATE bi_report_schedules
                    SET claimed_by = %s, claimed_at = now()
                    WHERE schedule_id = %s
                      AND (claimed_by IS NULL OR claimed_at < now() - INTERVAL '30 minutes')
                    RETURNING schedule_id
                    """,
                    (run_id, sid),
                )
                if claimed:
                    due.append({"schedule_id": sid, "report_type": report_type})
                    logger.info("Claimed schedule %s (type=%s)", sid, report_type)
                else:
                    logger.info("Schedule %s already claimed by another dispatcher", sid)

        except Exception as e:
            logger.error("Bad cron expression for schedule %s ('%s'): %s", sid, cron_expr, e)

    return due

# COMMAND ----------

from report_runner import run_schedule, run_proactive_schedule
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid as uuid_mod

dispatcher_run_id = str(uuid_mod.uuid4())

def _release_claim(sid):
    """Release the claim on a schedule after completion."""
    execute_insert(
        "UPDATE bi_report_schedules SET claimed_by = NULL, claimed_at = NULL, updated_at = now() WHERE schedule_id = %s",
        (sid,),
    )

def _release_claim_failed(sid, error_msg):
    """Release claim on failure without updating updated_at."""
    execute_insert(
        "UPDATE bi_report_schedules SET claimed_by = NULL, claimed_at = NULL WHERE schedule_id = %s",
        (sid,),
    )

def _run_one(schedule_info):
    """Run a single schedule with routing based on report_type."""
    if isinstance(schedule_info, str):
        # Legacy: just a schedule_id string
        sid = schedule_info
        report_type = "qa"
    else:
        sid = schedule_info["schedule_id"]
        report_type = schedule_info.get("report_type", "qa")

    logger.info("Running schedule %s (type=%s) ...", sid, report_type)
    try:
        if report_type == "proactive":
            result = run_proactive_schedule(sid, workspace_client=w)
        else:
            result = run_schedule(sid, workspace_client=w)

        status = result.get("status", "unknown")
        logger.info("Schedule %s completed with status: %s", sid, status)

        if status == "success":
            _release_claim(sid)
        else:
            _release_claim_failed(sid, result.get("error", ""))

        return {"schedule_id": sid, "type": report_type, "status": status, "run_id": result.get("run_id")}
    except Exception as e:
        logger.error("Schedule %s failed: %s", sid, e)
        _release_claim_failed(sid, str(e))
        return {"schedule_id": sid, "type": report_type, "status": "error", "error": str(e)}

results = []

if schedule_id_param:
    # Single-schedule mode — look up report_type
    type_df = execute_query(
        "SELECT report_type FROM bi_report_schedules WHERE schedule_id = %s",
        (schedule_id_param,),
    )
    rtype = type_df.iloc[0]["report_type"] if not type_df.empty else "qa"
    schedule_infos = [{"schedule_id": schedule_id_param, "report_type": rtype}]
else:
    schedule_infos = claim_due_schedules(dispatcher_run_id)

logger.info("Schedules to run: %d", len(schedule_infos))

MAX_PARALLEL = 4

if len(schedule_infos) <= 1:
    results = [_run_one(s) for s in schedule_infos]
else:
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(schedule_infos))) as pool:
        futures = {pool.submit(_run_one, s): s for s in schedule_infos}
        for future in as_completed(futures):
            results.append(future.result())

# COMMAND ----------

# Print summary
print("=" * 60)
print("DISPATCH SUMMARY")
print("=" * 60)
print(f"Dispatcher run ID:   {dispatcher_run_id}")
print(f"Schedules claimed:   {len(schedule_infos)}")
print(f"Schedules executed:  {len(results)}")
for r in results:
    status_icon = "OK" if r["status"] == "success" else r["status"].upper()
    rtype = r.get("type", "qa")
    print(f"  [{status_icon}] {r['schedule_id']} ({rtype}) — {r.get('run_id', 'N/A')}")
if not results:
    print("  (no schedules were due)")
print("=" * 60)
