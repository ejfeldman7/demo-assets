# Running a multi-tenant Genie space with one service principal (OAuth Custom Identity Claims)

**Status:** Validated end to end on Azure, most recently 2026-08-06. See the [validation record](#validation-record) for exactly what was tested. This is not an officially documented or supported mechanism for Genie, so treat it as "works today, proven by testing, at your own risk" until the feature request lands. Read the [caveats](#caveats-before-using-this-in-production) before committing to it in production.

**Hitting `OAUTH_CUSTOM_IDENTITY_CLAIM_NOT_PROVIDED`?** Jump to [Two behaviors that will surprise you](#two-behaviors-that-will-surprise-you). That error is expected behavior rather than a misconfiguration, and the fix is specific.

## The problem this solves

You want one Genie space to serve thousands of external end users, where each user sees only their own org, tenant, or site data, without creating a Databricks service principal per user or per tenant.

Unity Catalog row-level security normally keys off the identity running the query via `current_user()`. That does not help when every request runs as the same shared service principal, because `current_user()` returns the SP every time.

OAuth Custom Identity Claims fixes this. A single SP mints a short-lived token per request, each carrying an opaque `custom_claim` value. The claim rides through to the SQL warehouse and is readable in SQL through `current_oauth_custom_identity_claim()`, which a view or row filter can use like a bind parameter.

## How it works

```
External end user
      │  (however your app authenticates them: SSO, your own IDP, etc.)
      ▼
Your backend
      │  1. Resolves the user to a claim value (org/tenant/site id, or an
      │     opaque external id you look up downstream)
      │  2. Mints a fresh OAuth token from the ONE shared service principal,
      │     embedding that value as custom_claim
      ▼
Genie Conversation API  (called directly, with that token as bearer auth)
      │  Genie generates and executes SQL against the SQL warehouse
      ▼
SQL warehouse → Unity Catalog reads current_oauth_custom_identity_claim()
      │          and filters to that claim's rows
      ▼
Filtered results flow back through Genie to your backend and the end user
```

One SP total, no matter how many tenants you have. The SP never changes. Only the claim value inside each minted token changes.

## The object model: unfiltered tables, claim-enforced views

Get this part right before you write any SQL, because retrofitting it onto a production table is painful.

You have two audiences for the same data. Internal users, notebooks, jobs, and pipelines need normal access. External end users, arriving through Genie, need to be restricted to their own tenant. Serve them with two objects over one copy of the data:

| Object | Claim? | Who reads it |
|---|---|---|
| Base table | No row filter | Internal users, notebooks, jobs, DLT pipelines, dashboards |
| View over that table | `WHERE tenant_id = current_oauth_custom_identity_claim()` | The runtime SP only, through Genie |

Three rules make this a security boundary instead of a decoration. All three are required:

1. **Leave the base table unfiltered.** Do not attach a claim-based row filter to it. A row filter there locks out every caller that has no claim, which includes you, the table owner, every account admin, and every batch job. See [no admin bypass exists](#no-admin-bypass-exists).
2. **The view owner must not be the SP that Genie runs as.** Unity Catalog resolves a view's underlying tables using the *view owner's* privileges. If the SP owns the view, its own privileges reach the base table and the view restricts nothing. Own the views with a separate publisher identity: your own user for a proof of concept, a dedicated publisher SP in production.
3. **Grant the runtime SP `SELECT` on the view only.** Watch for inherited grants. A schema-level `SELECT` silently reaches every table in the schema, which is the most common way this boundary fails in practice.

```sql
-- Publisher identity (owns the view, can read the base table)
CREATE OR REPLACE VIEW main.gold.orders_tenant_scoped AS
  SELECT * FROM main.gold.orders
  WHERE tenant_id = current_oauth_custom_identity_claim();

ALTER VIEW main.gold.orders_tenant_scoped OWNER TO `publisher-sp-or-your-user`;

-- Runtime SP: view only, and make sure nothing inherits table access
REVOKE SELECT ON SCHEMA main.gold FROM `<runtime-sp-client-id>`;
GRANT SELECT ON VIEW main.gold.orders_tenant_scoped TO `<runtime-sp-client-id>`;
```

Two things make this work, both confirmed by testing. The claim is evaluated against the *querying* session's token, not the view owner's identity, so a view owned by a claim-less publisher still filters correctly by the runtime SP's claim. And once the runtime SP has no grant on the base table, Genie cannot read that table even if someone points the space at it by mistake: Unity Catalog refuses the query. The boundary does not depend on the Genie space staying configured correctly.

The tradeoff compared to a row filter on the base table: enforcement now rests on grants rather than being unconditional at the table. A row filter cannot be bypassed by querying somewhere else; this pattern can, if you grant the runtime SP more than the view. For a multi-tenant Genie deployment that trade is almost always worth it, because the alternative makes the table unusable for everyone else.

## Before you start

- An **account-level** service principal, not a workspace-local one, with a generated OAuth client secret. This is the runtime SP that Genie will use.
- Custom Identity Claims enabled for your account. It is Beta and allowlist-gated, and enablement is also per-workspace. See the [caveats](#caveats-before-using-this-in-production).
- A way to run SQL under an OAuth token: the SQL Statement Execution API or JDBC. You cannot complete the setup from a notebook or the SQL editor.

Reference: [Service principal OAuth M2M authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m). It covers SP and secret setup but not the `custom_claim` parameter, which is not yet publicly documented.

## Implementation

### 1. Grant the runtime SP its baseline access

The SP needs `USE CATALOG` on the catalog and `USE SCHEMA` on the schema, plus `CAN_USE` on the SQL warehouse the Genie space will use.

Do not grant it `SELECT` at the catalog or schema level. That inherits down to your base tables and breaks rule 3 above. Grant `SELECT` on views individually, in step 3.

### 2. Mint a bootstrap token

Any statement that references `current_oauth_custom_identity_claim()` needs a claim present, including the DDL that creates your views. The value is arbitrary for setup, so use a throwaway:

```bash
TOKEN=$(curl -s --request POST \
  --url "https://accounts.azuredatabricks.net/oidc/accounts/<ACCOUNT_ID>/v1/token" \
  --header "authorization: Basic $(echo -n CLIENT_ID:CLIENT_SECRET | base64)" \
  --header 'content-type: application/x-www-form-urlencoded' \
  --data 'grant_type=client_credentials&scope=all-apis&custom_claim=ddl-bootstrap' \
  | jq -r .access_token)
```

The claim is execution context, not part of the object definition. A view created under `custom_claim=ddl-bootstrap` is byte-identical to one created under any other value.

To confirm the claim is embedded, decode the JWT payload. It appears nested rather than at the top level:

```json
"custom": { "claim": "ddl-bootstrap" }
```

### 3. Create the claim-enforced view

Run this through the Statement Execution API with the bootstrap token:

```bash
curl -s --request POST \
  --url "https://<workspace-host>/api/2.0/sql/statements" \
  --header "Authorization: Bearer $TOKEN" \
  --header 'content-type: application/json' \
  --data '{
    "warehouse_id": "<warehouse_id>",
    "statement": "CREATE OR REPLACE VIEW main.gold.orders_tenant_scoped AS SELECT * FROM main.gold.orders WHERE tenant_id = current_oauth_custom_identity_claim()"
  }'
```

Then transfer ownership away from the runtime SP and grant it read access to the view alone, per the [three rules](#the-object-model-unfiltered-tables-claim-enforced-views). `ALTER VIEW ... OWNER TO`, `REVOKE`, and `GRANT` do not reference the claim function, so you can run those with your normal credentials.

If your existing entitlement logic lives in an HTTP lookup, as it does in the [AI/BI dashboard external embedding](https://docs.databricks.com/aws/en/dashboards/share/embedding/external-embed#-securely-present-dashboards-to-individual-users) pattern, reuse it and swap only the input source. Replace `:aibi_external_value` with `current_oauth_custom_identity_claim()`:

```sql
CREATE OR REPLACE VIEW main.gold.orders_tenant_scoped AS
  SELECT o.* FROM main.gold.orders o
  WHERE EXISTS (
    SELECT 1 FROM TABLE(customer_http_entitlements(current_oauth_custom_identity_claim())) e
    WHERE e.org = o.org AND e.tenant = o.tenant AND e.site = o.site
  );
```

Views can call other Unity Catalog functions, including ones that make outbound HTTP calls. Note the [`http_request`](https://docs.databricks.com/aws/en/sql/language-manual/functions/http_request) rate limits: it is built for interactive and agent traffic, not high-volume batch. Genie may issue several queries per conversation turn, so load-test this before committing to it at scale.

### 4. Verify the boundary before wiring up Genie

Four checks, and all four should pass:

```sql
-- 1. Two different claims see different rows (run each under its own token)
SELECT * FROM main.gold.orders_tenant_scoped;

-- 2. The runtime SP cannot reach the base table.
--    Expect INSUFFICIENT_PERMISSIONS / 42501.
SELECT * FROM main.gold.orders;

-- 3. You, with no claim, read the base table normally
SELECT * FROM main.gold.orders;

-- 4. A claim-less caller gets 22KD2 on the view, not an empty result
SELECT * FROM main.gold.orders_tenant_scoped;
```

Check 2 is the one people skip, and it is the one that catches an inherited schema-level grant.

### 5. Point the Genie space at the view

Build the space over the tenant-scoped views, not the base tables. No Genie-side configuration is needed for the claim to work, because enforcement happens in Unity Catalog.

Confirm what the space actually references, since a stale table identifier is easy to miss:

```bash
databricks api get "/api/2.0/data-rooms/<space_id>" | jq .table_identifiers
```

Reference: [Genie Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api).

### 6. Grant the SP access to the space

Separate from the Unity Catalog grants. This is the space's own ACL:

```bash
databricks api put "/api/2.0/permissions/genie/<space_id>" --json '{
  "access_control_list": [
    {"service_principal_name": "<runtime-sp-client-id>", "permission_level": "CAN_RUN"}
  ]
}'
```

### 7. Wire up the per-request flow

For each end-user request, your backend mints a fresh token carrying that user's claim, then calls Genie with it:

```bash
# Mint per-request (same call as step 2, real claim value this time)
curl --request POST \
  --url "https://accounts.azuredatabricks.net/oidc/accounts/<ACCOUNT_ID>/v1/token" \
  --header "authorization: Basic $(echo -n CLIENT_ID:CLIENT_SECRET | base64)" \
  --header 'content-type: application/x-www-form-urlencoded' \
  --data 'grant_type=client_credentials&scope=all-apis&custom_claim=<per-request-value>'

# Start a conversation
curl --request POST \
  --url "https://<workspace-host>/api/2.0/genie/spaces/<space_id>/start-conversation" \
  --header "Authorization: Bearer <token>" \
  --header "content-type: application/json" \
  --data '{"content": "<user question>"}'
```

Poll until the message finishes:

```
GET /api/2.0/genie/spaces/<space_id>/conversations/<conversation_id>/messages/<message_id>
```

`status` moves through `SUBMITTED`, `ASKING_AI`, `PENDING_WAREHOUSE`, then `COMPLETED` or `FAILED`. On `COMPLETED`, the `attachments` array holds the generated SQL in `query.query`, the row count in `query.query_result_metadata.row_count`, and Genie's natural-language answer. All of it reflects the filtered view rather than the raw table.

The end user never issues SQL. They ask a question; Genie writes the SQL and runs it under the claim token. That indirection is what makes the pattern safe, and it is why prompt injection cannot widen access: the filter is applied by Unity Catalog after Genie has written whatever query it chose.

### Token endpoint host by cloud

| Cloud | Account-level OIDC token endpoint |
|---|---|
| AWS | `https://accounts.cloud.databricks.com/oidc/accounts/<account_id>/v1/token` |
| Azure | `https://accounts.azuredatabricks.net/oidc/accounts/<account_id>/v1/token` |
| GCP | Untested. Verify before relying on it. |

## Two behaviors that will surprise you

`current_oauth_custom_identity_claim()` is resolved at query-analysis time, not at runtime. Internally it is an unevaluable expression that gets inlined during analysis, so it never survives to per-row evaluation. Two consequences follow, and both are intended behavior rather than bugs.

### Setup DDL needs a claim-bearing token

Any statement mentioning the function fails without a claim, including pure DDL:

```
[OAUTH_CUSTOM_IDENTITY_CLAIM_NOT_PROVIDED] No custom identity claim was provided. SQLSTATE: 22KD2
```

This hits `CREATE VIEW` and `CREATE FUNCTION` equally, and it fires from a PAT, the SQL editor, or a notebook cell. Those are all claim-less surfaces by design, so the failure is the specification, not your SQL.

The fix is [step 2](#2-mint-a-bootstrap-token): run the DDL through the Statement Execution API or JDBC under a token carrying any claim value.

Wrappers that look like they should defer evaluation do not work. `coalesce()`, `try_cast()`, and pushing the reference into an `EXISTS` subquery all fail the same way. There is no route to creating these objects from a claim-less session.

`ALTER VIEW`, `GRANT`, `REVOKE`, and `ALTER TABLE ... SET ROW FILTER` do not reference the function and work with ordinary credentials.

### No admin bypass exists

The function cannot participate in runtime short-circuiting, so a privileged-group bypass is impossible today. All of these throw instead of short-circuiting when the caller has no claim, even though the first branch is true:

```sql
-- The RETURN lines are function-body fragments, not standalone statements.
SELECT TRUE OR current_oauth_custom_identity_claim() = 'x';
RETURN is_account_group_member('admins') OR tenant_id = current_oauth_custom_identity_claim();
RETURN CASE WHEN is_account_group_member('admins') THEN true
            ELSE tenant_id = current_oauth_custom_identity_claim() END;
```

This is why rule 1 of the [object model](#the-object-model-unfiltered-tables-claim-enforced-views) says to keep base tables unfiltered. A claim-based row filter on a base table produces a hard error for every claim-less caller rather than an empty result, and no group membership can override it. If you have already attached one and need direct access back, `ALTER TABLE ... DROP ROW FILTER` removes it.

A feature request to fold a missing claim to `NULL`, so `COALESCE` and `OR` logic could handle it, sits with the Auth Platform team. It is not committed.

### Can I give my own user a default claim instead?

No, and the restriction is doing you a favor.

Claims are minted through the `client_credentials` service principal flow. User identities authenticate by a different path with no `custom_claim` parameter, there is no user-level default claim setting, and there is no session override: `SET custom_claim = ...` returns `CONFIG_NOT_AVAILABLE`. A value the caller could set in their own session would not be a security boundary.

Even if it existed, a standing claim on your own identity would pin every query you run, in every notebook and dashboard, to one tenant's slice of the data, and the results would look normal instead of raising an error. A loud failure beats a silent wrong answer.

To inspect data as yourself, read the unfiltered base table. That is what the object model is for.

## If you wrap Genie in a supervisor agent

Do not use the Mosaic AI Agent Framework's built-in on-behalf-of-user Genie tool. Its OBO downscoping flow strips the custom claim before it reaches Genie and throws `OAUTH_CUSTOM_IDENTITY_CLAIM_NOT_PROVIDED` reproducibly.

OBO and Custom Identity Claims solve problems that sound alike but are not. OBO authenticates as the end user's own real Databricks identity, which requires that user to already be a Databricks principal, exactly what this approach exists to avoid. Custom Identity Claims authenticates as one shared SP and carries the user context inside the token.

Reference: [User authorization (on-behalf-of) for agents](https://docs.databricks.com/aws/en/generative-ai/agent-framework/authenticate-on-behalf-of-user). The public docs confirm OBO is Public Preview and list Genie Space as a supported resource type, and say nothing about custom claims, consistent with these being separate mechanisms.

Instead, have the supervisor's host service mint the claim token itself and call the Genie Conversation API as a plain REST call, per [step 7](#7-wire-up-the-per-request-flow), then feed the result back into the supervisor's orchestration.

The same warning applies to any intermediary that re-mints or exchanges credentials on your behalf, including hosted MCP servers. If a layer between your backend and Genie issues its own token, assume the claim is dropped until you have tested otherwise.

## Caveats before using this in production

**Not officially supported for Genie.** It works because Genie's execution rides the same SQL warehouse plumbing that Custom Identity Claims does support (JDBC plus warehouse, JDBC plus interactive cluster, Statement Execution API). Genie is not on that supported-surfaces list. Testing confirms it works on Azure today, but "works when tested" and "supported" are different claims and only the first is established here. A future change to Genie's execution path could break it without notice.

**Enablement is per-workspace and can silently strip the claim.** Beyond account-level preview enrollment, claim propagation depends on a workspace-scoped header allowlist. A token can mint successfully with the claim embedded and still arrive at SQL execution without it, producing the same `22KD2` error a claim-less token produces. If setup fails in a workspace where you believe claims are enabled, rule this out before hunting for a mistake in your SQL. It needs a support ticket, not a code change.

**Beta and allowlist-gated**, with no committed GA date.

**The Genie UI will error**, including the chat interface and the sample-data preview, because browser sessions carry no claim. This pattern only works when your backend calls the Conversation API directly. Expect the sample-data preview to show `22KD2` for any claim-enforced view; that is cosmetic and does not affect API calls.

**Setup DDL requires OAuth client-credentials access to the SP**, so a PAT-based migration runner or IaC pipeline cannot complete it. Check this against your deployment tooling early.

**No break-glass path exists** for a claim-based row filter on a base table. Plan the view-based object model up front.

**The supervisor-wrapping workaround is untested as a combined pattern.** Direct Genie API calls with claims are confirmed working and the framework's OBO tool is confirmed broken, but "host service calls the Genie API directly, then feeds the result into a supervisor" has not been tested end to end.

**The first-party guide's AWS-only prerequisite is wrong.** It states the Databricks account must be AWS. Direct testing on Azure contradicts that. GCP remains untested.

## Validation record

Everything here about the claim mechanism comes from direct testing, not public documentation. What was actually exercised, so you can weigh each claim:

**2026-08-06, Azure workspace `field-eng-east`, catalog `ef_claims_test`.** One account-level SP, two claim values (`market_a` and `market_b`) over a two-row table.

Claim propagation and filtering:

| Tested | Result |
|---|---|
| Token minting with `custom_claim` | Claim embedded in the JWT as nested `"custom": {"claim": "..."}` |
| Statement Execution API, both claims | Each token saw only its own row, and the claim read back correctly |
| Genie Conversation API, both claims, against a row-filtered table | Correctly isolated per claim |
| Genie Conversation API, both claims, against a claim-enforced view | Correctly isolated per claim |
| Genie Conversation API as a claim-less caller | Message reached `FAILED` with `22KD2` |

Analysis-time evaluation:

| Tested | Result |
|---|---|
| `CREATE FUNCTION` and `CREATE VIEW` under a claim-bearing token | Both succeeded, including with the throwaway value `ddl-bootstrap` |
| Same DDL under a claim-less token | Failed with `22KD2` |
| `coalesce()`, `try_cast()`, `EXISTS`-subquery wrappers | All failed identically; none defer evaluation |
| `is_account_group_member('admins') OR <claim predicate>` | Threw rather than short-circuiting, with `EXECUTE` granted |
| `SET custom_claim = ...` from a user session | `CONFIG_NOT_AVAILABLE` |
| `SELECT` on a row-filtered base table as its owner | Failed with `22KD2`, so the lockout is real |

The object model:

| Tested | Result |
|---|---|
| Claim filtering through a view owned by a *different* principal than the caller | Worked. The claim comes from the querying session, not the view owner |
| Runtime SP reading the base table while holding schema-level `SELECT` | Returned all rows, so the view was not a boundary |
| Runtime SP reading the base table after revoking schema `SELECT` | `INSUFFICIENT_PERMISSIONS` / `42501` |
| Genie pointed at the base table with the SP's table access revoked | Query refused, no data returned |
| Internal user reading the unfiltered base table with no claim | All rows, as normal |

Two results are worth dwelling on.

Genie generated unconstrained SQL every time and the filter still held. One caller got a bare `SELECT *`; another got a `COUNT(*)` aggregate with no `WHERE` clause, which returned 1 rather than 2. Enforcement lives in Unity Catalog and does not depend on the model writing safe SQL, which is the property to demonstrate to a security team.

The schema-level grant is the failure everyone should expect to hit. A view over a base table looks like a boundary and behaves like one in casual testing, right up until you check whether the SP can read the table directly. It could, through an inherited schema grant, with no explicit grant on the table itself.

Not covered: GCP on any date, the supervisor-wrapping pattern, hosted MCP as an intermediary, and behavior at realistic tenant counts or query volume.

## References

- [Row filters and column masks](https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/)
- [Genie Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api)
- [AI/BI dashboard external embedding](https://docs.databricks.com/aws/en/dashboards/share/embedding/external-embed#-securely-present-dashboards-to-individual-users)
- [User authorization (on-behalf-of) for agents](https://docs.databricks.com/aws/en/generative-ai/agent-framework/authenticate-on-behalf-of-user)
- [Service principal OAuth M2M authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m)
- [`http_request` SQL function](https://docs.databricks.com/aws/en/sql/language-manual/functions/http_request)

No public documentation exists for `current_oauth_custom_identity_claim()` or the `custom_claim` token parameter. Everything above describing that mechanism rests on the [validation record](#validation-record) plus an internal first-party user guide.
