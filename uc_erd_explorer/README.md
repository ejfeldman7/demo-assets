# Interactive Unity Catalog ERD Viewer

A Databricks App that turns Unity Catalog's `information_schema` metadata into an
interactive, explorable entity-relationship diagram — with a built-in Genie chat for
asking schema questions in plain English.

![ERD overview](docs/screenshots/erd-overview.png)

## What it does

- **Renders a live ERD** for one or more Unity Catalog catalogs: every table as a node
  (with its columns, and PK/FK columns badged), every declared foreign key as a labeled,
  directional edge from the referencing table to the table it points to.
- **Catalog/schema tree picker**: an "All" toggle plus one row per catalog (tri-state
  checkbox: unchecked / indeterminate / checked) that expands to show its individual
  schemas, each independently selectable — built for a future with many catalogs each
  containing many schemas, not just the shipped single-catalog demo.
- **Click-to-filter**: click any table and the canvas highlights just its connected
  neighborhood — toggle between "direct neighbors" and "full connected component," with
  a one-click reset back to the whole graph.
- **Ask Genie**: a slide-in chat panel backed by a Genie Space that can answer questions
  like *"which tables reference `orders`?"*, *"what's the primary key of `invoices`?"*,
  or *"which tables have no foreign keys?"* — scoped so it can **only** ever see the
  catalogs you explicitly approved, never anything else in the workspace (see "Unscoped
  mode" below for the one deliberate exception).
- **No source data required**: everything is read from `information_schema` /
  `system.information_schema`, so this works against any Unity Catalog catalog with
  declared PK/FK constraints — you don't need to grant it access to actual row data.

![Click-to-filter](docs/screenshots/erd-click-filter.png)

![Genie chat](docs/screenshots/genie-chat.png)

## Why the Genie scoping matters

Most "point an LLM at your metadata" tools rely on prompt instructions to keep the
assistant in its lane ("only answer about catalog X"). Instructions are soft — a
determined question can talk around them. This app takes a different approach:

Genie is built on **dedicated Unity Catalog views** whose `WHERE table_catalog IN (...)`
clause is baked into the view DDL itself. Genie's SQL execution can only ever resolve
those views, so there is no query it could construct that reaches a catalog outside your
approved list — regardless of what the underlying service principal can otherwise browse.
The boundary lives in the data model, not in the prompt.

## Architecture

```
Databricks App (FastAPI backend + React/Vite frontend, Databricks-brand light theme)
├── Backend
│   ├── GET  /api/graph        → nodes (tables+columns) + edges (FK→PK), optionally
│   │                             narrowed via ?pairs=catalog.schema,..., queried live
│   │                             from system.information_schema
│   ├── GET  /api/schema-tree  → {catalog: [schema, ...]} enumeration for the picker
│   ├── GET  /api/config       → which catalogs this deployment is scoped to
│   └── POST /api/genie/ask    → starts/continues a Genie conversation, polls internally,
│                                 returns the final answer in one round-trip
├── Frontend (React Flow + dagre auto-layout)
│   ├── ERD canvas, PK/FK badges, labeled FK→PK edges
│   ├── Click-to-filter (client-side BFS: neighbors / connected component + reset)
│   ├── Catalog/schema tree picker + table search
│   └── Genie side panel
└── Setup (idempotent, chained as a single Databricks Job — see Deployment below)
    ├── create_scoped_views.py  → creates the hard-scoped metadata views that are
    │                              Genie's actual data source and access boundary
    └── create_genie_space.py  → creates/updates the Genie Space over 3 of those views
        (table_summary, column_inventory, fk_edges), and grants the app's own service
        principal access to the space
```

The scoped metadata views (in `{ERD_METADATA_LOCATION}`, e.g. `your_catalog.erd_meta`):

| View | Purpose |
|---|---|
| `table_summary` | One row per table — column count, PK column count, incoming/outgoing FK counts. Answers "what tables exist" and "which tables have no foreign keys." |
| `column_inventory` | One row per column, with `is_primary_key`/`is_foreign_key` flags. Answers "what columns does X have." |
| `fk_edges` | One row per foreign-key relationship (`fk_table.fk_column → pk_table.pk_column`). Answers "what references X" and "what does X join to." |

These 3 are the only views curated into the Genie Space — deliberately narrow, per
Databricks guidance that Genie Spaces perform best small and focused. They're built on
top of 5 internal, 1:1-filtered mirrors of `system.information_schema.*` (also created by
`create_scoped_views.py`) that are never exposed to Genie directly.

## Demo data

Out of the box this ships pointed at a synthetic `megacorp` catalog — a fictional
manufacturer with a **factory** schema (plants, production lines, machines, materials,
work orders, quality inspections, sensor readings, shifts, operators) and a SAP-style
**erp** schema (customers, vendors, sales/purchase orders, invoices, payments, cost
centers, general ledger). 22 tables, 26 foreign keys, including cross-schema references —
enough to be a genuinely interesting graph without needing any real customer data. DDL is
in `setup/megacorp_schema.sql`; no rows are populated, only structure.

## Prerequisites

- A Databricks workspace with Unity Catalog and Databricks Apps enabled
- The [Databricks CLI](https://docs.databricks.com/dev-tools/cli/index.html) (`databricks bundle` support)
- A SQL warehouse (serverless recommended)
- `uv` (Python) and `npm` (Node) for local development / building the frontend
- Enough Unity Catalog privilege to `GRANT` on your target catalog(s) — either you're an
  admin, or you can ask one to run the grant step below

## Two ways to deploy

Both routes run the exact same underlying setup logic (`setup/create_scoped_views.py` +
`setup/create_genie_space.py`), so behavior never diverges between them — pick whichever
fits how your team works.

### Route 1: CLI + Databricks Asset Bundle

For anyone with local CLI access.

```bash
git clone <this-repo> && cd erd-explorer
cd frontend && npm install && npm run build && cd ..   # build the React SPA first
databricks auth login --host <your-workspace-url> --profile <your-profile>
databricks bundle deploy -t dev -p <your-profile>
```

At this point the app and its setup job exist in the workspace but the demo catalog and
Genie Space don't yet. Finish the bootstrap:

```bash
# 1. Create the synthetic megacorp catalog (skip this if pointing at your own catalog(s) instead)
uv run --with databricks-sdk python setup/run_ddl.py setup/megacorp_schema.sql --profile <your-profile>

# 2. Grant the app's service principal access to it (see "Permissions" below for the exact commands)
databricks apps get erd-explorer-dev -p <your-profile>   # note service_principal_client_id

# 3. Create the scoped metadata views + Genie Space (idempotent, safe to re-run)
databricks bundle run setup_genie_space -t dev -p <your-profile>

# 4. Start the app
databricks bundle run erd_explorer_app -t dev -p <your-profile>
```

Open the URL printed by step 4.

**Deploying against your own catalog(s)** instead of (or in addition to) the demo data:

```bash
databricks bundle deploy -t dev -p <your-profile> \
    --var="erd_catalogs=sales,inventory" \
    --var="erd_metadata_location=sales.erd_meta" \
    --var="warehouse_id=<your-warehouse-id>"
```

Then run the same steps 2–4 above. The catalog list and metadata location are one
variable each, consumed by both the app and the Genie setup job — they can't drift apart.

### Route 2: Git folder + notebook (no CLI required)

For teams without local CLI/terminal access, or who'd rather configure and deploy
entirely from inside the workspace UI.

1. In the Databricks workspace UI: **Workspace ▸ Git Folders ▸ Add repo**, paste this
   repo's URL.
2. Open `notebooks/install.py` from inside that folder.
3. Click **Run all** once — this renders the configuration widgets at the top of the
   notebook (they take the place of the CLI route's `--var` bundle variables):

   | Widget | Required | What it controls |
   |---|---|---|
   | `repo_root` | yes | Workspace path to this repo's checkout, e.g. `/Workspace/Users/you@company.com/erd-explorer` (right-click the folder → "Copy path" if unsure) |
   | `warehouse_id` | yes | SQL warehouse id |
   | `app_name` | no (default `erd-explorer`) | Databricks App name |
   | `erd_catalogs` | no (default `megacorp`) | Comma-separated catalog allow-list. Clear it entirely for unscoped mode (every catalog visible to this deployment — see "Unscoped mode" below) |
   | `erd_metadata_location` | no (default `<first catalog>.erd_meta`) | Where the scoped Genie views live. **Required** if you clear `erd_catalogs` |
   | `create_demo_catalog` | no (default `no`) | Set to `yes` to create the synthetic `megacorp` catalog first |

4. Fill in the widgets, then **Run all** again. The notebook will, in order: optionally
   create the demo catalog, create the scoped Genie metadata views, stage an isolated
   deploy folder (so widget-driven config never touches the tracked `app.yaml`), create
   the Databricks App and deploy it, create/update the Genie Space and grant the app
   access to it, then redeploy with the real Genie Space id and start the app. It prints
   the app URL and the service principal client id at the end.
5. One remaining manual step, same as Route 1 — see **Permissions** below.

The notebook is idempotent (safe to "Run all" repeatedly, e.g. after changing widget
values) and calls the identical `setup/create_scoped_views.py` /
`setup/create_genie_space.py` functions the CLI route's job does — it's a different
front door onto the same automation, not a separate implementation to maintain.

## Configuration reference

| Bundle variable | Env var (equivalent) | Default | What it controls |
|---|---|---|---|
| `warehouse_id` | `DATABRICKS_WAREHOUSE_ID` | **required, no default** | SQL warehouse used for all `information_schema` queries |
| `erd_catalogs` | `ERD_CATALOGS` (comma-separated) | `megacorp` (packaged demo default) | The catalog allow-list — scopes **both** the ERD graph and the Genie Space. Set to an empty string for unscoped mode, see below |
| `erd_metadata_location` | `ERD_METADATA_LOCATION` (`"catalog.schema"`) | `megacorp.erd_meta` | Where the scoped Genie metadata views live. **Required** if `erd_catalogs` is empty (no catalog to default from) |
| `genie_space_id` | `GENIE_SPACE_ID` | (set after first setup run) | Which Genie Space the chat panel talks to |

**One asymmetry worth knowing**: the ERD graph is queried live, so changing
`erd_catalogs` and redeploying takes effect immediately. The Genie Space's views and
table list are saved configuration — after changing `erd_catalogs` or
`erd_metadata_location`, re-run `databricks bundle run setup_genie_space` to resync it.
That job is idempotent and finds/updates its own space automatically (via a stable
marker in the space description, not its title, so a changed catalog list won't create a
duplicate space).

### Unscoped mode

Leaving `erd_catalogs`/`ERD_CATALOGS` **empty** is a deliberate, explicit choice — not a
silent fallback — that scopes both the graph and the Genie Space to *every catalog this
deployment's own credentials can browse*, instead of an explicit allow-list. Unity
Catalog's own privilege filtering still applies: "unscoped" means "whatever this
deployment's grants allow," not literally every catalog that exists in the metastore.

```bash
databricks bundle deploy -t dev -p <your-profile> \
    --var="erd_catalogs=" \
    --var="erd_metadata_location=<some_catalog>.erd_meta" \
    --var="warehouse_id=<your-warehouse-id>"
```

Per a deliberate design decision: Genie mirrors this choice exactly — an unscoped graph
means an unscoped Genie Space too, not a Genie Space quietly kept narrow while the graph
widens. If you want Genie to stay narrowly scoped, use an explicit `erd_catalogs` list
instead of unscoped mode.

`erd_metadata_location` becomes **required** in unscoped mode (there's no "first catalog"
in the list to default the metadata views into) and can point at any catalog you like,
including one not otherwise shown in the graph.

## Permissions

Two *separate* things need granting, and both are workspace-specific enough that we
don't auto-generate the grant statements — but everything downstream of them is scripted.

Both need your app's **service principal client id**. Get it either way:
- CLI: `databricks apps get erd-explorer-dev -p <profile>` (look for `service_principal_client_id`)
- UI: open the app in the Databricks Apps UI and check its **Authorization** tab — see
  [Databricks Apps authorization docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth#app-authorization)
  for exactly where to find it if the tab layout has moved.

**1. Unity Catalog data access** (lets `/api/graph` and the scoped views actually return
rows — without this, queries succeed but silently return zero rows, not an error). This
is really **two grants, for two potentially different locations**:

```sql
-- (a) your actual data catalog(s) -- repeat this block per catalog/schema in erd_catalogs
GRANT USE CATALOG ON CATALOG <your_catalog> TO `<app-service-principal-client-id>`;
GRANT USE SCHEMA ON SCHEMA <your_catalog>.<your_schema> TO `<app-service-principal-client-id>`;
GRANT SELECT ON SCHEMA <your_catalog>.<your_schema> TO `<app-service-principal-client-id>`;

-- (b) wherever erd_metadata_location actually points -- by DEFAULT this is
-- <first erd_catalogs entry>.erd_meta (so often the same catalog as (a), just a
-- different schema), but if you've overridden erd_metadata_location to a different
-- catalog, grant on THAT catalog instead, not your data catalog:
GRANT USE CATALOG ON CATALOG <metadata_catalog> TO `<app-service-principal-client-id>`;  -- only if different from (a)
GRANT USE SCHEMA ON SCHEMA <metadata_catalog>.<metadata_schema> TO `<app-service-principal-client-id>`;
GRANT SELECT ON SCHEMA <metadata_catalog>.<metadata_schema> TO `<app-service-principal-client-id>`;
```

**2. Genie Space access** — a Genie Space is a workspace object with its own separate
ACL from Unity Catalog grants. This one *is* automated: `setup/create_genie_space.py`
takes `--grant-to-app <app-name>` (already wired into the DAB job) and grants the app's
service principal `CAN_RUN` on the space automatically every time the setup job runs.

## Local development

```bash
# Backend
DATABRICKS_PROFILE=<your-profile> uv run uvicorn app:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

The Vite dev server proxies API calls to the backend. Query the real `system.information_schema`
via your own CLI profile — no mocking.

## Troubleshooting

- **App shows `"Frontend not built"`** — the React SPA (`frontend/dist/`) wasn't
  present in the deployed source. Run `cd frontend && npm run build` and (if you're
  modifying this repo) commit the result. Unlike most Vite projects, `frontend/dist/` is
  **intentionally not gitignored here** — Route 2 (notebook/Git folder) has no local
  build step at all, so if `dist/` weren't committed, a fresh Git folder clone could never
  produce it. `databricks.yml` also has a `sync.include: ["frontend/dist/**"]` as
  defense-in-depth for Route 1 in case gitignore rules change. **After any change to
  `frontend/src/`, always run `npm run build` and commit the updated `frontend/dist/`.**
- **Genie chat returns a permission error** (`does not have read permission for node...`)
  — the app's service principal has UC data access but not Genie Space access. Re-run
  `databricks bundle run setup_genie_space` (it re-applies the `CAN_RUN` grant every
  time), or grant it manually via `PATCH /api/2.0/permissions/genie/{space_id}`.
- **`/api/graph` returns 0 nodes** — almost always a missing Unity Catalog grant (see
  Permissions above), not a bug. Constraint metadata views silently filter to what the
  caller can see rather than erroring.
- **Genie doesn't know about a table you just added** — the scoped views and Genie
  Space are snapshots, not live. Re-run `databricks bundle run setup_genie_space`.
- **`/api/graph?pairs=...` returns a 400** — either a pair isn't in `catalog.schema`
  format, or you asked for a catalog outside this deployment's `erd_catalogs` allow-list.
  Both are intentional: the app rejects invalid/out-of-scope requests clearly instead of
  silently widening back to "everything."

## Caveats

- **Unity Catalog PK/FK constraints are optional and frequently undeclared** in real
  environments — this tool can only show relationships that were explicitly declared as
  constraints. A catalog with real foreign-key relationships but no declared constraints
  will render as a set of disconnected tables. There's no way around this; it's a
  property of the underlying metadata, and Genie's instructions are written to say so
  rather than invent relationships that aren't there.
- This is a schema/metadata tool, not a data catalog or lineage tool — it doesn't show
  row counts, actual values, or upstream/downstream pipeline lineage.
- **Unscoped mode is a real widening of what Genie can see** — think of it as "trust the
  UC grants I've already given this service principal" rather than "safe by default."
  If you want Genie to answer questions about a small, deliberate set of catalogs no
  matter what else the app's SP can browse, use an explicit `erd_catalogs` list instead.
- In unscoped mode, catalogs whose names start with `__` (Databricks-internal plumbing,
  e.g. `__databricks_internal_catalog_...`) are automatically excluded from both the
  graph and Genie's scope — they aren't real user catalogs and would just be noise.

## Project structure

```
erd-explorer/
├── app.py, app.yaml              # FastAPI entrypoint + Databricks App config
├── server/
│   ├── config.py                 # dual-mode auth, catalog/metadata-location resolution
│   ├── graph.py                  # ERD graph builder (queries system.information_schema)
│   └── routes/{graph,genie}.py   # API routes
├── frontend/                     # React + Vite + reactflow + dagre SPA
│   └── dist/                     # built output -- committed on purpose, see Troubleshooting
├── setup/
│   ├── megacorp_schema.sql, run_ddl.py       # optional: creates the demo catalog
│   ├── create_scoped_views.py                # Genie's hard-scoped data source
│   └── create_genie_space.py                 # Genie Space create/update + ACL grant
├── notebooks/
│   └── install.py                # Route 2: notebook-based install (calls the same
│                                    setup/ functions as Route 1's DAB job)
├── databricks.yml                # Route 1: Databricks Asset Bundle (app + setup job)
└── DEMO.md, TASKS.md             # build history / design decisions (internal record)
```
