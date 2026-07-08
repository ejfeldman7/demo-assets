export interface TagValue {
  name: string
  value: string
}

export interface ColumnMeta {
  name: string
  type: string
  is_pk: boolean
  is_fk: boolean
  // Unity Catalog COMMENT / tags -- null/empty when the deployment's catalog has none.
  comment: string | null
  tags: TagValue[]
}

export interface TableNodeData {
  id: string
  catalog: string
  schema: string
  table: string
  comment: string | null
  tags: TagValue[]
  columns: ColumnMeta[]
}

// A collapsed schema summary node -- what /api/graph returns per (catalog, schema)
// instead of per-table once a catalog has more tables than
// ERD_SCHEMA_COLLAPSE_THRESHOLD. Selecting the schema (the same tree-picker mechanism
// used everywhere else) is what "expands" it to full TableNodeData detail.
export interface SchemaNodeData {
  id: string
  catalog: string
  schema: string
  table_count: number
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  fk_columns: string[]
  pk_columns: string[]
  constraint_name: string | null
  // True for a heuristic, undeclared-relationship guess (see server/graph.py
  // infer_relationships) -- never equivalent to a real constraint. Hidden by default.
  inferred: boolean
  // Present only in the schema_summary view: how many table-level FKs were rolled up
  // into this one schema-to-schema edge.
  relationship_count?: number
}

export interface GraphResponse {
  // Catalogs actually present in this result (not just the configured allow-list).
  catalogs: string[]
  // True when ERD_CATALOGS is unset -- every catalog visible to this deployment's
  // credentials is in scope, not just an explicit allow-list.
  unscoped: boolean
  // The catalog.schema pairs this response was narrowed to, or null if unfiltered
  // (everything in scope).
  pairs: string[] | null
  // "schema_summary" means `nodes` are SchemaNodeData, not TableNodeData -- see
  // SchemaNodeData's doc comment.
  view: 'detail' | 'schema_summary'
  nodes: TableNodeData[] | SchemaNodeData[]
  edges: GraphEdge[]
}

export interface CatalogSchemas {
  catalog: string
  schemas: string[]
}

export interface SchemaTreeResponse {
  catalogs: CatalogSchemas[]
  unscoped: boolean
}

export interface ConfigResponse {
  catalogs: string[] | null
  unscoped: boolean
  // Derived from the deployment's own WorkspaceClient host -- null if it couldn't be
  // resolved (e.g. auth failure), never a hardcoded placeholder.
  workspace: string | null
  // False for an unscoped deployment -- there's no defined (prod) catalog list to
  // derive a test-catalog name from, so the Prod/Test toggle has nothing to switch to.
  test_available: boolean
  // e.g. "_ts" -- appended to each configured catalog name in test mode (edp_customer ->
  // edp_customer_ts). Shown in the toggle's tooltip so it's not a mystery to the user.
  test_catalog_suffix: string
}

export type FilterMode = 'neighbors' | 'component'

export type CatalogEnv = 'prod' | 'test'
