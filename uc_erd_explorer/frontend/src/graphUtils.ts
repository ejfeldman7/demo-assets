import type { ColumnMeta, GraphEdge, SchemaNodeData, TableNodeData } from './types'

// Max column rows a card shows before capping. Beyond this, a card would grow taller than
// the viewport and blow up the auto-layout's bounding box (fit-view shrinks everything,
// export/minimap balloon). A "+N more columns" footer expands it on demand. Chosen ~12 to
// match how Lucidchart/Vertabelo cap wide tables.
export const COLUMN_CAP = 12
const FOOTER_HEIGHT = 26

/**
 * The columns a card actually renders, ordered PK -> FK -> rest (stable within each group),
 * capped at COLUMN_CAP unless expanded. Ordering PK/FK first is what guarantees the edge
 * anchor columns (fk_columns[0]/pk_columns[0]) survive the cap, so relationship lines never
 * lose their handle. Returns the visible slice plus how many were hidden.
 */
export function visibleColumns(
  columns: ColumnMeta[],
  expanded: boolean,
): { visible: ColumnMeta[]; hidden: number } {
  const rank = (c: ColumnMeta) => (c.is_pk ? 0 : c.is_fk ? 1 : 2)
  const sorted = columns
    .map((c, i) => ({ c, i }))
    .sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i)
    .map((x) => x.c)
  if (expanded || sorted.length <= COLUMN_CAP) return { visible: sorted, hidden: 0 }
  return { visible: sorted.slice(0, COLUMN_CAP), hidden: sorted.length - COLUMN_CAP }
}

// Approximate node dimensions for auto-layout (a card = header + optional tag row + one
// row per column; a collapsed schema card is a fixed size). Consumed by the ELK layout
// (elkLayout.ts) to give each node a footprint, and re-attached to the laid-out node so
// getNodesBounds() (the PNG/SVG export) has a real box per node, not just a point.
// Calibrated against the actual rendered card (TableNode): a column row measures ~25px
// and the header+card chrome ~36px. These MUST NOT under-estimate -- ELK spaces nodes by
// their declared box, so an under-estimate lets tall cards overlap their neighbors (the
// reported bug). A few px of headroom is harmless (it only adds gap).
const NODE_WIDTH = 240
export const ROW_HEIGHT = 25
const HEADER_HEIGHT = 36
const TAGS_ROW_HEIGHT = 30
const CARD_PADDING = 4
const SCHEMA_NODE_WIDTH = 220
const SCHEMA_NODE_HEIGHT = 88

function isSchemaNode(data: TableNodeData | SchemaNodeData): data is SchemaNodeData {
  return !('columns' in data)
}

export function nodeSize(
  data: (TableNodeData | SchemaNodeData) & { hasColumnFooter?: boolean },
): { width: number; height: number } {
  if (isSchemaNode(data)) {
    return { width: SCHEMA_NODE_WIDTH, height: SCHEMA_NODE_HEIGHT }
  }
  const tagsHeight = data.tags.length > 0 ? TAGS_ROW_HEIGHT : 0
  // The "+N more / show fewer" footer is part of the card, so its height must be in the
  // box ELK lays out around -- otherwise it'd overlap the card below.
  const footerHeight = data.hasColumnFooter ? FOOTER_HEIGHT : 0
  return {
    width: NODE_WIDTH,
    height: HEADER_HEIGHT + tagsHeight + data.columns.length * ROW_HEIGHT + footerHeight + CARD_PADDING,
  }
}

/** Build an undirected adjacency map from the edge list. */
function buildAdjacency(edges: GraphEdge[]): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>()
  const add = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, new Set())
    adj.get(a)!.add(b)
  }
  edges.forEach((e) => {
    add(e.source, e.target)
    add(e.target, e.source)
  })
  return adj
}

/** Direct neighbors (1-hop, either FK or PK direction) plus the node itself. */
export function directNeighbors(nodeId: string, edges: GraphEdge[]): Set<string> {
  const adj = buildAdjacency(edges)
  const result = new Set<string>([nodeId])
  ;(adj.get(nodeId) ?? new Set()).forEach((n) => result.add(n))
  return result
}

/**
 * Shortest join path between two tables over the (undirected) relationship graph -- "how
 * does A connect to B?". BFS, so the fewest hops. Returns the node ids along the path and
 * the edge ids linking them, or null if they're in different connected components.
 */
export function shortestPath(
  sourceId: string,
  targetId: string,
  edges: GraphEdge[],
): { nodeIds: string[]; edgeIds: string[] } | null {
  if (sourceId === targetId) return { nodeIds: [sourceId], edgeIds: [] }
  const adj = new Map<string, { to: string; edgeId: string }[]>()
  const link = (a: string, b: string, id: string) => {
    let l = adj.get(a)
    if (!l) adj.set(a, (l = []))
    l.push({ to: b, edgeId: id })
  }
  for (const e of edges) {
    link(e.source, e.target, e.id)
    link(e.target, e.source, e.id)
  }
  const prev = new Map<string, { node: string; edgeId: string }>()
  const visited = new Set<string>([sourceId])
  const queue = [sourceId]
  while (queue.length > 0) {
    const cur = queue.shift()!
    if (cur === targetId) break
    for (const { to, edgeId } of adj.get(cur) ?? []) {
      if (!visited.has(to)) {
        visited.add(to)
        prev.set(to, { node: cur, edgeId })
        queue.push(to)
      }
    }
  }
  if (!visited.has(targetId)) return null
  const nodeIds = [targetId]
  const edgeIds: string[] = []
  let cur = targetId
  while (cur !== sourceId) {
    const p = prev.get(cur)!
    edgeIds.push(p.edgeId)
    nodeIds.push(p.node)
    cur = p.node
  }
  nodeIds.reverse()
  edgeIds.reverse()
  return { nodeIds, edgeIds }
}

/** Full connected component (BFS transitive closure) containing the node. */
export function connectedComponent(nodeId: string, edges: GraphEdge[]): Set<string> {
  const adj = buildAdjacency(edges)
  const visited = new Set<string>([nodeId])
  const queue = [nodeId]
  while (queue.length > 0) {
    const cur = queue.shift()!
    ;(adj.get(cur) ?? new Set()).forEach((next) => {
      if (!visited.has(next)) {
        visited.add(next)
        queue.push(next)
      }
    })
  }
  return visited
}
