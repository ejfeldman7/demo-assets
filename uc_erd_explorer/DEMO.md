# Interactive UC ERD Viewer

## What this is
A Databricks App that renders an interactive entity-relationship diagram (ERD) for a
Unity Catalog catalog: tables as nodes, primary-key/foreign-key relationships as edges,
click-to-filter to a table's connected neighborhood, and a popup chat backed by a Genie
Space for asking schema questions in natural language.

## Demo data
`megacorp` — a synthetic manufacturer catalog created purely for this demo (structure
only, no rows needed):
- `megacorp.factory` — plants, production lines, machines, materials, bill of materials,
  work orders, quality inspections, sensor readings, shifts, operators (11 tables).
- `megacorp.erp` — SAP-style ERP: customers, vendors, sales/purchase orders + line items,
  invoices, payments, cost centers, general ledger (11 tables).
- Cross-schema FKs link ERP order lines and cost centers back into factory materials/plants,
  so the ERD shows a single connected graph spanning both schemas.
- 22 tables, 22 PK constraints, 26 FK constraints (verified via
  `megacorp.information_schema.table_constraints`).
- DDL lives at `setup/megacorp_schema.sql`, applied via `setup/run_ddl.py`.

## Scope decisions (locked in with user)
- **Catalog scope is configurable via `ERD_CATALOGS`** (default `megacorp`) — both the ERD
  graph and the Genie Space are scoped to exactly this list, never wider.
- **Genie Space answers schema/metadata questions only**, via dedicated scoped views (see
  "Genie data model" below) — it does not touch business data, since there isn't any
  (structure-only catalog).
- **Auth: service principal.** The app's own identity queries Unity Catalog; all users see
  the same view. Requires the app SP to have `USE CATALOG`/`USE SCHEMA`/`SELECT` on the
  configured catalogs (granted once at setup time — see "Deploying to a new workspace").

## Architecture
```
Databricks App (FastAPI backend + Vite/React frontend)
├── Backend
│   ├── GET  /api/graph       → nodes (tables+columns) + edges (FK→PK) for ERD_CATALOGS
│   ├── GET  /api/config      → which catalogs this deployment is scoped to
│   └── POST /api/genie/ask   → start/continue a Genie conversation; polls internally
│       (up to 60s) and returns the final answer in one round-trip
├── Frontend
│   ├── ERD canvas: React Flow + dagre auto-layout, PK/FK badges, labeled edges
│   ├── Click-to-filter: client-side BFS over the loaded adjacency list
│   │   (direct neighbors vs. full connected component, with reset)
│   └── Popup chat panel wired to the Genie proxy endpoints
└── Setup (one-time, idempotent, chained in the setup_genie_space DAB job)
    ├── setup/megacorp_schema.sql + run_ddl.py — creates the demo catalog itself
    ├── setup/create_scoped_views.py — creates the hard-scoped metadata views
    │   (Genie's actual access boundary; see "Genie data model" below) -- runs FIRST
    └── setup/create_genie_space.py — creates/updates the Genie Space over
        just 3 of those views (table_summary, column_inventory, fk_edges)
```

## Environment (this deployment)
- Workspace: `ef-temp-demo` (https://fe-vm-ef-demo-workspace.cloud.databricks.com)
- Warehouse: `6a09f4ec67bb14b5` (existing serverless warehouse, reused)
- Catalog: `megacorp` (created this session)
- **Live app**: https://erd-explorer-dev-2788980919466354.aws.databricksapps.com
  (verified end-to-end: `/api/health`, `/api/graph`=22/26, real `/api/genie/ask`)
- Genie Space: `space_id=01f175d54d24139aa826c977f3e88e74`

## Deploying to a new workspace
```bash
cd erd-explorer
databricks bundle deploy -t dev -p <your-profile> \
    --var="erd_catalogs=<your_catalog1,your_catalog2>" \
    --var="erd_metadata_location=<your_catalog1>.erd_meta" \
    --var="warehouse_id=<your_warehouse_id>"
```
Then, one-time manual step (not automatable generically -- requires whatever admin/grant
privileges your workspace uses): grant the app's own service principal (shown by
`databricks apps get erd-explorer-dev -p <profile>` as `service_principal_client_id`)
`USE CATALOG` on each catalog in `erd_catalogs`, and `USE SCHEMA` + `SELECT` on every
schema within them (this is what lets `/api/graph` and the scoped metadata views resolve
rows from `system.information_schema` -- without it, rows are silently filtered out, not
an error). Example:
```sql
GRANT USE CATALOG ON CATALOG your_catalog TO `<sp-client-id>`;
GRANT USE SCHEMA ON SCHEMA your_catalog.some_schema TO `<sp-client-id>`;
GRANT SELECT ON SCHEMA your_catalog.some_schema TO `<sp-client-id>`;
-- repeat per schema, plus the erd_meta schema itself:
GRANT USE SCHEMA ON SCHEMA your_catalog.erd_meta TO `<sp-client-id>`;
GRANT SELECT ON SCHEMA your_catalog.erd_meta TO `<sp-client-id>`;
```
Then run the setup job and start the app -- everything else (view creation, Genie Space
creation, and granting the app's SP access to the Genie Space itself, which is a
*separate* ACL from the UC grants above) is automated:
```bash
databricks bundle run setup_genie_space -t dev -p <your-profile>
databricks bundle run erd_explorer_app -t dev -p <your-profile>
```
`setup_genie_space` is idempotent (safe to re-run any time you change `erd_catalogs` or
`erd_metadata_location`) and finds/updates its own space via a stable marker in the
description, not the title -- so changing the catalog list won't create a duplicate space.

