# Multi-Tenant Genie RLS — scalable tenant-isolated Genie agent (Azure)

A working reference for **per-tenant row-level security on a Genie-backed agent**, built
to the customer's **Azure** constraints (no AWS-only features). It **scales to N firms** with
**one** Genie Space and **one** Unity Catalog ABAC row-filter policy — no per-tenant Spaces.

The customer's **Azure app (not a Databricks App)** authenticates its end user and calls
the deployed **agent** server-to-server with a **trusted `tenant_id`**. The agent calls the
shared Genie Space **as that firm's service principal**, so a UC **ABAC row-filter policy**
keyed on `current_user()` returns only that firm's rows (workspace **admins bypass**). See
[DESIGN.md](DESIGN.md).

## How it scales (the key idea)

| | This design |
|---|---|
| Genie Spaces | **1** (shared) |
| Enforcement | **1** ABAC policy (`firm_rls`) + **1** governed tag (`rls_firm`) + `rls_firm_filter` (admins bypass, default-deny) |
| Per firm | 1 service principal + 1 `entitlements` row + 1 OBO token |
| Provisioning | a reconcile **job** reads the firms lookup table and creates SPs/tokens/grants |

1000 firms → 1000 SPs + 1000 `entitlements` rows, still **one** Space and **one** ABAC policy.

## What's deployed

| Layer | Object |
|---|---|
| Data | `demos.genie_rls` — `firms` (lookup), `gl_transactions`, `clients`, `invoices` (3 firms) |
| RLS | **ABAC policy** `firm_rls` + governed tag `rls_firm` + `entitlements` (principal→firm) + `rls_firm_filter` (`is_member('admins')` bypass; `ELSE FALSE`) |
| Identities | per-firm SPs `firm-<firm_id>`; OBO tokens in secret scope `genie_rls` |
| Genie | ONE shared Space `<GENIE_SPACE_ID>` |
| Agent | `demos.genie_rls.tenant_agent` → endpoint `agents_demos-genie_rls-tenant_agent` |
| LLM | `databricks-claude-sonnet-4-6` (synthesis) + `databricks-meta-llama-3-1-8b-instruct` (guardrail) |

## Configure for your environment

This repo uses `<PLACEHOLDER>` markers for environment-specific values — **nothing here is
customer-specific**. Set them as environment variables (the scripts read these), or edit in place:

| Env var | Used for |
|---|---|
| `DATABRICKS_PROFILE` | Databricks CLI profile used for auth (`databricks auth profiles`) |
| `DATABRICKS_HOST` | your workspace URL, e.g. `https://<your-workspace>.cloud.databricks.com` |
| `WAREHOUSE_ID` | SQL warehouse that runs the queries / Genie Space |
| `GENIE_SPACE_ID` | the single shared Genie Space (created in step 3) |
| `DATABRICKS_TOKEN` | (mock app only) bearer token; or rely on `DATABRICKS_PROFILE` |

```bash
export DATABRICKS_PROFILE=my-workspace
export WAREHOUSE_ID=abcdef1234567890
export GENIE_SPACE_ID=01f0your_space_id
```
Also replace `<WORKSPACE_USERNAME>` (your workspace user) in the workspace file paths in
`agent/run_build.py` and `jobs/provisioner_job.json`.

## Reproduce

```bash
# 1. data + lookup
python3 scripts/run_sql.py sql/01_create_data.sql
# 2. entitlements table (principal -> firm mapping)
python3 scripts/run_sql.py sql/03_rls_scalable.sql
# 2b. ABAC policy + governed tag + row-filter fn (admins bypass, default-deny)
DBX_PROFILE=<DATABRICKS_PROFILE> python3 scripts/apply_abac.py
# 3. one shared Genie Space — created via the databricks-genie skill (manage_genie)
# 4. provision per-firm SPs + tokens + grants + entitlements from the firms lookup table
DBX_PROFILE=<DATABRICKS_PROFILE> python3 scripts/provision_sps.py
# 5. log + deploy the agent (serverless job with declared env; secret-backed FIRM_TOKEN vars)
#    upload agent/ to the workspace, submit agent/run_build.py as a serverless job
```

`scripts/provision_sps.py` is the job to schedule (or trigger on lookup-table updates):
it is idempotent and reconciles SPs to the source table.

## Run the mock Azure app (test client — runs locally, not a Databricks App)

```bash
cd mock_azure_app && pip install -r requirements.txt
export AGENT_ENDPOINT=agents_demos-genie_rls-tenant_agent
python client.py --tenant firm_001 "What were total expenses by account this year?"
streamlit run app.py        # firm selector UI
```

## Tests (prove isolation)

```bash
python3 scripts/test_isolation.py
```

1. **Isolation** — same question across the 3 firms → different, firm-scoped results.
2. **Prompt injection** — as firm_001, asking for Summit Ledger's data still returns only firm_001 rows.
3. **Missing tenant** — no `tenant_id` ⇒ access denied, no data queried.

Already verified at the data layer: each firm SP querying the **shared** table / Genie Space
sees only its own firm's rows.

## Guardrails

- **In-agent input guardrail** (LLM-judge) blocks prompt-injection / cross-tenant / unsafe input.
- **Structural** defense: tenant→SP routing is deterministic from the trusted `tenant_id`; the LLM cannot pick the firm, and the UC row filter caps blast radius regardless.
- **Production upgrade** (Azure-valid): AI Gateway Safety + PII on the LLM endpoint (Public Preview; external-model / FMAPI pay-per-token only) + dedicated Prompt Guard 2 / Llama Guard 4 endpoints.

## Not included (by design)

- **OAuth Custom Identity Claims** — native "pass a claim, UC enforces" path, but **AWS-only Private Preview**.
- **Entra External ID + OBO** — the cleaner native-identity version of the same single-Space + `current_user()` design (no per-firm secrets; identities flow from the IdP).
