"""Mock 'the customer's Azure app' — a NON-Databricks client that calls the deployed
agent over REST, passing a TRUSTED tenant_id (as the real Azure app would after
authenticating its end user). This file deliberately uses plain `requests` to
show the server-to-server call shape; no Databricks SDK assumptions.

Auth for the demo: a bearer token from env DATABRICKS_TOKEN, or fetched from the
`<DATABRICKS_PROFILE>` CLI profile if available. In production the Azure app would use its
own M2M OAuth client credentials for the workspace.
"""
import json
import os
import subprocess
import requests

HOST = os.environ.get("DATABRICKS_HOST", "https://<WORKSPACE_HOST>").rstrip("/")
ENDPOINT = os.environ.get("AGENT_ENDPOINT", "agents_demos-genie_rls-tenant_agent")
PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")


def _token() -> str:
    tok = os.environ.get("DATABRICKS_TOKEN")
    if tok:
        return tok
    # demo convenience: borrow an OAuth token from the CLI profile
    out = subprocess.run(
        ["databricks", "auth", "token", "--profile", PROFILE],
        capture_output=True, text=True,
    )
    if out.returncode == 0:
        return json.loads(out.stdout)["access_token"]
    raise RuntimeError("Set DATABRICKS_TOKEN, or DATABRICKS_PROFILE to a configured CLI profile.")


def ask_agent(tenant_id: str, question: str) -> dict:
    """Call the agent. tenant_id is the TRUSTED value the app supplies — it is sent
    in custom_inputs, separate from the user's natural-language question."""
    url = f"{HOST}/serving-endpoints/{ENDPOINT}/invocations"
    payload = {
        "input": [{"role": "user", "content": question}],
        "custom_inputs": {"tenant_id": tenant_id},
    }
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        json=payload, timeout=120,
    )
    r.raise_for_status()
    return r.json()


def extract_text(resp: dict) -> str:
    parts = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
        elif item.get("content"):
            parts.append(str(item["content"]))
    return "\n".join(parts).strip()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Mock the customer's Azure app -> agent")
    ap.add_argument("--tenant", required=True, help="firm_001 | firm_002 | firm_003")
    ap.add_argument("question", help="natural-language question")
    args = ap.parse_args()
    resp = ask_agent(args.tenant, args.question)
    print("\n=== ANSWER ===")
    print(extract_text(resp))
    print("\n=== custom_outputs (transparency) ===")
    print(json.dumps(resp.get("custom_outputs", {}), indent=2))
