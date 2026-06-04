# Brand Manager Forecasting Intelligence

A compound AI system built entirely on Databricks that gives brand managers natural language access to demand forecasts, inventory analytics, and automated reporting — with a proactive agent that autonomously discovers anomalies without being asked.

## What It Does

Brand managers ask questions in plain English — *"How are my top 5 SKUs trending this quarter?"* — and the system:

1. **Plans** — An LLM decomposes the question into concrete data queries
2. **Executes** — Routes each query to the right Genie Space, which generates SQL against governed metric views
3. **Synthesizes** — Combines results into an executive report with trends, anomalies, and recommendations
4. **Delivers** — Optionally generates a PDF and emails it on a schedule

A second autonomous agent runs on a schedule to proactively scan for anomalies — forecast accuracy drops, stockout risks, revenue spikes — and alerts stakeholders before they have to ask.

## Architecture

```
Brand Manager → Databricks App (Streamlit)
                    ↓
          ┌─────────────────────┐
          │   Supervisor Agent  │ ← Human-triggered Q&A
          │   Proactive Agent   │ ← Autonomous anomaly detection
          └─────────────────────┘
              ↓           ↓            ↓
     Foundation    Genie Spaces    Lakebase
     Model API     (NL → SQL)     (Memory)
                      ↓
               SQL Warehouse
                      ↓
              Unity Catalog
         (Governed Metric Views)
```

### Two-Agent Design

| Agent | Trigger | Approach | Memory |
|-------|---------|----------|--------|
| **Supervisor Agent** | Human question | Plan → Execute → Synthesize across multiple Genie Spaces | Conversation history (sessions + messages) |
| **Proactive Agent** | Scheduled cron | Phase 1 (deterministic SQL sweep) → Phase 2 (LLM drill-down with function calling) | Watching/resolved lists with staleness enforcement |

### Databricks Services Used

| Service | Role |
|---------|------|
| **Databricks Apps** | Hosts the Streamlit UI and both agents |
| **Genie Spaces** | Natural language → SQL on governed metrics (Demand Forecast + Inventory & Channel) |
| **Foundation Model API** | LLM for planning, synthesis, alert evaluation, and tool calling |
| **Lakeflow Declarative Pipelines** | Bronze/Silver medallion architecture with schema evolution |
| **AI Functions** | `ai_similarity()` for SKU resolution, `ai_forecast()` for demand prediction |
| **Unity Catalog** | Governed tables, metric views, and volumes |
| **Lakebase** | Managed Postgres for schedules, conversation memory, agent memory, and audit logs |
| **Databricks Jobs** | Daily data pipeline + hourly report dispatcher |
| **Secret Scopes** | SMTP and Lakebase credential management |

## Data Pipeline

The pipeline follows a **medallion architecture**:

- **Bronze** — Auto Loader streams raw CSVs into Delta tables with schema evolution
- **Silver** — `ai_similarity()` resolves messy SKU aliases to canonical names; data quality expectations quarantine bad records
- **Gold** — `ai_forecast()` generates per-customer-SKU demand forecasts; results are published as governed Metric Views with business definitions
- **Serving** — Two Genie Spaces (Demand Forecast, Inventory & Channel) consume the metric views

Two automated jobs orchestrate everything:
- **Daily Forecast Pipeline** (5am UTC) — Ingests new data, runs the declarative pipeline, drops stale forecasts, generates fresh predictions, refreshes metric views
- **Report Dispatcher** (hourly) — Evaluates cron schedules in Lakebase, runs due reports/alerts/proactive scans in parallel

## Application Tabs

| Tab | Purpose |
|-----|---------|
| **Home** | Landing page with guided overview of all features |
| **Ask the Genies** | Direct chat with individual Genie Spaces — starter question chips, SQL display, data tables |
| **Supervisor Agent** | Multi-Genie orchestration — complex questions, execution plan visualization, conversation memory, PDF export |
| **Schedules & Alerts** | Create/edit scheduled reports and anomaly alerts, manage Genie Space assignments, run-now capability |
| **Monitoring** | Visual dashboard — run health KPIs, alert breach timelines, proactive agent watching/resolved trends, schedule performance, filtered report library |
| **Data Pipeline** | Interactive Mermaid diagrams of the medallion flow, AI functions, proactive agent phases, and job orchestration |
| **Architecture** | System design diagrams — platform services, two-agent architecture, sequence diagrams for Supervisor and Proactive Agent flows |

## Proactive Anomaly Detection

The proactive agent runs autonomously on a schedule:

