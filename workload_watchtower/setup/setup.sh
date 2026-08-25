#!/usr/bin/env bash
#
# setup.sh — stand up Workload Watchtower in a Databricks workspace.
#
# Run as a WORKSPACE ADMIN with a configured CLI profile. The script is idempotent:
# every step checks-before-creating, so it is safe to re-run after fixing config or
# a transient failure. It performs privileged actions (creates a service principal,
# federates a Lakebase role, grants Unity Catalog / warehouse / job permissions) — it
# prints a plan and asks for confirmation before making any changes.
#
#   cp setup/config.env.example setup/config.env   # then edit values
#   ./setup/setup.sh                               # from the repo root
#
# See docs/RUNBOOK.md for the manual/account-admin steps this script cannot do, and for
# UI fallbacks if a CLI call's shape differs on your workspace/CLI version.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONFIG="setup/config.env"

# ── helpers ──────────────────────────────────────────────────────────────────
c_blue='\033[1;34m'; c_grn='\033[1;32m'; c_yel='\033[1;33m'; c_red='\033[1;31m'; c_off='\033[0m'
say()  { echo -e "${c_blue}▶ $*${c_off}"; }
ok()   { echo -e "${c_grn}  ✓ $*${c_off}"; }
warn() { echo -e "${c_yel}  ! $*${c_off}"; }
die()  { echo -e "${c_red}✗ $*${c_off}" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."; }

# ── 0. preflight ───────────────────────────────────────────────────────────--
say "Preflight checks"
need databricks; need jq; need python3; need npm; need envsubst
[[ -f "$CONFIG" ]] || die "$CONFIG not found. Copy setup/config.env.example to it and edit."
# shellcheck disable=SC1090
set -a && . "$CONFIG" && set +a

: "${DATABRICKS_PROFILE:?set DATABRICKS_PROFILE in $CONFIG}"
: "${WORKSPACE_HOST:?}"; : "${WAREHOUSE_ID:?}"; : "${UC_SCHEMA:?}"
: "${LAKEBASE_PROJECT:?}"; : "${LAKEBASE_BRANCH:?}"; : "${LAKEBASE_ENDPOINT_ID:?}"
: "${LAKEBASE_SCHEMA:?}"; : "${APP_NAME:?}"; : "${POLLER_JOB_NAME:?}"; : "${SECRET_SCOPE:?}"
export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"
P=(--profile "$DATABRICKS_PROFILE")

databricks current-user me "${P[@]}" >/dev/null 2>&1 || \
  die "profile '$DATABRICKS_PROFILE' is not authenticated. Run: databricks auth login --host $WORKSPACE_HOST --profile $DATABRICKS_PROFILE"
ME="$(databricks current-user me "${P[@]}" -o json | jq -r '.userName')"
ok "authenticated as $ME on $WORKSPACE_HOST"

CATALOG="${UC_SCHEMA%%.*}"
LAKEBASE_BRANCH_PATH="projects/${LAKEBASE_PROJECT}/branches/${LAKEBASE_BRANCH}"
LAKEBASE_ENDPOINT="${LAKEBASE_BRANCH_PATH}/endpoints/${LAKEBASE_ENDPOINT_ID}"

cat <<PLAN

  This will, in $WORKSPACE_HOST (profile: $DATABRICKS_PROFILE):
    • ensure Lakebase project '$LAKEBASE_PROJECT' (branch $LAKEBASE_BRANCH / endpoint $LAKEBASE_ENDPOINT_ID)
    • create UC schema $UC_SCHEMA + history tables (warehouse $WAREHOUSE_ID)
    • create Postgres schema '$LAKEBASE_SCHEMA' + seed governance rules (as $ME)
    • create Databricks App '$APP_NAME' (mints its service principal)
    • federate + GRANT the app SP on Lakebase, UC ($UC_SCHEMA), and warehouse $WAREHOUSE_ID
    • deploy the poller job '$POLLER_JOB_NAME' (runs every: ${poller_schedule:-0 0/5 * * * ?})
    • ${DEPLOY_MONITORING:-true} → deploy the Monitoring dashboard
    • ${CONFIGURE_SMTP:-false} → write SMTP secrets into scope '$SECRET_SCOPE'
    • render app/app.yaml, build the frontend, and deploy the app

PLAN
read -r -p "Proceed? [y/N] " reply
[[ "$reply" =~ ^[Yy]$ ]] || die "aborted."

# ── setup venv for the python helper scripts ─────────────────────────────────
say "Python env for setup helpers"
VENV="setup/.venv"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null
"$VENV/bin/pip" install -q -r setup/requirements.txt
PY="$VENV/bin/python"
ok "venv ready ($VENV)"

# ── 1. Lakebase project / branch / endpoint ──────────────────────────────────
say "Lakebase (Autoscaling) project"
if databricks postgres get-project "projects/${LAKEBASE_PROJECT}" "${P[@]}" >/dev/null 2>&1; then
  ok "project '$LAKEBASE_PROJECT' exists — reusing"
else
  databricks postgres create-project "$LAKEBASE_PROJECT" \
    --json "{\"spec\":{\"display_name\":\"$LAKEBASE_PROJECT\"}}" "${P[@]}" >/dev/null
  ok "created project '$LAKEBASE_PROJECT'"
fi
# discover the read-write endpoint host
LAKEBASE_HOST="$(databricks postgres list-endpoints "$LAKEBASE_BRANCH_PATH" "${P[@]}" -o json \
  | jq -r --arg e "$LAKEBASE_ENDPOINT_ID" '.[] | select(.name|endswith($e)) | .status.hosts.host' | head -1)"
[[ -n "$LAKEBASE_HOST" && "$LAKEBASE_HOST" != "null" ]] || \
  LAKEBASE_HOST="$(databricks postgres list-endpoints "$LAKEBASE_BRANCH_PATH" "${P[@]}" -o json | jq -r '.[0].status.hosts.host')"
[[ -n "$LAKEBASE_HOST" && "$LAKEBASE_HOST" != "null" ]] || die "could not resolve Lakebase endpoint host — is the endpoint ACTIVE?"
export LAKEBASE_ENDPOINT LAKEBASE_HOST LAKEBASE_SCHEMA
ok "endpoint host: $LAKEBASE_HOST"

# ── 2. Unity Catalog history schema + tables ─────────────────────────────────
say "Unity Catalog history ($UC_SCHEMA)"
UC_SCHEMA="$UC_SCHEMA" WT_WAREHOUSE_ID="$WAREHOUSE_ID" "$PY" setup/run_uc_ddl.py
ok "UC history tables ready"

# ── 3. Lakebase schema + governance rules (as owner) ─────────────────────────
say "Lakebase schema + seed rules"
MEMBERS_ARG=()
[[ -n "${SEED_MEMBERS_JSON:-}" ]] && MEMBERS_ARG=(--members "$SEED_MEMBERS_JSON")
( cd src/db && LAKEBASE_ENDPOINT="$LAKEBASE_ENDPOINT" LAKEBASE_HOST="$LAKEBASE_HOST" \
    LAKEBASE_SCHEMA="$LAKEBASE_SCHEMA" "$REPO_ROOT/$PY" -m db.bootstrap "${MEMBERS_ARG[@]}" )
ok "schema '$LAKEBASE_SCHEMA' + rules seeded"

# ── 4. Databricks App (mints the app SP) ─────────────────────────────────────
say "Databricks App '$APP_NAME'"
if databricks apps get "$APP_NAME" "${P[@]}" >/dev/null 2>&1; then
  ok "app '$APP_NAME' exists — reusing"
else
  databricks apps create "$APP_NAME" "${P[@]}" >/dev/null
  ok "created app '$APP_NAME'"
fi
APP_SP="$(databricks apps get "$APP_NAME" "${P[@]}" -o json | jq -r '.service_principal_client_id // .service_principal_id')"
[[ -n "$APP_SP" && "$APP_SP" != "null" ]] || die "could not read the app's service principal id from 'apps get'"
ok "app service principal: $APP_SP"

# ── 5. Federate the app SP into Lakebase (control-plane create-role) ─────────
say "Federate app SP on Lakebase"
ROLE_ID="$(echo "${APP_NAME}-app" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/^-*//;s/-*$//')"
if databricks postgres list-roles "$LAKEBASE_BRANCH_PATH" "${P[@]}" -o json 2>/dev/null \
     | jq -e --arg sp "$APP_SP" '.[] | select(.spec.postgres_role == $sp)' >/dev/null; then
  ok "app SP already federated"
else
  databricks postgres create-role "$LAKEBASE_BRANCH_PATH" --role-id "$ROLE_ID" \
    --json "{\"spec\":{\"identity_type\":\"SERVICE_PRINCIPAL\",\"postgres_role\":\"$APP_SP\",\"auth_method\":\"LAKEBASE_OAUTH_V1\"}}" "${P[@]}" >/dev/null
  ok "federated app SP as role-id '$ROLE_ID'"
fi
# grant the federated role schema privileges (as owner)
LAKEBASE_ENDPOINT="$LAKEBASE_ENDPOINT" LAKEBASE_HOST="$LAKEBASE_HOST" LAKEBASE_SCHEMA="$LAKEBASE_SCHEMA" \
  "$PY" setup/grant_app_sp_pg.py --app-sp "$APP_SP"
ok "granted app SP Postgres privileges on '$LAKEBASE_SCHEMA'"

# ── 6. Unity Catalog grants for the app SP ───────────────────────────────────
say "Unity Catalog grants for app SP"
databricks grants update catalog "$CATALOG" \
  --json "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_CATALOG\"]}]}" "${P[@]}" >/dev/null
databricks grants update schema "$UC_SCHEMA" \
  --json "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_SCHEMA\",\"SELECT\",\"MODIFY\"]}]}" "${P[@]}" >/dev/null
ok "granted USE_CATALOG on $CATALOG; USE_SCHEMA/SELECT/MODIFY on $UC_SCHEMA"

# ── 7. Warehouse CAN_USE for the app SP (merge, don't replace) ───────────────
say "Warehouse permission for app SP"
databricks warehouses update-permissions "$WAREHOUSE_ID" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_USE\"}]}" "${P[@]}" >/dev/null
ok "granted CAN_USE on warehouse $WAREHOUSE_ID"

# ── 8. Deploy the poller job (bundle) ────────────────────────────────────────
say "Deploy poller bundle"
databricks bundle deploy -t default "${P[@]}" \
  --var="workspace_host=$WORKSPACE_HOST" \
  --var="warehouse_id=$WAREHOUSE_ID" \
  --var="uc_schema=$UC_SCHEMA" \
  --var="lakebase_endpoint=$LAKEBASE_ENDPOINT" \
  --var="lakebase_host=$LAKEBASE_HOST" \
  --var="lakebase_schema=$LAKEBASE_SCHEMA" \
  --var="secret_scope=$SECRET_SCOPE" \
  ${poller_schedule:+--var="poller_schedule=$poller_schedule"}
ok "poller job deployed"
# let the app SP trigger the poller (the "Run poll" button)
JOB_ID="$(databricks jobs list "${P[@]}" -o json | jq -r --arg n "$POLLER_JOB_NAME" '.[] | select(.settings.name==$n) | .job_id' | head -1)"
if [[ -n "$JOB_ID" && "$JOB_ID" != "null" ]]; then
  databricks permissions update jobs "$JOB_ID" \
    --json "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_MANAGE_RUN\"}]}" "${P[@]}" >/dev/null
  ok "app SP can run poller job ($JOB_ID)"
else
  warn "couldn't find poller job id to grant run permission — do it in the Jobs UI"
fi

# ── 9. Monitoring dashboard (optional) ───────────────────────────────────────
if [[ "${DEPLOY_MONITORING:-true}" == "true" ]]; then
  say "Deploy Monitoring dashboard"
  DASH_OUT="$(WT_MON_CATALOG="$CATALOG" WT_MON_SCHEMA="${UC_SCHEMA##*.}" WT_WAREHOUSE_ID="$WAREHOUSE_ID" \
      "$PY" monitoring/deploy_monitoring.py)"
  echo "$DASH_OUT"
  DASHBOARD_URL="$(echo "$DASH_OUT"       | sed -n 's/^DASHBOARD_URL=//p'       | head -1)"
  DASHBOARD_EMBED_URL="$(echo "$DASH_OUT" | sed -n 's/^DASHBOARD_EMBED_URL=//p' | head -1)"
  # persist back into config.env so re-runs and app renders reuse them
  if [[ -n "$DASHBOARD_URL" ]]; then
    python3 - "$CONFIG" "$DASHBOARD_URL" "$DASHBOARD_EMBED_URL" <<'PYW'
import re, sys
cfg, url, embed = sys.argv[1], sys.argv[2], sys.argv[3]
txt = open(cfg).read()
for key, val in (("DASHBOARD_URL", url), ("DASHBOARD_EMBED_URL", embed)):
    txt = re.sub(rf'(?m)^{key}=.*$', f'{key}="{val}"', txt)
open(cfg, "w").write(txt)
PYW
    export DASHBOARD_URL DASHBOARD_EMBED_URL
    ok "dashboard published; URLs written to $CONFIG"
  fi
else
  warn "DEPLOY_MONITORING=false — Monitoring page will be hidden"
  export DASHBOARD_URL="${DASHBOARD_URL:-}" DASHBOARD_EMBED_URL="${DASHBOARD_EMBED_URL:-}"
fi

# ── 10. SMTP secrets (optional) ──────────────────────────────────────────────
if [[ "${CONFIGURE_SMTP:-false}" == "true" ]]; then
  say "Configure SMTP secrets in scope '$SECRET_SCOPE'"
  databricks secrets create-scope "$SECRET_SCOPE" "${P[@]}" 2>/dev/null || true
  databricks secrets put-acl "$SECRET_SCOPE" "$APP_SP" READ "${P[@]}" 2>/dev/null || true
  put() { databricks secrets put-secret "$SECRET_SCOPE" "$1" --string-value "$2" "${P[@]}" >/dev/null; }
  [[ -n "${SMTP_HOST:-}" ]]     && put smtp_host "$SMTP_HOST"
  [[ -n "${SMTP_PORT:-}" ]]     && put smtp_port "$SMTP_PORT"
  [[ -n "${SMTP_USER:-}" ]]     && put smtp_user "$SMTP_USER"
  [[ -n "${SMTP_PASSWORD:-}" ]] && put smtp_password "$SMTP_PASSWORD"
  [[ -n "${SMTP_FROM:-}" ]]     && put smtp_from "$SMTP_FROM"
  ok "SMTP secrets written; app SP has READ"
else
  warn "CONFIGURE_SMTP=false — email automations disabled (findings still show on the board)"
fi

# ── 11. Render app.yaml + build frontend ─────────────────────────────────────
say "Render app/app.yaml + build frontend"
export LAKEBASE_ENDPOINT LAKEBASE_HOST LAKEBASE_SCHEMA WAREHOUSE_ID UC_SCHEMA POLLER_JOB_NAME \
       WORKSPACE_LABEL SECRET_SCOPE WT_MODEL DASHBOARD_URL DASHBOARD_EMBED_URL
: "${WORKSPACE_LABEL:=$APP_NAME}"; : "${WT_MODEL:=databricks-claude-sonnet-5}"
envsubst < app/app.yaml.template > app/app.yaml
ok "wrote app/app.yaml"
( cd app/frontend && npm install --no-audit --no-fund >/dev/null 2>&1 && npm run build >/dev/null )
ok "frontend built (app/frontend/dist)"

# ── 12. Deploy the app ───────────────────────────────────────────────────────
say "Deploy the app"
deploy_app() {
  local src="/Workspace/Users/${ME}/${APP_NAME}-src"
  # attach Lakebase database + warehouse as app resources (auto-injects PG* env)
  databricks apps update "$APP_NAME" --json "$(cat <<JSON
{"resources":[
  {"name":"database","database":{"instance_name":"$LAKEBASE_PROJECT","database_name":"databricks_postgres","permission":"CAN_CONNECT_AND_CREATE"}},
  {"name":"warehouse","sql_warehouse":{"id":"$WAREHOUSE_ID","permission":"CAN_USE"}}
]}
JSON
  )" "${P[@]}" >/dev/null
  databricks sync app "$src" --exclude 'frontend/src' --exclude 'frontend/node_modules' --exclude '.venv' "${P[@]}"
  databricks apps deploy "$APP_NAME" --source-code-path "$src" "${P[@]}"
}
if deploy_app; then
  APP_URL="$(databricks apps get "$APP_NAME" "${P[@]}" -o json | jq -r '.url // empty')"
  ok "app deployed${APP_URL:+: $APP_URL}"
else
  warn "app resource-attach / deploy needs attention — the CLI shape can vary by tier/version."
  warn "See docs/RUNBOOK.md → 'Deploy the app' for the exact commands + the UI fallback."
fi

echo
ok "Setup complete. Next: open the app, confirm the board populates after a poll, and review docs/RUNBOOK.md"
