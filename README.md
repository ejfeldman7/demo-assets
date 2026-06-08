# demo-assets

A collection of Databricks demo applications and reference implementations, organized as self-contained directories.

## Contents

### Apps & UI Demos

| Directory | Description |
|-----------|-------------|
| [`abac_helper`](./abac_helper) | Streamlit app providing a UI for managing row-level security rules, propagating Unity Catalog tags, and auditing RLS/ABAC policies |
| [`chunking_playground`](./chunking_playground) | Interactive Streamlit app for iterating on RAG chunking strategies — compare strategies, chunk sizes, and overlap visually |
| [`simple_mas_chat_app`](./simple_mas_chat_app) | Minimal Streamlit chat app wired to a Databricks Model Serving (MAS) endpoint, ~100 lines with OAuth auth |
| [`uc_metadata_auditor`](./uc_metadata_auditor) | Flask app for AI-powered Unity Catalog metadata generation, PII detection, and governance workflows |
| [`lakebase_image_serving`](./lakebase_image_serving) | Streamlit app that browses AI image predictions from Unity Catalog volumes, using Lakebase for fast metadata filtering |

### Pipelines & Frameworks

| Directory | Description |
|-----------|-------------|
| [`databricks_metric_views`](./databricks_metric_views) | CI/CD pipeline for deploying metric views to Unity Catalog via DABs, with Jinja2 templating and automated tests |

### Full Compound-AI Demos

| Directory | Description |
|-----------|-------------|
| [`brand-intel-demo`](./brand-intel-demo) | Compound AI system for brand manager forecasting — natural language queries routed through Genie Spaces, PDF reports, and a proactive anomaly-detection agent |
| [`genie-multitenant-rls`](./genie-multitenant-rls) | Reference implementation for per-tenant row-level security on a shared Genie Space using ABAC policies and per-tenant service principals (Azure) |

## Deployment

Each directory includes its own `README.md` with setup and deployment instructions. Most apps use Databricks Asset Bundles (`databricks.yml`) for deployment:

```bash
cd <directory>
databricks bundle deploy
databricks bundle run
```

Set `DATABRICKS_BUNDLE_ENGINE=direct` if you hit a Terraform PGP signature error.
