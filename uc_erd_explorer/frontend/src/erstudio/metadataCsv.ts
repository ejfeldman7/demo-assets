import type { GraphResponse, TableNodeData } from '../types'

const HEADER = [
  'catalog',
  'schema',
  'table',
  'column',
  'uc_comment',
  'uc_tags',
  'is_primary_key',
  'is_foreign_key',
  'is_inferred_relationship',
  'inferred_relationship_target',
  'source_system',
]

function csvEscape(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

/**
 * One row per column, for ER/Studio's metadata import alongside physical_model.sql.
 * `is_inferred_relationship`/`inferred_relationship_target` surface server/graph.py's
 * infer_relationships() heuristic guesses here -- and ONLY here, never as a DDL
 * constraint (see ddlBuilder.ts) -- so a modeler can review and decide whether to
 * formalize one, rather than the guess silently becoming a real constraint.
 */
export function buildMetadataCsv(graph: GraphResponse): string {
  const nodes = graph.nodes as TableNodeData[]

  const inferredBySourceColumn = new Map<string, string>()
  for (const edge of graph.edges) {
    if (!edge.inferred) continue
    const targetNode = nodes.find((n) => n.id === edge.target)
    if (!targetNode) continue
    for (const col of edge.fk_columns) {
      inferredBySourceColumn.set(`${edge.source}.${col}`, `${targetNode.catalog}.${targetNode.schema}.${targetNode.table}`)
    }
  }

  const rows: string[][] = [HEADER]
  for (const node of nodes) {
    for (const col of node.columns) {
      const inferredTarget = inferredBySourceColumn.get(`${node.id}.${col.name}`)
      rows.push([
        node.catalog,
        node.schema,
        node.table,
        col.name,
        col.comment ?? '',
        col.tags.map((t) => (t.value && t.value !== 'true' ? `${t.name}:${t.value}` : t.name)).join('; '),
        String(col.is_pk),
        String(col.is_fk),
        String(Boolean(inferredTarget)),
        inferredTarget ?? '',
        `${node.catalog}.${node.schema}.${node.table}`,
      ])
    }
  }
  return rows.map((row) => row.map(csvEscape).join(',')).join('\n')
}
