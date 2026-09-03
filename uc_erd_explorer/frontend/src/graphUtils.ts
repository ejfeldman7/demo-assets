import type { GraphEdge, SchemaNodeData, TableNodeData } from './types'

// Approximate node dimensions for auto-layout (a card = header + optional tag row + one
// row per column; a collapsed schema card is a fixed size). Consumed by the ELK layout
// (elkLayout.ts) to give each node a footprint, and re-attached to the laid-out node so
// getNodesBounds() (the PNG/SVG export) has a real box per node, not just a point.
// Calibrated against the actual rendered card (TableNode): a column row measures ~25px
// and the header+card chrome ~36px. These MUST NOT under-estimate -- ELK spaces nodes by
// their declared box, so an under-estimate lets tall cards overlap their neighbors (the
// reported bug). A few px of headroom is harmless (it only adds gap).
const NODE_WIDTH = 240
const ROW_HEIGHT = 25
const HEADER_HEIGHT = 36
const TAGS_ROW_HEIGHT = 30
const CARD_PADDING = 4
const SCHEMA_NODE_WIDTH = 220
const SCHEMA_NODE_HEIGHT = 88

function isSchemaNode(data: TableNodeData | SchemaNodeData): data is SchemaNodeData {
  return !('columns' in data)
}

export function nodeSize(data: TableNodeData | SchemaNodeData): { width: number; height: number } {
  if (isSchemaNode(data)) {
    return { width: SCHEMA_NODE_WIDTH, height: SCHEMA_NODE_HEIGHT }
  }
  const tagsHeight = data.tags.length > 0 ? TAGS_ROW_HEIGHT : 0
  return { width: NODE_WIDTH, height: HEADER_HEIGHT + tagsHeight + data.columns.length * ROW_HEIGHT + CARD_PADDING }
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
