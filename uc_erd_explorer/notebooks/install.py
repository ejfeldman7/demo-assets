# Databricks notebook source
# MAGIC %md
# MAGIC # Interactive UC ERD Viewer — Notebook Installer
# MAGIC
# MAGIC A no-CLI, no-Databricks-Asset-Bundle alternative to the `databricks.yml` deploy path
# MAGIC (see the repo README for that route). Use this if you don't have local CLI access,
# MAGIC or you'd rather configure and deploy entirely from inside the workspace.
# MAGIC
# MAGIC **How to use:** add this repo as a Databricks Git folder (Workspace ▸ Git Folders ▸
# MAGIC Add repo), open this notebook from inside that folder, fill in the widgets above
# MAGIC (click "Run all" once, the widgets will appear; set them, then "Run all" again), and
# MAGIC run all cells top to bottom. Safe to re-run — every step is idempotent, same as the
# MAGIC CLI/DAB path.
# MAGIC
# MAGIC This notebook does exactly what `databricks bundle deploy` + `databricks bundle run
# MAGIC setup_genie_space` + `databricks bundle run <app>` do together, just via the
# MAGIC Databricks SDK from notebook cells instead of the CLI -- it calls the SAME
# MAGIC `setup/create_scoped_views.py` and `setup/create_genie_space.py` functions, so the
# MAGIC two deploy routes can never drift apart in behavior.

# COMMAND ----------

# MAGIC %md ## 1. Configuration widgets
# MAGIC These take the place of the CLI route's `--var` bundle variables / env vars.

# COMMAND ----------

dbutils.widgets.text("repo_root", "", "Workspace path to this repo's root (required -- e.g. /Workspace/Users/you@company.com/erd-explorer). Right-click the folder in the workspace browser and choose \"Copy path\" if unsure.")
dbutils.widgets.text("app_name", "erd-explorer", "Databricks App name")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse id (required)")
dbutils.widgets.text("erd_catalogs", "megacorp", "Catalogs to visualize (comma-separated; leave BLANK for unscoped -- every catalog visible to this deployment, including Genie)")
dbutils.widgets.text("erd_metadata_location", "", "Genie metadata views location \"catalog.schema\" (blank = <first erd_catalogs entry>.erd_meta; REQUIRED if erd_catalogs is blank)")
dbutils.widgets.dropdown("create_demo_data", "no", ["yes", "no"], "Create the synthetic megacorp schemas/tables? Works whether demo_catalog already exists (e.g. you don't have CREATE CATALOG permission -- only schemas/tables get added to it) or not (it gets created too)")
dbutils.widgets.text("demo_catalog", "", "Catalog to create the demo data in (only used if create_demo_data=yes). Blank = reuse the first erd_catalogs entry, so you don't have to type the same catalog name twice; falls back to \"megacorp\" if erd_catalogs is also blank.")
dbutils.widgets.dropdown("add_demo_metadata", "no", ["yes", "no"], "Also add illustrative COMMENTs/tags to the demo data? (separate opt-in -- most real deployments won't want fabricated metadata layered onto their own catalogs, and even demo users may want the bare structure only)")

repo_root_widget = dbutils.widgets.get("repo_root").strip()
app_name = dbutils.widgets.get("app_name").strip()
warehouse_id = dbutils.widgets.get("warehouse_id").strip()
erd_catalogs_raw = dbutils.widgets.get("erd_catalogs").strip()
erd_metadata_location_raw = dbutils.widgets.get("erd_metadata_location").strip()
create_demo_data = dbutils.widgets.get("create_demo_data") == "yes"
demo_catalog_raw = dbutils.widgets.get("demo_catalog").strip()
add_demo_metadata = dbutils.widgets.get("add_demo_metadata") == "yes"

assert repo_root_widget, "repo_root widget is required -- the Workspace path to this repo's checkout"
assert app_name, "app_name widget is required"
assert warehouse_id, "warehouse_id widget is required -- pick any SQL warehouse id from your workspace"
# erd_catalogs is intentionally NOT required -- leaving it blank is a deliberate "unscoped"
# mode (every catalog visible to this deployment's credentials), matching server/config.py.

catalogs = [c.strip() for c in erd_catalogs_raw.split(",") if c.strip()]
# Same guard as the CLI route's resolve_metadata_location() -- only treat the widget
# value as a real "catalog.schema" if it actually contains a dot; otherwise fall back to
# the computed default instead of crashing on `.split(".", 1)` unpacking a 1-element list.
if erd_metadata_location_raw and "." in erd_metadata_location_raw:
    metadata_location = erd_metadata_location_raw
