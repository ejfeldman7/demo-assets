# Running a Multi-Tenant Genie Space with One Service Principal (OAuth Custom Identity Claims)

**Status:** Empirically validated on Azure (2026-07-06) against the Genie Conversation API directly. Not an officially documented or supported mechanism for Genie specifically — treat as "works today, technically proven, at-your-own-risk" until a formal feature request materializes. See caveats at the end before committing to this in production.

## The problem this solves

You want a single Genie space (optionally wrapped in a supervisor agent) to serve thousands of external end users, where each user must only see their own org/tenant/site's data — without provisioning a Databricks service principal per user or per tenant. Unity Catalog row-level security normally keys off the identity actually executing the query (`current_user()`), which doesn't help when every request runs as the same shared SP.

**OAuth Custom Identity Claims** solves this by letting a single SP mint a different, short-lived OAuth token per request, each carrying an opaque `custom_claim` value. That value is passed through to the SQL Warehouse and readable in SQL via `current_oauth_custom_identity_claim()` — which a Unity Catalog row filter function can use exactly like a bind parameter.

## Architecture

```
External end user
      │  (however your app authenticates them — SSO, your own IDP, etc.)
      ▼
Your backend / supervisor agent's host service
      │  1. Resolves the user to a claim value (org/tenant/site id,
      │     or an opaque external id you'll look up downstream)
      │  2. Mints a fresh OAuth token from the ONE shared service
      │     principal, embedding that value as custom_claim
      ▼
Genie Conversation API  (called directly, with that token as bearer auth)
      │  Genie generates and executes SQL against the SQL Warehouse
      ▼
SQL Warehouse → Unity Catalog row filter reads
current_oauth_custom_identity_claim() → filters the query
      ▼
Filtered results flow back through Genie → your backend → the end user
```

One SP total, regardless of how many end users or tenants you have. The SP never changes; only the claim value in each minted token changes.

## Step-by-step setup

### 1. Create one account-level service principal

Custom Identity Claims requires an **account-level** SP (not a workspace-local one) with a generated OAuth client secret.

- [Service principal OAuth M2M authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m) — general SP + OAuth secret setup (public docs; does not cover the custom-claims parameter specifically, which is not yet publicly documented)

Grant this one SP whatever it needs on the underlying resources: `USE CATALOG` / `USE SCHEMA` / `SELECT` / `EXECUTE` on the relevant catalog and schema, and `CAN_USE` on the SQL Warehouse the Genie space will use.

### 2. Design the row filter function around the claim

