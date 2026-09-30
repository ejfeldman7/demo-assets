import type { GraphResponse } from './types.ts'

export interface ExportScope {
  nodeIds: Set<string>
  edgeIds: Set<string>
}

/**
 * Narrow a graph down to an active click-to-filter selection (a specific table's
 * neighbors/connected component) before handing it to any of the text export formats
 * below -- so "export while a table is selected" produces just that subset, not the
 * whole catalog/schema-scoped graph with the selection ignored.
 *
 * Boundary stub nodes (out-of-view FK targets) are always dropped here: they're
 * on-canvas reference markers, not real tables, and must never appear as empty table
 * definitions in any export.
 *
 * Cross-scope edges (declared FKs whose target is off-canvas) are dropped by default
 * for MD/YAML/JSON exports -- there's no matching table entry to reference. Pass
 * `keepDeclaredCrossScope: true` (ER/Studio DDL) to keep them: the DDL builder can
 * still emit a valid FOREIGN KEY ... REFERENCES clause from the edge's target node ID
 * even without a CREATE TABLE for the target.
 *
 * (Image export takes the live `displayNodes` instead of routing through here, so a
 * WYSIWYG screenshot still includes any stubs the user has toggled on.)
 */
export function scopeGraph(
  graph: GraphResponse,
  scope: ExportScope | null,
  opts?: { keepDeclaredCrossScope?: boolean },
): GraphResponse {
  // graph.nodes is typed as a union of two array types (TableNodeData[] | SchemaNodeData[])
  // rather than an array of a union -- .filter() can't narrow that back cleanly, but
  // every element still has an `id` regardless of which shape it is.
  const modelNodes = (graph.nodes as Array<{ id: string; is_boundary?: boolean }>).filter(
    (n) => !n.is_boundary && (!scope || scope.nodeIds.has(n.id)),
  )
  const modelEdges = graph.edges.filter(
    (e) =>
      (!e.cross_scope || (opts?.keepDeclaredCrossScope && !e.inferred)) &&
      (!scope || scope.edgeIds.has(e.id)),
  )
  return {
    ...graph,
    nodes: modelNodes as GraphResponse['nodes'],
    edges: modelEdges,
  }
}