elif catalogs:
    metadata_location = f"{catalogs[0]}.erd_meta"
else:
    raise ValueError(
        "erd_metadata_location is required when erd_catalogs is blank (unscoped mode) "
        "-- there's no catalog to default the metadata views into."
    )
metadata_catalog, metadata_schema = metadata_location.split(".", 1)

# demo_catalog cascades from erd_catalogs (same "don't type the same name twice" pattern
# as erd_metadata_location above) so the common case -- demo data + app pointed at the
# same one catalog -- only needs erd_catalogs filled in.
if demo_catalog_raw:
    demo_catalog = demo_catalog_raw
elif catalogs:
    demo_catalog = catalogs[0]
else:
    demo_catalog = "megacorp"

print(f"app_name={app_name}")
print(f"warehouse_id={warehouse_id}")
print(f"catalogs={catalogs}")
print(f"metadata_location={metadata_location}")
print(f"create_demo_data={create_demo_data}")
print(f"demo_catalog={demo_catalog}")
print(f"add_demo_metadata={add_demo_metadata}")

# COMMAND ----------

# MAGIC %md ## 2. Locate this repo's checkout and import the shared setup modules
# MAGIC Reuses `setup/create_scoped_views.py` and `setup/create_genie_space.py` directly --
# MAGIC this notebook does not re-implement any of that logic.
# MAGIC
# MAGIC Uses the `repo_root` widget rather than trying to auto-detect the notebook's own
# MAGIC path -- the auto-detection tricks (`os.getcwd()`, the notebook context API) turned
# MAGIC out to behave inconsistently across interactive/job/serverless compute, so an
# MAGIC explicit path is more reliable than clever detection here.

# COMMAND ----------

import os
import sys

REPO_ROOT = repo_root_widget.rstrip("/")
SETUP_DIR = os.path.join(REPO_ROOT, "setup")
assert os.path.isdir(SETUP_DIR), f"Could not find setup/ under repo_root={REPO_ROOT} (looked in {SETUP_DIR}) -- double check the repo_root widget points at this repo's checkout."

sys.path.insert(0, SETUP_DIR)
import create_scoped_views
import create_genie_space
import create_megacorp_demo
import grant_catalog_access

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()  # native workspace auth -- runs as whoever executes this notebook
# NOTE: Apps operations below use raw REST calls (w.api_client.do) rather than the typed
# databricks-sdk Apps model classes (App/AppDeployment/EnvVar/...) -- those classes are
# only available in newer SDK versions, and notebook compute's preinstalled SDK version
# can lag behind what's on PyPI regardless of environment `dependencies` declarations.
# Raw dicts avoid that version coupling entirely.
print(f"Repo root: {REPO_ROOT}")
print(f"Authenticated as: {w.current_user.me().user_name}")

# COMMAND ----------

# MAGIC %md ## 3. (Optional) Create the synthetic megacorp demo data
# MAGIC Skip this if you set `erd_catalogs` to your own existing catalog(s) above.
# MAGIC
# MAGIC `demo_catalog` is created if it doesn't already exist, or used as-is (no attempt to
# MAGIC create it) if it does -- so this works whether you're pointing at a brand-new
# MAGIC catalog name or one you already have USE/CREATE SCHEMA rights on but not
# MAGIC metastore-level CREATE CATALOG rights.

# COMMAND ----------

if create_demo_data:
    create_megacorp_demo.create_megacorp_demo(w, warehouse_id, demo_catalog)
else:
    print("Skipped (create_demo_data=no).")

# COMMAND ----------

# MAGIC %md ## 3b. (Optional) Add illustrative comments/tags to the demo data
# MAGIC Independent of step 3 above -- this layers a handful of illustrative `COMMENT`/tag
# MAGIC statements onto a few `demo_catalog` columns/tables purely to demo the ERD viewer's
# MAGIC comment/tag surfacing feature. Works against a catalog that already has the demo
# MAGIC structure from a previous run, without needing to re-run step 3. Skip this if you'd
# MAGIC rather see the bare structure, or if you're pointing at your own catalog(s) instead.

# COMMAND ----------

if add_demo_metadata:
    create_megacorp_demo.add_demo_metadata(w, warehouse_id, demo_catalog)
else:
    print("Skipped (add_demo_metadata=no).")

# COMMAND ----------

# MAGIC %md ## 4. Create the scoped Genie metadata views
# MAGIC Same function the CLI/DAB route's `setup/create_scoped_views.py` job task calls.