If you already have a row-filter pattern that takes an external value and looks up org/tenant/site via an HTTP call (e.g. the pattern used for [AI/BI dashboard external embedding](https://docs.databricks.com/aws/en/dashboards/share/embedding/external-embed#-securely-present-dashboards-to-individual-users)), reuse it almost unchanged — just swap the input source:

```sql
-- Before (dashboard external-embed pattern):
-- uses :aibi_external_value as the input to the HTTP lookup

-- After (Genie / Custom Identity Claims pattern):
CREATE OR REPLACE FUNCTION gold_schema.claim_entitlement_filter(org STRING, tenant STRING, site STRING)
  RETURN EXISTS (
    SELECT 1 FROM TABLE(customer_http_entitlements(current_oauth_custom_identity_claim())) e
    WHERE e.org = org AND e.tenant = tenant AND e.site = site
  );

ALTER TABLE gold.some_table
  SET ROW FILTER gold_schema.claim_entitlement_filter ON (org, tenant, site);
```

For a simpler single-value case (one claim maps directly to one filterable column), it's just:

```sql
CREATE OR REPLACE FUNCTION catalog.schema.filter_by_claim(tenant_id STRING)
  RETURN IF(tenant_id = current_oauth_custom_identity_claim(), true, false);

ALTER TABLE catalog.schema.some_table
  SET ROW FILTER catalog.schema.filter_by_claim ON (tenant_id);
```

Row filters can call other Unity Catalog functions, including ones that issue outbound HTTP requests:

- [Row filters and column masks](https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/) — public UC row-filter mechanics
- [`http_request` SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/http_request) — note documented rate limits; designed for interactive/agent-style use, not high-volume batch

**Important:** `current_oauth_custom_identity_claim()` appears to be evaluated eagerly (likely constant-folded) even at `CREATE FUNCTION` time, not only when the function is later queried. This means the statement that *creates* the function must also run under a token that carries a valid claim — a personal access token or any claim-less token will fail immediately with `OAUTH_CUSTOM_IDENTITY_CLAIM_NOT_PROVIDED`, even for pure DDL.

### 3. Build the Genie space over the protected tables/views

Point the Genie space at the row-filtered table(s) as you normally would — no special Genie-side configuration is needed for the claim to take effect, since enforcement happens in Unity Catalog, not in Genie itself.

- [Genie Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api) — space/conversation/message structure

### 4. Grant the SP access to the Genie space itself

This is a separate permission from the Unity Catalog grants in step 1 — it's the Genie space's own ACL:

```bash
databricks api put "/api/2.0/permissions/genie/<space_id>" --json '{
  "access_control_list": [
    {"service_principal_name": "<sp-client-id>", "permission_level": "CAN_RUN"}
  ]
}'
```

### 5. Per-request flow: mint a token, call Genie directly

For each end-user request, your backend mints a fresh token from the one SP, with that request's claim value:

```bash
curl --request POST \
  --url "https://accounts.azuredatabricks.net/oidc/accounts/<ACCOUNT_ID>/v1/token" \
  --header "authorization: Basic $(echo -n CLIENT_ID:CLIENT_SECRET | base64)" \
  --header 'content-type: application/x-www-form-urlencoded' \
  --data 'grant_type=client_credentials&scope=all-apis&custom_claim=<per-request-value>'
```

Then call the Genie Conversation API with that token as bearer auth:

```bash
curl --request POST \
  --url "https://<workspace-host>/api/2.0/genie/spaces/<space_id>/start-conversation" \
  --header "Authorization: Bearer <token>" \
  --header "content-type: application/json" \
  --data '{"content": "<user question>"}'
```

Poll for completion and read the result:

```
GET /api/2.0/genie/spaces/<space_id>/conversations/<conversation_id>/messages/<message_id>
```

The message's `status` moves through `SUBMITTED` → `ASKING_AI` → `COMPLETED` (or `FAILED`). Once `COMPLETED`, the `attachments` array contains the generated SQL (`query.query`), the row count actually returned (`query.query_result_metadata.row_count`), and Genie's natural-language answer — all of which reflect the row filter, not just the raw table contents.

### 6. Note on the account-level token endpoint host

The host differs by cloud:

| Cloud | Account-level OIDC token endpoint |
|---|---|
| AWS | `https://accounts.cloud.databricks.com/oidc/accounts/<account_id>/v1/token` |
| Azure | `https://accounts.azuredatabricks.net/oidc/accounts/<account_id>/v1/token` |
| GCP | Not tested as part of this work — verify before relying on it |

## If wrapping Genie in a supervisor agent

**Do not use the Mosaic AI Agent Framework's built-in on-behalf-of-user (OBO) Genie tool for this.** That framework's OBO downscoping flow strips the custom claim before it reaches Genie, reproducibly throwing `OAUTH_CUSTOM_IDENTITY_CLAIM_NOT_PROVIDED`. OBO and Custom Identity Claims solve different problems that sound similar: OBO authenticates as the *end user's own real Databricks identity* (which requires the external user to already be a Databricks principal — the exact thing this whole approach is trying to avoid), while Custom Identity Claims authenticates as the one shared SP and carries the user context in the token instead.

- [User authorization (on-behalf-of) for agents](https://docs.databricks.com/aws/en/generative-ai/agent-framework/authenticate-on-behalf-of-user) — public docs confirming OBO is Public Preview and lists Genie Space as a supported resource type; does not mention custom claims, consistent with these being separate mechanisms

**Workaround:** have the supervisor's host service mint the custom-claim token itself and call the Genie Conversation API directly as a plain REST call (steps 5 above), then feed the result back into the supervisor's own orchestration — rather than relying on the framework's native Genie tool wrapper.

## Caveats before using this in production

- **Not officially documented or supported for Genie specifically.** It works because Genie's execution rides on the same SQL Warehouse plumbing that Custom Identity Claims is documented for (JDBC + SQL Warehouse, JDBC + interactive cluster, SQL Statement Execution API) — but Genie itself is not on that supported-surfaces list. This is empirically proven, not a committed contract.
- **Custom Identity Claims is Beta / allowlist-gated**, with no committed GA date as of this writing.
- **The Genie UI (chat interface, sample-data preview) is not a supported surface and will error** — this only works when the Genie Conversation API is called directly by your own backend, not through the native chat UI.
- **The supervisor-wrapping workaround above has not been independently tested as a combined pattern** — direct Genie API calls with claims are confirmed working, and the framework's OBO tool is confirmed broken, but "host service calls Genie API directly, feeds result into a supervisor" specifically has not been tested end-to-end by us.
- The written first-party guide for Custom Identity Claims states the Databricks account must be AWS-only — **this is empirically incorrect**; it was directly tested and confirmed working on Azure (same mechanism: one SP, `custom_claim` varying per token, row filter enforced correctly, verified both at the raw SQL Warehouse level and through the Genie Conversation API itself). GCP has not been tested.

## References

- [AI/BI dashboard external embedding — securely present dashboards to individual users](https://docs.databricks.com/aws/en/dashboards/share/embedding/external-embed#-securely-present-dashboards-to-individual-users)
- [Row filters and column masks](https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/)
- [Genie Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api)
- [User authorization (on-behalf-of) for agents](https://docs.databricks.com/aws/en/generative-ai/agent-framework/authenticate-on-behalf-of-user)
- [Service principal OAuth M2M authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m)
- [`http_request` SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/http_request)

No public documentation exists yet for `current_oauth_custom_identity_claim()` or the `custom_claim` OAuth token parameter — everything above describing that specific mechanism is based on direct empirical testing (2026-07-06, Azure workspace `field-eng-east`) plus an internal first-party user guide, not a public Databricks reference.