## Single source of truth for catalog scope: `ERD_CATALOGS`
Both the ERD graph and the Genie Space are scoped by the **same** catalog list, so a
customer deploying this into their own workspace only sets one thing:
- App: `ERD_CATALOGS` env var (comma-separated catalogs, default `megacorp`) drives
  `GET /api/graph` directly and live (`server/config.get_catalogs()`).
- Genie: driven by the *same* `ERD_CATALOGS` (or explicit `--catalogs`), but see the
  "Genie data model" section below -- as of this revision Genie's boundary is enforced by
  dedicated Unity Catalog views, not by pointing it at any catalog's own information_schema.
- **Asymmetry to call out in the README**: the graph is live-queried, so changing
  `ERD_CATALOGS` takes effect immediately. Genie's scoped views and space are saved
  configuration, not live -- if you change `ERD_CATALOGS` after initial deploy, re-run
  `databricks bundle run setup_genie_space` (both scripts are idempotent) to resync.
- DAB: `var.erd_catalogs` templates into both the app's `ERD_CATALOGS` env var and the
  `setup_genie_space` job's `--catalogs` args on both tasks -- never set independently.

## Genie data model: hard-scoped UC views, not information_schema directly (revised)
Superseding the earlier design (Genie curated directly on `<catalog>.information_schema.*`).
Per user direction: treat Genie scoping as **a data-modeling problem, not an instructions
problem** -- Genie instructions/trusted-assets guidance are soft (a determined prompt can
talk around them); the real boundary has to be enforced in the UC objects Genie can query.

- `setup/create_scoped_views.py` creates a dedicated metadata schema at
  `{ERD_METADATA_LOCATION}` (env var `"catalog.schema"`, default
  `"<first ERD_CATALOGS entry>.erd_meta"`, e.g. `megacorp.erd_meta`) containing:
  - 5 internal 1:1-filtered mirrors of `system.information_schema.*`
    (`scoped_tables`, `scoped_columns`, `scoped_table_constraints`,
    `scoped_key_column_usage`, `scoped_referential_constraints`), each with
    `WHERE table_catalog IN (<ERD_CATALOGS>)` baked into the view DDL itself.
  - 4 denormalized helper views built only on the internal ones above (never touch
    `system.information_schema` again): `primary_keys`, `fk_edges` (the FK->PK join,
    computed once here), `column_inventory` (columns + is_primary_key/is_foreign_key),
    `table_summary` (per-table column/PK/FK counts -- `outgoing_foreign_key_count = 0`
    finds tables with no FKs).
- `setup/create_genie_space.py` curates **only 3** of those views into the Genie Space --
  `table_summary`, `column_inventory`, `fk_edges` -- deliberately narrow per Databricks
  guidance that Genie Spaces perform best small/focused. The other 6 views are internal
  plumbing, never exposed to Genie directly.
- Why this is a hard boundary and not a soft one: Genie's SQL execution can only ever
  resolve `{loc}.table_summary`/`.column_inventory`/`.fk_edges` -- those views' own WHERE
  clauses already exclude every catalog not in `ERD_CATALOGS` before Genie ever sees a row.
  There is no query Genie could construct that reaches an out-of-scope catalog, regardless
  of what the underlying service principal/warehouse can otherwise browse.
- Verified via `ef-temp-demo`: "What tables exist in the psk catalog?" (psk is a real,
  much larger catalog the same SP can see) -> "I have no visibility into the psk catalog."
  Also verified: "How many customers does the company have?" correctly declined as
  business data (these views carry zero row-level data, only structure).
- Order matters: `create_scoped_views.py` must run before `create_genie_space.py` --
  the DAB's `setup_genie_space` job encodes this as a 2-task dependency chain.

## Graph query source: `system.information_schema` (revised)
Verified empirically against `ef-temp-demo` (no special enablement needed, unlike other
`system.*` schemas): `system.information_schema.{table_constraints,key_column_usage,
referential_constraints}` aggregates PK/FK metadata across **every catalog in the
metastore** in one query (`WHERE table_catalog IN (...)`), privilege-filtered per caller
just like the per-catalog views. The backend's `/api/graph` should query this instead of
looping per-catalog `<catalog>.information_schema.*` — same join logic, one query, and it
scales to multiple catalogs for free if this app is ever pointed at more than `megacorp`.

**Genie Space's data source is the scoped views in `{ERD_METADATA_LOCATION}`, never
`system.information_schema` directly** — see "Genie data model" above for the current
(revised) design. A Genie Space's curated table list is its actual access boundary;
pointing it at `system.information_schema` would let it see every catalog the app's
service principal can browse (confirmed: psk, dais, samples, etc. all show up in that
view raw). Scoped views with the catalog filter baked into their own DDL are a hard
boundary; a `WHERE table_catalog = 'megacorp'` instruction alone would only be a soft one.

## Full research/design record
See `/Users/ethan.feldman/.claude/plans/snug-tumbling-sunbeam.md` for the original
architecture plan (join SQL, Genie REST endpoints, DAB structure) — the catalog scope was
narrowed to `megacorp` after that plan was approved; everything else in it still applies.
