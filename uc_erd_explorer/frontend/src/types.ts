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
  // Backend returns the in-scope catalog allow-list (plural). `schemas` may be null
  // when no schema filter was applied. The UI reads node-level catalog/schema, so
  // these top-level fields are informational only.
  catalogs?: string[]
  schemas?: string[] | null
  nodes: TableNodeData[]
  edges: GraphEdge[]
}

export type SchemaFilter = 'factory' | 'erp' | 'both'
export type FilterMode = 'neighbors' | 'component'
