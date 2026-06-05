#!/usr/bin/env python3
"""Apply per-firm RLS as an ABAC policy (CREATE POLICY) with a workspace-admins bypass
and explicit default-deny. Idempotent; run as a workspace admin:

    DBX_PROFILE=<DATABRICKS_PROFILE> python3 scripts/apply_abac.py

Steps: explicit-default-deny row-filter function -> governed tag 'rls_firm' + apply to
firm_id columns -> drop any classic row filters -> CREATE POLICY matching the governed
tag, TO account users + the firm service principals (admins bypass via the function).
NOTE: firm SPs are enumerated in TO for the demo; at scale use an account-level group.
"""
import base64, os
from databricks.sdk import WorkspaceClient

WH = os.environ.get("WAREHOUSE_ID", "<WAREHOUSE_ID>")
SCOPE = "genie_rls"
TABLES = ["gl_transactions", "clients", "invoices"]

FUNCTION = """CREATE OR REPLACE FUNCTION demos.genie_rls.rls_firm_filter(p_firm_id STRING)
RETURNS BOOLEAN
RETURN
  CASE
    WHEN is_member('admins') THEN TRUE
    WHEN EXISTS (SELECT 1 FROM demos.genie_rls.entitlements e
                 WHERE e.principal = current_user() AND e.firm_id = p_firm_id) THEN TRUE
    ELSE FALSE
  END"""

def main():
    w = WorkspaceClient(profile=os.environ.get("DBX_PROFILE")) if os.environ.get("DBX_PROFILE") else WorkspaceClient()

    def run(s, tolerate=()):
        r = w.statement_execution.execute_statement(warehouse_id=WH, statement=s, wait_timeout="50s")
        if r.status.state.value != "SUCCEEDED":
            msg = r.status.error.message if r.status.error else r.status.state.value
            if any(t in msg.lower() for t in tolerate):
                print("  skip:", msg[:70]); return []
            raise RuntimeError(f"{msg}\n{s[:160]}")
        return r.result.data_array if r.result and r.result.data_array else []

    run(FUNCTION); print("function (explicit ELSE FALSE) ready")

    run("CREATE GOVERNED TAG rls_firm VALUES ('true')", tolerate=("already exists", "exists"))
    for t in TABLES:
        run(f"ALTER TABLE demos.genie_rls.{t} ALTER COLUMN firm_id SET TAGS ('rls_firm'='true')")
    print("governed tag rls_firm applied to firm_id columns")

    for t in TABLES:
        run(f"ALTER TABLE demos.genie_rls.{t} DROP ROW FILTER", tolerate=("no row filter", "does not have"))

    sps = [s for s in w.service_principals.list() if (s.display_name or "").startswith("firm-")]
    princ = ", ".join(f"`{s.application_id}`" for s in sps)
    run("DROP POLICY firm_rls ON SCHEMA demos.genie_rls",
        tolerate=("not found", "does not exist", "no policy", "cannot be found"))
    run("CREATE POLICY firm_rls ON SCHEMA demos.genie_rls "
        "ROW FILTER demos.genie_rls.rls_firm_filter "
        f"TO `account users`, {princ} "
        "FOR TABLES MATCH COLUMNS has_tag('rls_firm') AS fcol USING COLUMNS (fcol)")
    print(f"ABAC policy firm_rls created (TO account users + {len(sps)} firm SPs)")

    # verify
    print("\n--- verify ---")
    print("admin (you):", run("SELECT current_user(), count(*) FROM demos.genie_rls.gl_transactions"),
          "(expect ALL rows — admin bypass)")
    tok = base64.b64decode(w.secrets.get_secret(scope=SCOPE, key="token_firm_001").value).decode()
    spw = WorkspaceClient(host=w.config.host, token=tok)
    r = spw.statement_execution.execute_statement(
        warehouse_id=WH,
        statement="SELECT count(*), count(DISTINCT firm_id) FROM demos.genie_rls.gl_transactions",
        wait_timeout="50s")
    print("firm_001 SP:", r.result.data_array, "(expect its firm only)")

if __name__ == "__main__":
    main()
