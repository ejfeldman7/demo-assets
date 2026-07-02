import dagre from 'dagre'
import type { Node, Edge } from 'reactflow'
import { Position } from 'reactflow'
import type { GraphEdge, TableNodeData } from './types'

// Approximate node dimensions for dagre (a card = header + one row per column).
const NODE_WIDTH = 240
const ROW_HEIGHT = 22
const HEADER_HEIGHT = 40
const CARD_PADDING = 12

function nodeHeight(data: TableNodeData): number {
  return HEADER_HEIGHT + data.columns.length * ROW_HEIGHT + CARD_PADDING
}

/**
 * Run dagre auto-layout. Left-to-right reads more clearly than top-down for
 * these ~22 wide table cards, and keeps the two schemas visually flowing.
 */
export function layoutGraph(
  nodes: Node<TableNodeData>[],
  edges: Edge[],
): Node<TableNodeData>[] {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_WIDTH, height: nodeHeight(n.data) })
  })
  edges.forEach((e) => {
    g.setEdge(e.source, e.target)
  })

  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    return {
      ...n,
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      // dagre gives center; React Flow wants top-left.
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - nodeHeight(n.data) / 2,
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