# COMMAND ----------

view_statements = create_scoped_views.build_statements(catalogs, metadata_catalog, metadata_schema)
for i, stmt in enumerate(view_statements, 1):
    print(f"[{i}/{len(view_statements)}] {stmt.strip().splitlines()[0][:70]}...", end=" ")
    resp = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=stmt, wait_timeout="50s")
    assert resp.status.state.value == "SUCCEEDED", resp.status.error
    print("ok")
print(f"\nScoped views ready in {metadata_location}.")

# COMMAND ----------

# MAGIC %md ## 5. Stage an isolated deploy folder and write its `app.yaml`
# MAGIC Copies the app source into `/Workspace/Users/<you>/.erd-explorer-deploy/<app_name>/app`
# MAGIC rather than deploying directly from the Git folder, so widget-driven config never
# MAGIC touches (or drifts from) the tracked `app.yaml` in git.

# COMMAND ----------

import shutil

me = w.current_user.me().user_name
deploy_dir = f"/Workspace/Users/{me}/.erd-explorer-deploy/{app_name}/app"
deploy_local_path = deploy_dir  # Workspace paths are POSIX-addressable from notebook Python

# Only copy what the app actually needs to run -- not setup/, docs/, notebooks/, etc.
APP_SOURCE_ITEMS = ["app.py", "server", "frontend/dist", "pyproject.toml", "requirements.txt"]

if os.path.isdir(deploy_local_path):
    shutil.rmtree(deploy_local_path)
os.makedirs(deploy_local_path, exist_ok=True)

for item in APP_SOURCE_ITEMS:
    src = os.path.join(REPO_ROOT, item)
    dst = os.path.join(deploy_local_path, item)
    if not os.path.exists(src):
        print(f"WARNING: {src} not found -- did you run `cd frontend && npm run build`? Skipping.")
        continue
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)

print(f"Deploy folder staged at {deploy_local_path}")

# COMMAND ----------

# MAGIC %md ## 6. Create (or reuse) the Databricks App and deploy the staged source
# MAGIC Env vars are set directly on the deployment (no `app.yaml` patching needed --
# MAGIC deployment-time env vars take precedence). Uses plain REST calls via
# MAGIC `w.api_client.do(...)` rather than the typed SDK model classes, since notebook
# MAGIC compute's preinstalled `databricks-sdk` version can lag behind what a custom
# MAGIC environment's `dependencies` would otherwise pull in.

# COMMAND ----------

import time


def get_or_create_app(name: str, warehouse_id: str) -> dict:
    try:
        return w.api_client.do(method="GET", path=f"/api/2.0/apps/{name}")
    except Exception:
        pass
    body = {
        "name": name,
        "description": "Interactive Unity Catalog ERD viewer (notebook-deployed)",
        "resources": [
            {"name": "sql-warehouse", "sql_warehouse": {"id": warehouse_id, "permission": "CAN_USE"}}
        ],
    }
    w.api_client.do(method="POST", path="/api/2.0/apps", body=body)
    return _wait_for_app_active(name)


def _wait_for_app_active(name: str, timeout_s: int = 600) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app = w.api_client.do(method="GET", path=f"/api/2.0/apps/{name}")
        state = (app.get("compute_status") or {}).get("state")
        if state in ("ACTIVE", "STOPPED"):
            return app
        if state == "ERROR":
            raise RuntimeError(f"App compute failed: {app.get('compute_status')}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for app {name} to become active")


