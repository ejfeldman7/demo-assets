import { Position, type Edge, type Node } from 'reactflow'
import { nodeSize } from './graphUtils'
import type { SchemaNodeData, TableNodeData } from './types'

// Minimal shape of what we use from elkjs, so this module stays typed without a static
// import of the (very large) bundle.
interface ElkLaidOutNode {
  id: string
  x?: number
  y?: number
  width?: number
  height?: number
}
interface ElkInstance {
  layout(graph: unknown): Promise<{ children?: ElkLaidOutNode[] }>
}

// elk.bundled.js is ~1.3MB -- kept OFF the initial bundle via a dynamic import (same
// pattern as the export code). The chunk downloads in parallel with the initial /api/graph
// fetch (which waits on a warehouse query), so it's usually ready by the time there are
// nodes to lay out and adds no perceptible latency. Instantiated once and reused.
let elkPromise: Promise<ElkInstance> | null = null
function getElk(): Promise<ElkInstance> {
  if (!elkPromise) {
    elkPromise = import('elkjs/lib/elk.bundled.js').then(
      (m) => new (m.default as new () => ElkInstance)(),
    )
  }
  return elkPromise
}

// ELK (Eclipse Layout Kernel) replaces dagre for node placement. Its layered algorithm
// does stronger crossing minimization on dense schemas, and -- the reason we adopt it now
// -- it supports compound/nested nodes, which the upcoming catalog/schema grouping needs
// (dagre can't nest). Layout is async (elk.layout returns a Promise), so App runs it in an
// effect and holds the result in state.
//
// Edges still render via React Flow's per-column-handle smoothstep routing (the crow's-foot
// markers depend on the Left/Right handle geometry), so we only take ELK's node positions,
// not its edge routes.

export type LayoutDirection = 'LR' | 'TB'

const LAYOUT_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  // Gap between successive layers (the "rank" spacing) and between siblings in a layer --
  // loose enough that cards don't crowd and a hovered detail box has room to sit between
  // them without overhanging a neighbor.
  'elk.layered.spacing.nodeNodeBetweenLayers': '130',
  'elk.spacing.nodeNode': '70',
  'elk.layered.spacing.edgeNodeBetweenLayers': '40',
  'elk.edgeRouting': 'ORTHOGONAL',
  // Keep a stable, input-order-influenced placement so the layout doesn't reshuffle
  // wildly between reloads of the same schema.
  'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
}

export async function layoutGraphElk(
  nodes: Node<TableNodeData | SchemaNodeData>[],
  edges: Edge[],
  direction: LayoutDirection,
): Promise<Node<TableNodeData | SchemaNodeData>[]> {
  if (nodes.length === 0) return []

  const graph = {
    id: 'root',
    layoutOptions: { ...LAYOUT_OPTIONS, 'elk.direction': direction === 'LR' ? 'RIGHT' : 'DOWN' },
    children: nodes.map((n) => {
      const { width, height } = nodeSize(n.data)
      return { id: n.id, width, height }
    }),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }

  const elk = await getElk()
  const laid = await elk.layout(graph)
  const byId = new Map((laid.children ?? []).map((c) => [c.id, c]))

  // Handle sides follow the flow direction for schema-summary (centered-handle) edges; the
  // per-column detail handles stay Left/Right regardless (see TableNode), which is also
  // what keeps the crow's-foot marker geometry valid in either direction.
  const sourcePosition = direction === 'LR' ? Position.Right : Position.Bottom
  const targetPosition = direction === 'LR' ? Position.Left : Position.Top

  return nodes.map((n) => {
    const c = byId.get(n.id)
    const { width, height } = nodeSize(n.data)
    return {
      ...n,
      sourcePosition,
      targetPosition,
      // Explicit width/height on the node so getNodesBounds() (PNG/SVG export) has a real
      // footprint per node. ELK's x/y are already top-left (no center-offset needed).
      width,
      height,
      position: { x: c?.x ?? 0, y: c?.y ?? 0 },
    }
  })
}
