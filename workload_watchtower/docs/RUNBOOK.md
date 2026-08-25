# Workload Watchtower — Admin Runbook

Step-by-step to stand up Watchtower in your workspace, plus the steps `setup/setup.sh` can't
safely do for you (account-admin grants, embedding, UI fallbacks) and how to verify + tear down.

---

## 0. Prerequisites checklist

- [ ] Workspace has **Unity Catalog** and **serverless** enabled.
- [ ] You are a **workspace admin** (required — the poller reads *all* users' Query History /
      Jobs / Pipelines, which only an admin can see).
- [ ] **Databricks CLI ≥ 0.285.0**: `databricks --version` (Lakebase Autoscaling commands live
      under `databricks postgres`).
- [ ] A **serverless SQL warehouse** — note its ID (Warehouse → ⋯ → Copy ID).
- [ ] A UC **catalog** where you can create a schema, and the privilege to `CREATE SCHEMA` in it.
- [ ] A **chat serving endpoint** for the AI features (e.g. `databricks-claude-sonnet-5`) — check
      **Serving** in the workspace.
- [ ] Local tools: `python3`, `npm`, `jq`, `envsubst` (macOS: `brew install gettext jq`).
- [ ] *(Optional, email)* SMTP host/credentials (e.g. SendGrid, Gmail app password).

---

