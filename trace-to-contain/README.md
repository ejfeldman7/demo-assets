# Trace to Contain

FAB-to-field engineering intelligence for RF semiconductor manufacturing, on synthetic data.

![Trace to Contain — 22-second launch video](media/brag.gif)

## Overview

When a filter module fails at a customer and comes back as an RMA, the money question is not what
happened to that one part. It is whether the rest of the wafer it came from is also bad, and whether
any of it already shipped. Today that answer takes days of stitching together the fab parametric
system, MES, and final-test logs by hand.

Trace to Contain treats the RMA as the trigger, not the evidence. It traces the return to its wafer
population, explains the likely driver with correlation that has the test-floor confounders stripped
out, predicts which sibling die are at risk, and routes a tiered containment recommendation to the
engineer who has the authority to act on it. The decision, its evidence, and the model version are
written back for audit.

The positioning is the seam none of the point tools own: fab (wafer sort) to final test to field.
Yield and test-analytics vendors stop at wafer sort; this connects the three islands and turns the
connection into a governed, auditable action.

Alongside the guided case flow, an **Ask the Data** panel puts a curated Genie space over the same
lakehouse, so an engineer can ask the population- and portfolio-level questions the case view does not
pre-answer — blast radius, shipment exposure, which lots are worst — in plain language. The case flow
is depth on one return; Ask the Data is breadth across the population.

The demo data carries a real story. A film-thickness excursion on deposition tool DEP-02 shifts BAW
filter center frequency, worst at the wafer edge, and that front-end signature propagates to
final-test margin loss and field returns. Two confounders are built in on purpose (an ATE test-site
offset and a probe-card time drift) so the demo can show why the raw correlation is misleading and
what conditioning it reveals.

## Live Links

- Runs as a Databricks App on any Unity Catalog workspace. See Setup below to deploy your own.
- PRD (full requirements, four-persona review): [`docs/prd.md`](./docs/prd.md)

## Features

- Genealogy trace (module to die to wafer to lot) with per-hop trace grain and confidence, degrading
  gracefully to wafer-level when die-level trace does not exist.
- Confounder-conditioned correlation: center-frequency shift holds after conditioning on ATE site,
  insertion loss collapses once conditioned on time (a probe-card drift artifact).
- One feature definition (`die_features`) used for point-in-time training and live scoring, with the
  current per-case vector served from Lakebase (`trace_ops.online_features`).
- Margin regressor trained with Optuna, tracked in MLflow, registered to Unity Catalog.
- Wafer spatial map: predicted margin per die, showing the radial excursion signature.
- What-if: edit front-end measurements and re-score the die live against a Model Serving endpoint.
- Tiered containment agent (no action, sample screen, 100% screen, partial hold, full lot hold) with
  evidence, rejected alternatives, and a foundation-model narrative (Claude) explaining the call.
- Role-gated approval enforcing segregation of duties (FA Engineer proposes, Quality Engineer holds,
  MRB disposes), an MES hold instruction on approval, and an append-only audit trail.
- Ask the Data: a curated Genie space over the lakehouse answers population- and portfolio-level
  questions the guided flow doesn't pre-answer (blast radius, shipment exposure, cross-case patterns),
  returning the answer, the generated SQL, and rows. Runs on-behalf-of the signed-in user (Unity
  Catalog / RLS apply) with an app service-principal fallback.
- Reset Demo button that restores the operational store to its seeded baseline.

## Prerequisites

- A Databricks workspace with Unity Catalog, Lakebase, Feature Engineering, MLflow, Model Serving,
  and Databricks Apps enabled. Built and verified on `unity-gateway-demo` (AWS, us-east-2).
- A SQL warehouse for data exploration and DDL.
- CLI `databricks` >= v1.15 and Python 3.12 with `databricks-connect`, `faker`, `numpy`, `pandas`,
  and `psycopg[binary]` for the data-generation and seed steps.
- Node 18+ and npm to build the app frontend (or use the prebuilt assets in `app/server/static`).

## Setup

The build runs in three phases. Notebooks live in [`src/`](./src); the app in [`app/`](./app).

