ABAC Helper App
==============

Overview
--------
This repository contains a Databricks Lakehouse App (Streamlit) for managing:
- Group-to-customer access rules used for row-level access control
- Unity Catalog governed tags on tables and columns
- Audit reporting for access and tag changes
- RLS/ABAC tooling for creating policies, functions, and propagating tags

The app is deployed with Databricks Asset Bundles and runs as a Databricks App. It
uses Databricks SQL to read and mutate Unity Catalog metadata and the access/audit
tables.

App Pages
---------
- Group Access Management
  - Create, edit, expire, and delete access rules in `group_customer_access`
  - Supports INCLUDE/EXCLUDE semantics and effective/expiration dates
- Tag Management
  - Browse catalogs/schemas/tables, view tags, and apply/remove governed tags
  - Apply column tags using a dropdown of table columns
- Audit & Reports
  - Change history from `access_audit_log`
  - Access matrix for current rules and rules expired in the last 60 days
  - Tag coverage for `secure_contracts=true` with a dropdown to select other tags
- RLS & ABAC Tools
  - Create access-filter UDFs
  - Create tag-based row filter policies
  - Propagate tag values to columns based on parent tags

Configuration
-------------
Runtime configuration is set via `app/app.yaml`:
- `DATABRICKS_SERVER_HOSTNAME`
- `DATABRICKS_HTTP_PATH` / `DATABRICKS_WAREHOUSE_ID`
- `CATALOG_NAME`
- `SCHEMA_NAME`
- `ACCESS_TABLE`
- `AUDIT_TABLE`
- `ADMIN_GROUP`

Defaults are defined in `app/config/settings.py`.

Data Model
----------
The app expects these tables in the configured catalog/schema:

`group_customer_access`:
- `group_name` STRING
- `customer_ids` ARRAY<INT> (NULL or empty means all customers)
- `access_type` STRING (INCLUDE | EXCLUDE)
- `effective_date` DATE
- `expiration_date` DATE
- `notes` STRING
- `created_by`, `created_at`, `modified_by`, `modified_at`

`access_audit_log`:
- `timestamp` TIMESTAMP
- `user` STRING
- `action_type` STRING
- `object_type` STRING
- `object_name` STRING
- `old_value`, `new_value`, `notes`

On startup the app attempts to provision these tables if missing
(`app/utils/setup_utils.py`).

Authentication and Authorization
--------------------------------
The app is intended for Databricks Apps and uses the Databricks SDK / OAuth
credentials provider to connect to the SQL Warehouse. Admin access is enforced
via:

`SELECT is_member('<ADMIN_GROUP>')`

Only members of `ADMIN_GROUP` can use the app.

Required Permissions
--------------------
The app runs as the app service principal. Ensure:

1) Service principal is a member of `access_admin` (or your configured group)
2) The admin group exists in your workspace
3) SQL Warehouse permissions:
   - `CAN_USE` on the configured SQL Warehouse
4) Unity Catalog permissions (minimum):
   - `USE CATALOG` on the target catalog
   - `USE SCHEMA` on the target schema
   - `SELECT` on:
     - `<catalog>.<schema>.group_customer_access`
     - `<catalog>.<schema>.access_audit_log`
     - `system.information_schema.tables`
     - `system.information_schema.columns`
     - `system.information_schema.table_tags`
     - `system.information_schema.column_tags`
     - `system.information_schema.schema_tags`
     - `system.information_schema.catalog_tags`
   - `INSERT`, `UPDATE`, `DELETE` on `<catalog>.<schema>.group_customer_access`
   - `INSERT` on `<catalog>.<schema>.access_audit_log`
5) Tag management:
   - `MODIFY` on any tables/columns where the app will apply/remove tags
6) RLS/ABAC tools:
   - `CREATE FUNCTION` on the schema where functions are created
   - `CREATE POLICY` on the schema where policies are created

If your workspace has stricter requirements, you may also need `OWNERSHIP`
or `MANAGE` privileges on the target schema/tables to create policies and
apply governed tags.

Deploying with Databricks Asset Bundles
---------------------------------------
From the repo root:

1) Validate and deploy the bundle
   - `databricks bundle validate -t dev --profile ef-temp-demo`
   - `databricks bundle deploy -t dev --profile ef-temp-demo`

2) Deploy the app from bundle source
   - `databricks apps deploy abac-helper --source-code-path "/Workspace/Users/<you>@databricks.com/.bundle/abac_helper_app/dev/files/app" --profile ef-temp-demo`

Notes
-----
- The app uses governed tags such as `secure_contracts=true` to drive ABAC
  policies and tag propagation.
- The access matrix and tag coverage views are powered by Unity Catalog
  information schema tables.