## 1. Authenticate the CLI

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com --profile <profile>
databricks current-user me --profile <profile>      # confirm it works
```

## 2. Fill in the config

```bash
cp setup/config.env.example setup/config.env
```

Edit `setup/config.env`:

| Key | What to put |
|---|---|
| `DATABRICKS_PROFILE` / `WORKSPACE_HOST` | the profile + URL from step 1 |
| `WORKSPACE_LABEL` | short label for the app's top-right chip (e.g. `acme-prod`) |
| `WAREHOUSE_ID` | your serverless SQL warehouse ID |
| `UC_SCHEMA` | `<catalog>.<schema>` for history (e.g. `main.workload_monitoring`) — created if absent |
| `LAKEBASE_PROJECT` / `_BRANCH` / `_ENDPOINT_ID` | reuse an existing Autoscaling project or let setup create `LAKEBASE_PROJECT` |
| `LAKEBASE_SCHEMA` | Postgres schema for triage state (default `watchtower`) |
| `APP_NAME` | Databricks App name (mints its service principal) |
| `WT_MODEL` | a chat serving endpoint |
| `SEED_MEMBERS_JSON` | *(optional)* path to a roster JSON (see `setup/it_members.example.json`) |
| `DEPLOY_MONITORING` | `true` to deploy the AI/BI dashboard |
| `CONFIGURE_SMTP` + `SMTP_*` | *(optional)* email automations |

`config.env` is gitignored (it can hold SMTP credentials).

## 3. Run setup

```bash
./setup/setup.sh
```

It prints a plan and asks before making changes, then runs every step idempotently. Re-run it any
time — it only creates what's missing. When it finishes it prints the app URL.

---

## 4. Manual / account-admin steps

### 4a. (Recommended) Run the poller as the app service principal

By default the poller runs **as you** (the deploying admin). To decouple it from a person and run
it as the app's service principal, one **account-admin** action is required (it can't be done with
workspace admin alone):

1. Grant your user the `servicePrincipal.user` role **on the app SP** so a bundle can bind
   `run_as` to it. As an **account admin**:
   ```bash
   databricks auth login --host https://accounts.cloud.databricks.com --account-id <ACCOUNT_ID>
   # then, via the account access-control rule-set for the SP, add your user as servicePrincipal.user
   ```
   (Account console → User management → Service principals → the app SP → Permissions → add your
   user as *Manager/User*.)
2. Add a `run_as` block to the `targets.default` stanza in `databricks.yml`:
   ```yaml
   targets:
     default:
       # ...
       run_as:
         service_principal_name: <APP_SP_CLIENT_ID>   # printed by setup.sh
   ```
3. Redeploy: re-run `./setup/setup.sh` (or `databricks bundle deploy -t default -p <profile> --var=...`).

> The app SP must be a **workspace admin** for cross-user visibility, and `main()` tolerates a
> non-owner SP failing to (re)create indexes — the schema is created once, by you, in step 3.
>
> **If you use the SQL Alert task (Alerting option 2):** the `watchtower_critical` alert is owned by
> the deployer, so a poller running as the app SP can't evaluate it ("SQL entity could not be
> found / not accessible"). Grant the app SP access to the alert — add a `permissions` block to the
> `resources.alerts.watchtower_critical` resource, e.g. `- level: CAN_RUN` with
> `service_principal_name: <APP_SP_CLIENT_ID>` — and redeploy.

### 4b. Enable the Monitoring dashboard embed

The Monitoring page embeds the AI/BI dashboard via an `/embed/dashboardsv3/<id>` URL (the
`/published` console URL refuses framing). For the iframe to render:

1. **Workspace admin → Settings → Security → AI/BI dashboard embedding** (labeled *Embed
   dashboards*): set an approved-domains policy that includes your app's domain
   (`*.databricksapps.com`), or *Allow all domains* for a quick internal rollout.
2. **Genie Agents** — enable this setting too if you want the dashboard's in-panel **Ask Genie** to
   work inside the embed.
3. The embed renders in a **direct browser tab** with **third-party cookies enabled**. In the
   in-workspace app preview or with 3p cookies blocked, use the page's **Open in Databricks**
   button instead. (Verified live: after enabling *Embed dashboards*, reload the app tab — the
   Monitoring page renders the 6-page suite in-app.)

If the embed still refuses, open the dashboard → **Share → Embed** and copy the exact iframe `src`
into `DASHBOARD_EMBED_URL` in `config.env`, then re-run the app-deploy step.

### 4c. Deploy the app — UI fallback

`setup.sh` attaches the Lakebase database + warehouse as app resources and deploys the code. The
resource JSON shape can vary by Lakebase tier / CLI version; if that step warns, do it in the UI:

1. **Compute → Apps → `APP_NAME` → Edit → Resources**: add your **Lakebase database**
   (`databricks_postgres`, *Can connect and create*) and your **SQL warehouse** (*Can use*).
2. Deploy the code (frontend is already built by setup):
   ```bash
   SRC=/Workspace/Users/<you>/<APP_NAME>-src
   databricks sync app "$SRC" --exclude 'frontend/src' --exclude 'frontend/node_modules' --exclude '.venv' -p <profile>
   databricks apps deploy <APP_NAME> --source-code-path "$SRC" -p <profile>
   ```

### 4d. SMTP (if you didn't use `CONFIGURE_SMTP`)

```bash
databricks secrets create-scope <SECRET_SCOPE> -p <profile>
databricks secrets put-acl    <SECRET_SCOPE> <APP_SP_CLIENT_ID> READ -p <profile>
databricks secrets put-secret <SECRET_SCOPE> smtp_host --string-value smtp.sendgrid.net -p <profile>
# + smtp_port / smtp_user / smtp_password / smtp_from
```

---

## 5. Verify end-to-end

1. Open the app URL. The **Dashboard** loads with a "last poll" tile.
2. Click **Run poll** (top-right). Within a few seconds the poll run should complete; open the
   **Actions → poll runs**-style view or the Dashboard tile to confirm `workloads_seen > 0`.
3. Lower a rule threshold in **Rules** (e.g. long-running query to 60s) and start a slow query in
   the workspace; the next poll should raise a finding + card on the **Triage Board**.
4. On a finding, click **Explain** — the Triage Copilot should return a grounded diagnosis
   (confirms `WT_MODEL` + serving access).
5. If SMTP is configured, use **Actions → Send** on a drafted email to confirm delivery.
6. If monitoring is deployed, open **Monitoring** and confirm the embed renders (see 4b).

---

## 6. Troubleshooting (known gotchas)

| Symptom | Cause / fix |
|---|---|
| Poller job fails: `cannot locate poller module directory` | Serverless `spark_python_task` runs without `__file__`; the resolver globs `/Workspace/Users/*/.bundle/*/*/files`. If your bundle path differs, set `WT_BUNDLE_FILES_PATH` on the job. |
| Poller fails minting Lakebase creds / `w.postgres` missing | The serverless image shipped an old SDK. The bundle pins `databricks-sdk>=0.96.0`; confirm it deployed. |
| App can't connect to Lakebase: `password authentication failed` | The app SP's Postgres role must be **federated** via `databricks postgres create-role` (setup does this). A manual `CREATE ROLE` is non-federated and rejected — never create it that way. |
| Poller (as app SP) logs `must be owner of table findings` | Expected — a non-owner SP can't `CREATE INDEX`. `main()` soft-fails bootstrap and polls anyway; the schema was created by you in step 3. |
| Blank Monitoring iframe | See 4b — approved domains + third-party cookies, or use *Open in Databricks*. |
| Trends/Dashboard numbers look wrong or the app errors on a number | UC Statement Execution returns numbers as strings; the app coerces them and has an error boundary — re-run the app-deploy step if you edited `uc.py`. |
| A warehouse permission you set earlier disappeared | Use `warehouses update-permissions` (merge), not `set-permissions` (replace) — setup uses the former. |

---

## 7. Teardown

```bash
set -a && . setup/config.env && set +a
P="--profile $DATABRICKS_PROFILE"
databricks apps delete "$APP_NAME" $P
databricks bundle destroy -t default $P              # removes the poller job
# Lakebase project + UC schema hold data — delete deliberately:
# databricks postgres delete-project projects/$LAKEBASE_PROJECT $P
# DROP SCHEMA $UC_SCHEMA CASCADE;  (and DROP SCHEMA $LAKEBASE_SCHEMA in Postgres)
databricks secrets delete-scope "$SECRET_SCOPE" $P   # if you created it
```
