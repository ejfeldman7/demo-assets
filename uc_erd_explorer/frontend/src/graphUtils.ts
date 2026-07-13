import dagre from 'dagre'
import type { Node, Edge } from 'reactflow'
import { Position } from 'reactflow'
import type { GraphEdge, SchemaNodeData, TableNodeData } from './types'

// Approximate node dimensions for dagre (a card = header + optional tag row + one row
// per column; a collapsed schema card is a fixed size).
const NODE_WIDTH = 240
const ROW_HEIGHT = 22
const HEADER_HEIGHT = 40
const TAGS_ROW_HEIGHT = 28
const CARD_PADDING = 12
const SCHEMA_NODE_WIDTH = 220
const SCHEMA_NODE_HEIGHT = 84

function isSchemaNode(data: TableNodeData | SchemaNodeData): data is SchemaNodeData {
  return !('columns' in data)
}

function nodeSize(data: TableNodeData | SchemaNodeData): { width: number; height: number } {
  if (isSchemaNode(data)) {
    return { width: SCHEMA_NODE_WIDTH, height: SCHEMA_NODE_HEIGHT }
  }
  const tagsHeight = data.tags.length > 0 ? TAGS_ROW_HEIGHT : 0
  return { width: NODE_WIDTH, height: HEADER_HEIGHT + tagsHeight + data.columns.length * ROW_HEIGHT + CARD_PADDING }
}

/**
 * Run dagre auto-layout. Left-to-right reads more clearly than top-down for
 * these ~22 wide table cards, and keeps the two schemas visually flowing.
 */
export function layoutGraph(
  nodes: Node<TableNodeData | SchemaNodeData>[],
  edges: Edge[],
): Node<TableNodeData | SchemaNodeData>[] {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))

  nodes.forEach((n) => {
    const { width, height } = nodeSize(n.data)
    g.setNode(n.id, { width, height })
  })
  edges.forEach((e) => {
    g.setEdge(e.source, e.target)
  })

  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    const { width, height } = nodeSize(n.data)
    return {
      ...n,
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      // Explicit width/height, not just position -- getNodesBounds() (used by the PNG/
      // SVG export) needs these on the node object itself to compute an accurate
      // bounding box. Without them it has no footprint per node to work with, only a
      // point, which undersizes the exported image and clips the far/bottom edge of
      // whatever nodes happen to be at the layout's extremes.
      width,
      height,
      // dagre gives center; React Flow wants top-left.
      position: {
        x: pos.x - width / 2,
        y: pos.y - height / 2,
      },
    }
  })
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