```bash
# 0. Catalog + schemas (metastore has Default Storage, so create via SQL)
#    CREATE CATALOG trace_to_contain;
#    CREATE SCHEMA trace_to_contain.lakehouse;  CREATE SCHEMA trace_to_contain.ops;

# 1. Synthetic data (serverless notebooks) -> lakehouse
#    src/data-generation/01_generate_fab_data.py   (lot, wafer, die, fab_measurements, module,
#                                                    back_end_outcomes, die_to_module, parameter_dim)
#    src/data-generation/02_generate_rma.py         (rma_history)

# 2. Intelligence (serverless jobs)
#    src/ml/01_features_and_correlation.py  -> die_features (UC feature table) + correlation_results
#    src/ml/02_train_margin_model.py         -> ft_margin_predictor@prod + back_end_prediction + evals
#    src/ml/03_whatif_serving_twin.py        -> ft_margin_whatif (raw-feature model for live scoring)
#    Create a scale-to-zero Model Serving endpoint from ft_margin_whatif:
#    databricks serving-endpoints create --json '{"name":"trace-margin-whatif","config":{...}}'

# 2b. Population Q&A Genie space (curated to the genie-workbench IQ-Scanner checklist)
#    src/ops/02_create_genie_space.py  -> Genie space over 7 lakehouse tables (rma_history,
#                                          back_end_outcomes, die, wafer, lot, die_to_module, module);
#                                          prints space_id. Paste it into app/app.yaml (GENIE_SPACE_ID).

# 3. Operational store + app
#    Create ONE Lakebase project (auto-provisions a production branch); add a scale-to-zero branch
#    for the app. Seed the ops schema:
#    src/ops/01_seed_lakebase_ops.py  (pass pg_host/pg_token/pg_user) -> trace_ops.* + trace_ops_seed

#    Build + deploy the app:
cd app/client && npm install && npm run build        # emits app/server/static
cd .. && databricks sync . /Workspace/Users/<you>/trace-to-contain-src
databricks apps create trace-to-contain
#    Attach resources (postgres branch/database + two serving endpoints) via apps create-update,
#    grant the app SP on trace_ops (+ SELECT on trace_ops_seed), grant the app SP CAN RUN on the Genie
#    space + SELECT on the 7 lakehouse tables, then:
databricks apps deploy trace-to-contain --source-code-path /Workspace/Users/<you>/trace-to-contain-src
```

### Configuration (deploy it yourself)

The demo carries no customer branding and no hardcoded personal paths, so it is meant to be
re-pointed at your own workspace:

- Catalog and schema are set at the top of each notebook (default `trace_to_contain` / `lakehouse`);
  `02_train_margin_model.py` and the seed notebook also take widgets. Change them in one place.
- The Lakebase project id (`trace-to-contain`) is referenced by the seed step and by the app's
  `LAKEBASE_ENDPOINT`. Use your own project id.
- The app reads `PGHOST`, `LAKEBASE_ENDPOINT`, `NARRATIVE_ENDPOINT`, and `WHATIF_ENDPOINT` from
  `app/app.yaml` env; set them to your deployment's values. `PGUSER` is injected by the attached
  Lakebase resource.
- The MLflow experiment folder resolves to the notebook runner's home (`current_user`), so no path
  edit is needed.

See [`docs/prd.md`](./docs/prd.md) §14 for the phased build plan and the data model.

## Architecture

Rendered in-app on the Solution Reference tab. Four zones, left to right:

1. Sources (manufacturing): ATE / wafer-probe test (STDF-like), MES / QMS RMA events, a landing volume.
2. Lakehouse (system of record, governed by Unity Catalog): Lakeflow / Auto Loader ingestion; Delta
   tables (`fab_measurements` long/EAV, genealogy, `back_end_outcomes`, `rma_history`); a
   confounder-conditioned correlation step; and a curated **Genie space (Ask the Data)** for governed
   NL→SQL over these tables.
3. Intelligence: one Feature Engineering definition (`die_features`); an MLflow/Optuna margin model
   registered to UC; a Model Serving endpoint (Foundation Model API, Claude) for the narrative.
4. Operational: one Lakebase project holding the online feature vector and the operational store
   (`trace_ops`: cases, predictions, recommendations, approvals, workflow events); a governed agent;
   the Databricks App; and the FA / Quality / MRB human-in-the-loop.

An approved hold issues an instruction to MES and writes the disposition back to the lakehouse,
closing the loop. Unity Catalog spans the flow for lineage and audit
(`fab_measurements` to features to model to prediction to disposition).

Lakebase is deliberately one project. Feature Engineering's managed online store provisions its own
project, so the online feature vector is served from `trace_ops.online_features` in the operational
project instead, keeping the whole demo on a single Lakebase project.

## Operations

- The `trace-margin-whatif` serving endpoint is scale-to-zero. The first what-if call after idle cold
  starts (about 10 to 15 seconds); warm it once before presenting.
- Reset Demo restores `trace_ops` from the `trace_ops_seed` snapshot and resets sequences. It is fast
  (set-based), unlike the one-time seed which is slower.
- Two serving endpoints back the app: the Foundation Model endpoint for the narrative and
  `trace-margin-whatif` for live scoring. Both need the app service principal to have CAN_QUERY.
- Ask the Data (Genie) runs on-behalf-of the signed-in user with an app-SP fallback; the app SP needs
  CAN RUN on the space and SELECT on the seven lakehouse tables. The first query cold-starts the
  space/warehouse (about 10 to 30 seconds); warm it once before presenting.

## Known Issues / Roadmap

- The margin model floors around 0.2 dB, so what-if sliders reliably cross the guardband but will not
  drive a die below the hard spec. The guardband crossing is the intended demo beat.
- The recommendation engine is deterministic (tiered rules over the prediction, population, and
  lineage). The foundation model writes the explanation, not the decision.
- No DAB yet; deployment is CLI-driven. A bundle would make it one-command redeployable.

## Changelog

- 2026-09-14: Initial contribution. Synthetic data, correlation, margin model, what-if serving,
  Lakebase operational store, and the case-management app.
- 2026-09-15: Added Ask the Data — a curated, workbench-audited Genie space over the lakehouse
  (`src/ops/02_create_genie_space.py`) surfaced as an in-app panel (`/api/genie/ask`, on-behalf-of the
  user with an app-SP fallback).