**Phase 1 — Deterministic SQL Sweep:**
- `variance_baseline(accuracy_pct)` — Forecast accuracy outliers (sigma threshold)
- `variance_baseline(unit_variance)` — Volume variance outliers
- `forecast_vs_inventory(60d)` — 60-day stockout risk
- Results are deduplicated by SKU (keep highest sigma) and ranked → top 20 candidates

**Phase 2 — LLM Drill-Down:**
- The LLM investigates top candidates using function calling (up to 15 tool calls, 180s timeout)
- 5 analytical SQL tools: `variance_baseline`, `forecast_vs_inventory`, `compare_periods`, `channel_decomposition`, `correlate`
- 7 Genie query templates: `variance_check`, `channel_split`, `stockout_risk`, `category_trend`, `top_movers`, `inventory_position`, `forecast_coverage`
- Output: structured findings with severity, recommended actions, and a memory update

**Persistent Memory:**
- Watching list tracks items across runs (topic, severity, trend, runs_watched)
- Auto-resolves items stable for 5+ runs or watched for 10+ runs
- Prevents duplicate alerts and tracks escalation patterns

## Project Structure

```
├── app/                                  # Databricks App (Streamlit)
│   ├── app.py                            # Main UI — 7 tabs, routing, session state (entry point)
│   ├── app.yaml                          # App configuration (env vars, resources, secrets)
│   ├── requirements.txt                  # Python dependencies
│   ├── src/                              # Utility modules (added to sys.path by app.py)
│   │   ├── supervisor.py                 # Supervisor Agent — plan → execute → synthesize
│   │   ├── proactive_agent.py            # Proactive Agent — Phase 1 sweep + Phase 2 drill-down
│   │   ├── analytical_tools.py           # 5 SQL-based analytical tools for function calling
│   │   ├── genie_templates.py            # 7 constrained Genie query templates with validation
│   │   ├── genie.py                      # Genie Spaces API wrapper
│   │   ├── report_runner.py              # PDF generation, schedule execution
│   │   ├── email_utils.py                # SMTP email delivery (config + send_email)
│   │   ├── viz_utils.py                  # Vega-Lite: spec extraction, Streamlit render, PNG for PDFs
│   │   └── db.py                         # Lakebase (Postgres) connection pool + DDL + monitoring queries
│   └── fonts/                            # PDF fonts (DejaVuSans)
│
├── data/                                 # Databricks Notebooks
│   ├── 01_synthetic_data_generator.py    # Deterministic synthetic data (incremental)
│   ├── 02_pipeline_bronze_silver.py      # Lakeflow Declarative Pipeline (Bronze + Silver)
│   ├── 03_gold_ai_forecast.py            # ai_forecast() per customer-SKU
│   ├── 04_gold_metric_views.py           # Gold metric views with YAML definitions
│   ├── 05_genie_and_agent.py             # One-time setup: Genie Spaces + Agent config
│   ├── 06_scheduled_report_dispatcher.py # Hourly cron — runs due report schedules
│   └── drop_ai_forecasts.py              # Helper for full-refresh rebuilds
│
└── README.md
```

> **Note:** The app uses a flat layout where `app.py` lives at the app root and all
> supporting utilities live in `app/src/`. At startup `app.py` adds `src/` to
> `sys.path`, so the modules import each other with flat names (`from db import ...`).
> The Databricks bundle definition (`databricks.yml`) is environment-specific and
> is **not** included in this repository — see the deployment note below.

## Lakebase Schema

The operational database stores all runtime state:

| Table | Purpose |
|-------|---------|
| `bi_report_schedules` | Scheduled reports, anomaly alerts, and proactive agent configs (cron, recipients, thresholds) |
| `bi_report_audit_log` | Execution history — status, duration, breach values, PDF paths, email delivery |
| `bi_conversation_sessions` | Supervisor Agent conversation sessions (topic, summary) |
| `bi_conversation_messages` | Individual messages with role, content, timestamps |
| `bi_agent_memory` | Proactive agent memory — narrative, watching list, resolved list, findings (append-only) |

## Setup

### Prerequisites

- A Databricks workspace with Unity Catalog enabled
- A SQL Warehouse (Serverless recommended)
- Databricks CLI configured with a profile
- A Lakebase (managed Postgres) instance

### 1. Configure

Edit the following files with your workspace-specific values:

