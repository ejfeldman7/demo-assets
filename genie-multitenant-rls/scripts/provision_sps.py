#!/usr/bin/env python3
"""Provision per-firm service principals from the firms lookup table.

Runs as a workspace admin. Idempotent / reconciling — safe to re-run and
to schedule (or trigger on lookup-table updates). For each firm in the source
table it ensures:
  1. a service principal  firm-<firm_id>  exists (created via SDK if missing)
  2. the SP has token-usage permission + a current OBO token (workspace-level)
  3. the token (and app_id) are stored in the secret scope for the agent to use
  4. UC + warehouse grants so the SP can run Genie/SQL
  5. an entitlements row  (application_id -> firm_id)  for the row-filter

This is the SP-based alternative to provisioning Entra identities. Scales to N
firms (N SPs, N entitlements rows, ONE Genie Space, ONE row filter).

Local run:   python3 scripts/provision_sps.py
As a job:    runs with default auth (the job's run-as identity = a workspace admin)
"""
import base64
import os
import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import settings

WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "<WAREHOUSE_ID>")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "<GENIE_SPACE_ID>")  # single shared space
SECRET_SCOPE = "genie_rls"
SOURCE_TABLE = "demos.genie_rls.firms"        # the customer's lookup table
ENTITLEMENTS = "demos.genie_rls.entitlements"
TABLES = ["demos.genie_rls.gl_transactions",
          "demos.genie_rls.clients",
          "demos.genie_rls.invoices"]
TOKEN_LIFETIME = 7 * 24 * 3600  # 7 days; the reconcile run rotates it

def _client():
    prof = os.environ.get("DBX_PROFILE")
    return WorkspaceClient(profile=prof) if prof else WorkspaceClient()

def main():
    w = _client()  # run-as identity (runtime cred in a job): does SP create, grants, SQL, secrets

    # Privileged token-minting must use a NON-runtime credential. We authenticate as the
    # dedicated sp-provisioner SP via its stored machine token (set by setup_provisioner.py).
    # No personal token is ever stored or used.
    prov_token = os.environ.get("PROVISIONER_TOKEN") or base64.b64decode(
        w.secrets.get_secret(scope=SECRET_SCOPE, key="provisioner_token").value).decode()
    admin_w = WorkspaceClient(host=w.config.host, token=prov_token)

    def sql(stmt):
        w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID, statement=stmt, wait_timeout="30s")

    # 1. secret scope
    if SECRET_SCOPE not in [s.name for s in w.secrets.list_scopes()]:
        w.secrets.create_scope(scope=SECRET_SCOPE)
        print(f"created secret scope {SECRET_SCOPE}")

    # 2. read the source lookup table
    res = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"SELECT firm_id, firm_name FROM {SOURCE_TABLE} ORDER BY firm_id",
        wait_timeout="30s")
    firms = res.result.data_array or []
    print(f"source table has {len(firms)} firms")

    existing = {sp.display_name: sp for sp in w.service_principals.list()}

    for firm_id, firm_name in firms:
        name = f"firm-{firm_id}"
        sp = existing.get(name)
        if sp is None:
            sp = w.service_principals.create(display_name=name, active=True)
            print(f"  created SP {name} -> {sp.application_id}")
        else:
            print(f"  SP {name} exists -> {sp.application_id}")
        app_id = sp.application_id

        # token-usage permission + fresh OBO token (privileged -> via provisioner SP credential)
        admin_w.token_management.update_permissions(access_control_list=[
            settings.TokenAccessControlRequest(
                service_principal_name=app_id,
                permission_level=settings.TokenPermissionLevel.CAN_USE)])
        obo = admin_w.token_management.create_obo_token(
            application_id=app_id, lifetime_seconds=TOKEN_LIFETIME,
            comment=f"genie_rls {firm_id}")
        w.secrets.put_secret(scope=SECRET_SCOPE, key=f"token_{firm_id}", string_value=obo.token_value)
        w.secrets.put_secret(scope=SECRET_SCOPE, key=f"appid_{firm_id}", string_value=app_id)

        # warehouse access (additive)
        try:
            from databricks.sdk.service.sql import (
                WarehouseAccessControlRequest, WarehousePermissionLevel)
            w.warehouses.update_permissions(warehouse_id=WAREHOUSE_ID, access_control_list=[
                WarehouseAccessControlRequest(
                    service_principal_name=app_id,
                    permission_level=WarehousePermissionLevel.CAN_USE)])
        except Exception as e:
            print(f"    warn: warehouse grant: {repr(e)[:120]}")

        # UC grants
        sql(f"GRANT USE CATALOG ON CATALOG demos TO `{app_id}`")
        sql(f"GRANT USE SCHEMA ON SCHEMA demos.genie_rls TO `{app_id}`")
        for t in TABLES:
            sql(f"GRANT SELECT ON TABLE {t} TO `{app_id}`")

        # CAN_RUN on the shared Genie Space (so the agent can call it as this SP)
        try:
            hdr = dict(w.config.authenticate())
            r = requests.patch(
                f"{w.config.host}/api/2.0/permissions/genie/{GENIE_SPACE_ID}",
                headers={**hdr, "Content-Type": "application/json"},
                json={"access_control_list": [
                    {"service_principal_name": app_id, "permission_level": "CAN_RUN"}]},
                timeout=30)
            if r.status_code != 200:
                print(f"    warn: genie grant {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print(f"    warn: genie grant: {repr(e)[:120]}")

        # entitlements upsert (idempotent)
        sql(f"DELETE FROM {ENTITLEMENTS} WHERE firm_id = '{firm_id}'")
        sql(f"INSERT INTO {ENTITLEMENTS} VALUES ('{app_id}', '{firm_id}', \"{firm_name}\")")
        print(f"    provisioned + mapped {firm_id} ({firm_name})")

    print("reconcile complete.")

if __name__ == "__main__":
    main()
