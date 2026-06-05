"""Log + register + deploy the agent on serverless (env declared in the job)."""
import os, sys
HERE = os.environ.get(
    "AGENT_DIR",
    "/Workspace/Users/<WORKSPACE_USERNAME>/genie-multitenant-rls/agent/agent",
)
sys.path.insert(0, HERE)
os.chdir(HERE)

import mlflow
from agent import LLM_ENDPOINT, GUARD_ENDPOINT, FIRMS
from mlflow.models.resources import DatabricksServingEndpoint

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment("/Users/<WORKSPACE_USERNAME>/genie_rls_exp")
MODEL = "demos.genie_rls.tenant_agent"
SECRET_SCOPE = "genie_rls"

resources = [
    DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT),
    DatabricksServingEndpoint(endpoint_name=GUARD_ENDPOINT),
]

with mlflow.start_run(run_name="genie_rls"):
    info = mlflow.pyfunc.log_model(
        name="agent",
        python_model="agent.py",
        resources=resources,
        pip_requirements=["mlflow==3.6.0", "databricks-langchain", "databricks-sdk",
                          "pydantic", "langchain-core"],
        input_example={
            "input": [{"role": "user", "content": "What were total expenses by account this year?"}],
            "custom_inputs": {"tenant_id": "firm_001"},
        },
        registered_model_name=MODEL,
    )
print("MODEL_VERSION=" + str(info.registered_model_version))

# secret-backed env vars: one firm token per tenant (demo scale; production reads
# dynamically from a secret scope / Key Vault).
env_vars = {f"FIRM_TOKEN_{fid.upper()}": f"{{{{secrets/{SECRET_SCOPE}/token_{fid}}}}}" for fid in FIRMS}
env_vars["GENIE_SPACE_ID"] = os.environ.get("GENIE_SPACE_ID", "<GENIE_SPACE_ID>")

from databricks import agents
dep = agents.deploy(
    MODEL, str(info.registered_model_version),
    environment_vars=env_vars,
    tags={"project": "genie_rls", "pattern": "single-space-rls-per-firm-sp"},
)
print("ENDPOINT_NAME=" + dep.endpoint_name)