**`app/app.yaml`** — Set environment variables:
- `LAKEBASE_HOST`, `LAKEBASE_DB`, `PGUSER`, `PGPASSWORD` — Lakebase connection
- `DEMAND_GENIE_SPACE_ID`, `INVENTORY_GENIE_SPACE_ID` — Created in step 3
- `WAREHOUSE_ID` — Your SQL Warehouse ID
- `APP_URL` — Your deployed app URL

**`data/05_genie_and_agent.py`** — Set `WAREHOUSE_ID`

### 2. Deploy the Pipelines & Jobs

This repository ships the application and notebook source only. The Databricks Asset
bundle definition (`databricks.yml`) is environment-specific and is not included — provide
your own bundle that wires up:
- A Lakeflow Declarative Pipeline (Bronze/Silver) from `data/02_pipeline_bronze_silver.py`
- A Daily Forecast Pipeline job (`data/01`, `03`, `04`, `drop_ai_forecasts`)
- A Scheduled Report Dispatcher job (`data/06_scheduled_report_dispatcher.py`, hourly cron)

With a bundle in place, deploy it with:

```bash
databricks bundle deploy --profile <YOUR_PROFILE>
```

Alternatively, upload the `data/` notebooks to your workspace and schedule the jobs manually.

### 3. Run the Setup Notebooks (once)

Run the notebooks in order:

1. **`01_synthetic_data_generator.py`** — Creates the catalog, schemas, and synthetic data
2. **`02_pipeline_bronze_silver.py`** — Runs automatically via the declarative pipeline
3. **`03_gold_ai_forecast.py`** — Generates demand forecasts
4. **`04_gold_metric_views.py`** — Creates governed metric views
5. **`05_genie_and_agent.py`** — Creates Genie Spaces and prints their IDs (update `app.yaml` with these)

### 4. Create Secret Scopes

SMTP (for email delivery):
```bash
databricks secrets create-scope smtp-scope
databricks secrets put-secret smtp-scope smtp-host --string-value "<SMTP_HOST>"
databricks secrets put-secret smtp-scope smtp-port --string-value "<SMTP_PORT>"
databricks secrets put-secret smtp-scope smtp-user --string-value "<SMTP_USER>"
databricks secrets put-secret smtp-scope smtp-password --string-value "<SMTP_PASSWORD>"
```

Lakebase (for the report dispatcher job):
```bash
databricks secrets create-scope lakebase-scope
databricks secrets put-secret lakebase-scope pgpassword --string-value "<LAKEBASE_PASSWORD>"
databricks secrets put-secret lakebase-scope pguser --string-value "<LAKEBASE_USER>"
databricks secrets put-secret lakebase-scope lakebase-host --string-value "<LAKEBASE_HOST>"
databricks secrets put-secret lakebase-scope lakebase-db --string-value "<LAKEBASE_DB>"
```

### 5. Deploy the App

```bash
databricks apps create brand-intel-demo \
  --source-code-path "/Workspace/Users/<you>/.bundle/brand-intel-demo/dev/files/app"

databricks apps deploy brand-intel-demo \
  --source-code-path "/Workspace/Users/<you>/.bundle/brand-intel-demo/dev/files/app"
```

## Customization

This demo uses **generic consumer product categories** (Electronics, Home & Kitchen, Health & Beauty, Sports & Outdoor, Office Supplies) but can be easily adapted to any industry:

- Edit `data/01_synthetic_data_generator.py` to change product categories, SKU names, and customer profiles
- Update the metric view comments in `data/04_gold_metric_views.py` to match your domain
- Adjust the sample Genie questions in `data/05_genie_and_agent.py`

## Key Design Decisions

- **Materialized Views for dimensions** — Avoids row duplication that Auto Loader's append-only semantics would cause on dimension tables, preventing quadratic growth in `ai_similarity()` cross-joins
- **Hash-based deterministic data generation** — Uses `hash()` instead of sequential RNG so incremental runs produce stable, reproducible data
- **Lakebase for operational state** — Managed Postgres keeps schedules, conversation memory, and audit logs separate from the analytical data lake
- **Supervisor Agent pattern** — LLM plans multi-step analyses, Genie executes against governed data, LLM synthesizes results — combining the strengths of each
- **Two-phase proactive detection** — Phase 1 (deterministic SQL) is fast and bounded; Phase 2 (LLM with function calling) provides intelligent, targeted drill-down
- **Template-constrained Genie queries** — The proactive agent fills validated templates rather than free-form questions, ensuring consistent and governable queries
- **Append-only agent memory** — Memory rows are never updated, providing a full audit trail and enabling trend analysis across runs
