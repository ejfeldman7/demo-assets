#!/usr/bin/env python3
"""ONE-TIME setup (run by a workspace admin interactively): create the dedicated
`sp-provisioner` service principal, make it a workspace admin, mint ITS OBO token,
and store that machine credential in the secret scope. The scheduled provisioning
job then authenticates as this SP (non-runtime credential) to mint per-firm tokens
— so no personal token is ever stored.

Rotate by re-running this (re-mints the provisioner token). Run as an admin:
    DBX_PROFILE=<DATABRICKS_PROFILE> python3 scripts/setup_provisioner.py
"""
import base64, os
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import settings, iam

SECRET_SCOPE = "genie_rls"
PROVISIONER_NAME = "sp-provisioner"
PROVISIONER_TOKEN_LIFETIME = 90 * 24 * 3600  # 90 days; re-run to rotate

def main():
    prof = os.environ.get("DBX_PROFILE")
    w = WorkspaceClient(profile=prof) if prof else WorkspaceClient()
    host = w.config.host

    # 1. create (or find) the provisioner SP
    sp = next((s for s in w.service_principals.list()
               if s.display_name == PROVISIONER_NAME), None)
    if sp is None:
        sp = w.service_principals.create(display_name=PROVISIONER_NAME, active=True)
        print(f"created {PROVISIONER_NAME} -> {sp.application_id}")
    else:
        print(f"{PROVISIONER_NAME} exists -> {sp.application_id}")

    # 2. make it a workspace admin (required to mint OBO tokens for other SPs)
    admins = next((g for g in w.groups.list(filter='displayName eq "admins"')), None)
    if admins:
        members = {m.value for m in (admins.members or [])}
        if sp.id not in members:
            w.groups.patch(
                id=admins.id,
                schemas=[iam.PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
                operations=[iam.Patch(op=iam.PatchOp.ADD, path="members",
                                      value=[{"value": sp.id}])])
            print("added provisioner to workspace admins")
        else:
            print("provisioner already a workspace admin")
    else:
        print("WARN: could not find 'admins' group")

    # 3. token-use permission + mint the provisioner's own OBO token
    w.token_management.update_permissions(access_control_list=[
        settings.TokenAccessControlRequest(
            service_principal_name=sp.application_id,
            permission_level=settings.TokenPermissionLevel.CAN_USE)])
    obo = w.token_management.create_obo_token(
        application_id=sp.application_id, lifetime_seconds=PROVISIONER_TOKEN_LIFETIME,
        comment="sp-provisioner machine credential")

    # 4. store the machine credential in the secret scope (admins-only)
    if SECRET_SCOPE not in [s.name for s in w.secrets.list_scopes()]:
        w.secrets.create_scope(scope=SECRET_SCOPE)
    w.secrets.put_secret(scope=SECRET_SCOPE, key="provisioner_token", string_value=obo.token_value)
    w.secrets.put_secret(scope=SECRET_SCOPE, key="provisioner_appid", string_value=sp.application_id)
    print("stored provisioner_token in secret scope")

    # 5. verify: as the provisioner SP, mint an OBO token for an existing firm SP
    firm_sp = next((s for s in w.service_principals.list()
                    if (s.display_name or "").startswith("firm-")), None)
    if firm_sp:
        prov_w = WorkspaceClient(host=host, token=obo.token_value)
        try:
            t = prov_w.token_management.create_obo_token(
                application_id=firm_sp.application_id, lifetime_seconds=3600,
                comment="verify provisioner can mint")
            print(f"VERIFmasked: provisioner minted a firm token OK (len={len(t.token_value)})")
        except Exception as e:
            print(f"VERIFY FAILED: provisioner cannot mint: {repr(e)[:200]}")
    print("provisioner setup complete.")

if __name__ == "__main__":
    main()
