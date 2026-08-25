"""
deploy_monitoring.py — deploy the Cost & Usage Suite Lakeview dashboard for Watchtower.

Standalone re-implementation of mohanab89/databricks-dashboard-suite's create_dashboards.py
(see NOTICE.md): creates the reference views + SQL functions the dashboard's datasets depend on
(via the SQL warehouse), then creates + publishes the 6-page Lakeview dashboard via the SDK.

Usage (values come from your config.env — source it first):
  set -a && . setup/config.env && set +a
  DATABRICKS_CONFIG_PROFILE=$DATABRICKS_PROFILE \
    WT_MON_CATALOG=${UC_SCHEMA%%.*} WT_MON_SCHEMA=${UC_SCHEMA##*.} \
    python3 monitoring/deploy_monitoring.py

Prints DASHBOARD_ID / DASHBOARD_URL / DASHBOARD_EMBED_URL for setup to capture into config.env.
"""

from __future__ import annotations

import os
import pathlib

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import Dashboard

# Where the reference views/functions + dashboard live. Required — no environment defaults.
CATALOG = os.environ["WT_MON_CATALOG"]
SCHEMA = os.environ.get("WT_MON_SCHEMA", "monitoring")
WAREHOUSE_ID = os.environ["WT_WAREHOUSE_ID"]
TEAM_TAGS = os.environ.get("WT_TEAM_TAGS", "team_name,group")
DASH_JSON = pathlib.Path(__file__).with_name("cost-usage-suite.lvdash.json")
DISPLAY_NAME = os.environ.get("WT_DASHBOARD_NAME", "Watchtower — Cost & Usage Suite")

w = WorkspaceClient()


def sql(stmt: str) -> None:
    resp = w.statement_execution.execute_statement(
        statement=stmt, warehouse_id=WAREHOUSE_ID, wait_timeout="50s")
    st = resp.status.state.value if resp.status and resp.status.state else "?"
    if st != "SUCCEEDED":
        err = resp.status.error.message if (resp.status and resp.status.error) else st
        raise RuntimeError(f"DDL failed ({st}): {err}\n{stmt[:200]}")


def _team_fn_sql() -> str:
    keys = [k.strip() for k in TEAM_TAGS.split(",") if k.strip()]
    parts = []
    for col in ("cluster_tags", "job_tags"):
        cs = "CASE\n"
        for k in keys:
            cs += f"WHEN map_contains_key({col}, '{k}') THEN lower({col}.`{k}`)\n"
        cs += (f"WHEN map_contains_key({col}, 'LakehouseMonitoring') AND "
               f"{col}.LakehouseMonitoring = 'true' THEN 'LakehouseMonitoring'\n")
        cs += f"ELSE NULL END AS {col}_team_name_init\n"
        parts.append(cs)
    inner = (f"SELECT ifnull(cluster_tags_team_name_init, job_tags_team_name_init) AS team_name_init "
             f"FROM (SELECT {', '.join(parts)})")
    q = f"(SELECT ifnull(team_name_init, 'unknown') AS team_name FROM ({inner}))"
    return (f"CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.team_name_from_tags"
            f"(cluster_tags MAP<STRING,STRING>, job_tags MAP<STRING,STRING>) RETURNS STRING RETURN {q}")


def create_functions_and_views() -> None:
    sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    sql(f"""CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.job_type_from_sku(sku STRING) RETURNS STRING RETURN
        CASE WHEN sku LIKE '%JOBS_SERVERLESS%' THEN 'JOBS_SERVERLESS'
             WHEN sku LIKE '%JOBS_COMPUTE_(PHOTON)%' THEN 'JOBS_COMPUTE_PHOTON'
             WHEN sku LIKE '%JOBS_COMPUTE%' THEN 'JOBS_COMPUTE'
             WHEN sku IS NULL THEN 'UNKNOWN' ELSE 'OTHER' END""")
    sql(f"""CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.sql_type_from_sku(sku STRING) RETURNS STRING RETURN
        CASE WHEN sku LIKE '%SERVERLESS_SQL%' THEN 'SQL_SERVERLESS'
             WHEN sku LIKE '%SQL_PRO%' THEN 'SQL_PRO'
             WHEN sku LIKE '%SQL%' THEN 'SQL_CLASSIC'
             WHEN sku IS NULL THEN 'UNKNOWN' ELSE 'OTHER' END""")
    sql(_team_fn_sql())
    sql(f"""CREATE OR REPLACE VIEW {CATALOG}.{SCHEMA}.workspace_reference AS
        SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name
        FROM system.access.workspaces_latest
        UNION SELECT DISTINCT CAST(workspace_id AS STRING), CAST(workspace_id AS STRING)
        FROM system.billing.usage
        WHERE CAST(workspace_id AS STRING) NOT IN (SELECT CAST(workspace_id AS STRING) FROM system.access.workspaces_latest)""")
    sql(f"""CREATE OR REPLACE VIEW {CATALOG}.{SCHEMA}.warehouse_reference AS
        SELECT workspace_id, warehouse_id, warehouse_name FROM (
          SELECT CAST(workspace_id AS STRING) AS workspace_id, CAST(warehouse_id AS STRING) AS warehouse_id,
                 warehouse_name, ROW_NUMBER() OVER (PARTITION BY workspace_id, warehouse_id ORDER BY change_time DESC) AS rn
          FROM system.compute.warehouses) WHERE rn = 1""")
    print(f"functions + views created in {CATALOG}.{SCHEMA}")


def _find_existing() -> str | None:
    """Return the id of an existing (non-trashed) dashboard named DISPLAY_NAME, else None —
    so re-running updates in place instead of failing with AlreadyExists."""
    try:
        for d in w.lakeview.list():
            if d.display_name == DISPLAY_NAME and getattr(d, "lifecycle_state", None) != "TRASHED":
                return d.dashboard_id
    except Exception:
        pass
    return None


def deploy_dashboard() -> str:
    host = w.config.host.rstrip("/")
    parent = f"/Workspace/Users/{w.current_user.me().user_name}/watchtower-monitoring"
    w.workspace.mkdirs(parent)
    data = DASH_JSON.read_text().replace("{catalog}", CATALOG).replace("{schema}", SCHEMA)
    did = _find_existing()
    if did is None:
        created = w.lakeview.create(dashboard=Dashboard(
            display_name=DISPLAY_NAME, parent_path=parent, serialized_dashboard=data, warehouse_id=WAREHOUSE_ID))
        did = created.dashboard_id
    # resolve in-dashboard page links now that the id is known, then update + (re)publish
    linked = data.replace("__PAGE__/", f"{host}/dashboardsv3/{did}/published/pages/")
    cur = w.lakeview.get(did)
    cur.serialized_dashboard = linked
    w.lakeview.update(dashboard_id=did, dashboard=cur)
    w.lakeview.publish(dashboard_id=did, warehouse_id=WAREHOUSE_ID)
    url = f"{host}/dashboardsv3/{did}/published"
    embed = f"{host}/embed/dashboardsv3/{did}"
    print(f"dashboard deployed + published: {url}")
    # These three lines are parsed by setup/setup.sh to populate config.env.
    print(f"DASHBOARD_ID={did}")
    print(f"DASHBOARD_URL={url}")
    print(f"DASHBOARD_EMBED_URL={embed}")
    return did


if __name__ == "__main__":
    create_functions_and_views()
    deploy_dashboard()
