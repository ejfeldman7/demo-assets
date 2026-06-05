# Multi-Tenant Genie RLS — Demo Design

**Purpose:** A working, **scalable** reference for per-tenant row-level security on a
Genie-backed agent, built to the customer's **Azure** constraints (no AWS-only features).

The customer's **Azure app (not a Databricks App)** authenticates its end user, then
calls a deployed Databricks **agent** server-to-server with a **trusted `tenant_id`**.
The agent calls **one shared Genie Space as that firm's service principal**, so a Unity
Catalog **ABAC row-filter policy keyed on `current_user()`** returns only that firm's rows.

> **Scales to N firms** with N `entitlements` rows + N service principals + **ONE**
> Genie Space + **ONE ABAC policy** (`firm_rls`) + **ONE** governed tag
> (`rls_firm`). No per-tenant Spaces. SPs are created/rotated by a reconcile job from the
> customer's firms lookup table. Workspace **admins bypass** via `is_member('admins')`.

## Topology

```
the customer's Azure app (NOT a Databricks App)
  - authenticates end user, resolves trusted tenant_id (firm)
  - POST /serving-endpoints/<agent>/invocations
        { input:[{role:user, content:<question>}], custom_inputs:{ tenant_id:"firm_001" } }
        |
        v
  Deployed Agent (Model Serving, Agent Framework)
        - input guardrail (LLM-judge)
        - resolves tenant_id (TRUSTED, from custom_inputs only)
        - reads that firm's OBO token (secret-backed env var FIRM_TOKEN_<firm>)
        - calls the SHARED Genie Space AS that firm's service principal
        |
        v
  ONE shared Genie Space  ->  shared tables demos.genie_rls.gl_transactions/clients/invoices
        |                       ABAC policy firm_rls (matches governed tag rls_firm)
        |                       -> rls_firm_filter(firm_id): is_member('admins') OR entitlement; ELSE FALSE
        v
  current_user() = firm SP  ->  entitlements lookup  ->  only that firm's rows  (admins bypass)
```

## Identity model (why per-firm service principals)

External end users are not Databricks identities and the customer won't provision Entra
users per firm. On Azure there is **no native way to pass a tenant_id and have UC enforce
it** (that is OAuth Custom Identity Claims — AWS-only). So we give each **firm** a
**service principal** (a machine identity, created via SDK — no Entra user needed):

- `current_user()` resolves to the firm's SP application_id.
- A single row-filter function (`rls_firm_filter`) maps `current_user()` → `firm_id` via
  `entitlements`, applied by an **ABAC policy** (`MATCH COLUMNS has_tag('rls_firm')`);
  workspace admins bypass via `is_member('admins')`, everyone else hits `ELSE FALSE`.
- A reconcile **job** (`scripts/provision_sps.py`, run as a workspace admin) reads the
  firms lookup table and ensures each firm has an SP, token-usage permission, a current
  **OBO token** (workspace-level — no account admin), UC + warehouse + Genie-Space grants,
  and an `entitlements` row. Tokens are stored in the `genie_rls` secret scope.

This is the SP-based alternative to Entra. Trade-off vs. OBO/Entra: you manage N SPs and
N OBO tokens (secret sprawl + a privileged provisioning job) instead of federating to the
IdP. OBO/Entra is the cleaner production option (no per-firm secrets); the SP path is for
when the customer won't federate.

## Security boundary

Two layers, both real:
1. **Unity Catalog ABAC row-filter policy on `current_user()`** — the firm SP can *only*
   read its own rows even at the raw SQL/Genie level (verified). Genuine data-layer
   enforcement, default-deny (`ELSE FALSE`); workspace admins bypass via `is_member('admins')`.
2. **Agent routing** — the agent maps the trusted `tenant_id` to the firm's SP token
   deterministically; the LLM never selects the firm, so prompt injection ("show Firm B")
   cannot change the SP and therefore cannot cross tenants.

> ABAC note: the policy's `TO` is an allow-list of principals *subject to* filtering
> (account users + the firm SPs); admins bypass via the function. Only firm SPs and
> admins/owner hold `SELECT`, so no other principal can read regardless.

## Components

| Path | Component |
|---|---|
| `sql/01_create_data.sql` | `demos.genie_rls` shared tables (3 firms) + `firms` lookup |
| `sql/03_rls_scalable.sql` | `entitlements` table (the principal → firm mapping) |
| `sql/04_abac_policy.sql` / `scripts/apply_abac.py` | **ABAC policy** `firm_rls` + governed tag `rls_firm` + `rls_firm_filter` (admins bypass, default-deny) |
| `scripts/provision_sps.py` | Reconcile job: firms lookup → SPs + OBO tokens + grants + entitlements |
| Genie | ONE shared Space `<GENIE_SPACE_ID>` over the shared tables |
| `agent/agent.py` | ResponsesAgent: trusted tenant_id → firm SP token → shared Space |
| `agent/run_build.py` | Serverless log + register + deploy (secret-backed FIRM_TOKEN env vars) |
| `mock_azure_app/` | Local stand-in for the customer's Azure app (CLI + Streamlit) |
| `scripts/test_isolation.py` | Isolation + prompt-injection + missing-tenant tests |

`sql/02_secure_views.sql` (per-firm views) and per-firm Spaces were the earlier
non-scalable approach and have been superseded/removed.

## Not included (by design)

- **OAuth Custom Identity Claims** — native "pass a claim" path, **AWS-only PrPr**.
- **Entra External ID + OBO** — the cleaner native-identity path (no per-firm secrets);
  same single-Space + `current_user()` row filter, identities flow from the IdP.