def deploy_app(name: str, source_code_path: str, env_vars: dict) -> dict:
    body = {
        "source_code_path": source_code_path,
        "mode": "SNAPSHOT",
        "env_vars": [{"name": k, "value": v} for k, v in env_vars.items()],
    }
    resp = w.api_client.do(method="POST", path=f"/api/2.0/apps/{name}/deployments", body=body)
    deployment_id = resp["deployment_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        dep = w.api_client.do(method="GET", path=f"/api/2.0/apps/{name}/deployments/{deployment_id}")
        state = (dep.get("status") or {}).get("state")
        if state == "SUCCEEDED":
            return dep
        if state == "FAILED":
            raise RuntimeError(f"Deployment failed: {dep.get('status')}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for deployment {deployment_id}")


def start_app(name: str) -> dict:
    current = w.api_client.do(method="GET", path=f"/api/2.0/apps/{name}")
    if (current.get("compute_status") or {}).get("state") == "ACTIVE":
        return current  # already running -- deploying while active serves the new code
    w.api_client.do(method="POST", path=f"/api/2.0/apps/{name}/start")
    return _wait_for_app_active(name)


app = get_or_create_app(app_name, warehouse_id)
app_sp_client_id = app["service_principal_client_id"]
print(f"App: {app['name']} (service_principal_client_id={app_sp_client_id})")

# First deploy: GENIE_SPACE_ID isn't known yet (created in the next step) -- deploy once
# without it, the chat will show a friendly "not configured" message until step 8 redeploys.
deploy_app(
    app_name,
    deploy_local_path,
    {"DATABRICKS_WAREHOUSE_ID": warehouse_id, "ERD_CATALOGS": ",".join(catalogs)},
)
print("Initial deployment complete.")

# COMMAND ----------

# MAGIC %md ## 7. Create/update the Genie Space and grant the app access to it
# MAGIC Same functions the CLI/DAB route's `setup/create_genie_space.py` job task calls,
# MAGIC including the `--grant-to-app`-equivalent Genie Space ACL grant.

# COMMAND ----------

genie_builder = create_genie_space.build_serialized_space(catalogs, metadata_location, warehouse_id)
existing_space_id = create_genie_space.find_managed_space_id(w)

if existing_space_id:
    print(f"Updating existing Genie Space {existing_space_id}...")
    w.api_client.do(
        method="PATCH",
        path=f"/api/2.0/genie/spaces/{existing_space_id}",
        body={
            "title": genie_builder.title,
            "description": genie_builder.description,
            "warehouse_id": genie_builder.warehouse_id,
            "serialized_space": genie_builder.to_json(),
        },
    )
    genie_space_id = existing_space_id
else:
    parent_path = f"/Workspace/Users/{me}/erd-explorer-genie"
    w.workspace.mkdirs(parent_path)
    print(f"Creating new Genie Space under {parent_path}...")
    resp = w.api_client.do(
        method="POST",
        path="/api/2.0/genie/spaces",
        body={
            "title": genie_builder.title,
            "description": genie_builder.description,
            "parent_path": parent_path,
            "warehouse_id": genie_builder.warehouse_id,
            "serialized_space": genie_builder.to_json(),
        },
    )
    genie_space_id = resp["space_id"]

print(f"Genie Space ready: space_id={genie_space_id}")

# Genie Spaces are a separate workspace-object ACL from the UC grants below -- the app's
# own service principal needs this explicitly, same as the CLI/DAB route's --grant-to-app.
w.api_client.do(
    method="PATCH",
    path=f"/api/2.0/permissions/genie/{genie_space_id}",
    body={"access_control_list": [{"service_principal_name": app_sp_client_id, "permission_level": "CAN_RUN"}]},
)
print(f"Granted CAN_RUN on the Genie Space to {app_name}'s service principal.")

# COMMAND ----------

# MAGIC %md ## 8. Redeploy with the real `GENIE_SPACE_ID`, then start the app

# COMMAND ----------

deploy_app(
    app_name,
    deploy_local_path,
    {
        "DATABRICKS_WAREHOUSE_ID": warehouse_id,
        "ERD_CATALOGS": ",".join(catalogs),
        "GENIE_SPACE_ID": genie_space_id,
    },
)
start_app(app_name)

app = w.api_client.do(method="GET", path=f"/api/2.0/apps/{app_name}")
print(f"\nApp is live: {app['url']}")

# COMMAND ----------

# MAGIC %md ## 9. Grant the app's service principal access to your catalog(s)
# MAGIC Uses the `service_principal_client_id` fetched from the Apps API in step 6 --
# MAGIC no need to copy/paste it. Same `setup/grant_catalog_access.py` function the CLI
# MAGIC route's equivalent script calls. Catalog-level grants cascade to every schema/table
# MAGIC inside (matching `erd_catalogs` being catalog-level scoping), plus a
# MAGIC schema-specific grant for the Genie metadata location. Requires *you* (whoever is
# MAGIC running this notebook) to already have grant-issuing rights on these catalogs --
# MAGIC if you don't, the statements below fail with a clear permission error rather than
# MAGIC silently doing nothing, and print the exact SQL for a catalog admin to run instead.
# MAGIC
# MAGIC Skipped entirely in unscoped mode (`erd_catalogs` blank) -- there's no fixed catalog
# MAGIC list to grant on; the app relies on whatever grants its service principal already
# MAGIC has, same as any other unscoped deployment.

# COMMAND ----------

grant_catalog_access.grant_catalog_access(w, warehouse_id, catalogs, metadata_catalog, metadata_schema, app_sp_client_id)
