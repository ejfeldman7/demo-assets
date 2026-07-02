export interface ColumnMeta {
  name: string
  type: string
  is_pk: boolean
  is_fk: boolean
}

export interface TableNodeData {
  id: string
  catalog: string
  schema: string
  table: string
  columns: ColumnMeta[]
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  fk_columns: string[]
  pk_columns: string[]
  constraint_name: string
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
  nodes: TableNodeData[]
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
}

export type FilterMode = 'neighbors' | 'component'
