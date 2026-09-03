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
  children?: ElkLaidOutNode[] // present for compound (group) nodes in grouped layout
}
interface ElkInstance {
  layout(graph: unknown): Promise<{ children?: ElkLaidOutNode[] }>
}

/** A schema container drawn behind its tables in grouped mode (see App). */
export interface GroupBox {
  id: string // "group:<catalog>.<schema>"
  catalog: string
  schema: string
  count: number
  x: number
  y: number
  width: number
  height: number
}

export interface LayoutResult {
  nodes: Node<TableNodeData | SchemaNodeData>[]
  groups: GroupBox[]
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
  groupBySchema = false,
): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes: [], groups: [] }

  // Handle sides follow the flow direction for schema-summary (centered-handle) edges; the
  // per-column detail handles stay Left/Right regardless (see TableNode), which is also
  // what keeps the crow's-foot marker geometry valid in either direction.
  const sourcePosition = direction === 'LR' ? Position.Right : Position.Bottom
  const targetPosition = direction === 'LR' ? Position.Left : Position.Top
  const dir = direction === 'LR' ? 'RIGHT' : 'DOWN'
  const elk = await getElk()

  const applyPositions = (posById: Map<string, { x: number; y: number }>) =>
    nodes.map((n) => {
      const { width, height } = nodeSize(n.data)
      const p = posById.get(n.id) ?? { x: 0, y: 0 }
      // Explicit width/height so getNodesBounds() (PNG/SVG export) has a real footprint.
      return { ...n, sourcePosition, targetPosition, width, height, position: p }
    })

  if (!groupBySchema) {
    const graph = {
      id: 'root',
      layoutOptions: { ...LAYOUT_OPTIONS, 'elk.direction': dir },
      children: nodes.map((n) => {
        const { width, height } = nodeSize(n.data)
        return { id: n.id, width, height }
      }),
      edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    }
    const laid = await elk.layout(graph)
    const posById = new Map((laid.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]))
    return { nodes: applyPositions(posById), groups: [] }
  }

  // Grouped: cluster tables into a compound node per catalog.schema. ELK lays out each
  // group's tables within a padded box (the padding leaves room for the header we draw),
  // and positions the groups relative to each other; INCLUDE_CHILDREN lets it route the
  // cross-group FK edges. We take back each group's box + each table's ABSOLUTE position
  // (group origin + child offset) -- tables stay top-level React Flow nodes, so the rest of
  // the app (column push, edges, selection) is untouched; the box is a separate render node.
  const byGroup = new Map<string, Node<TableNodeData | SchemaNodeData>[]>()
  for (const n of nodes) {
    const key = 'columns' in n.data ? `${n.data.catalog}.${n.data.schema}` : `__ungrouped.${n.id}`
    let list = byGroup.get(key)
    if (!list) byGroup.set(key, (list = []))
    list.push(n)
  }
  const graph = {
    id: 'root',
    // The schema boxes are mostly disconnected (FKs usually live within a schema), so the
    // layered algorithm would stack them in one tall column. rectpacking instead packs the
    // boxes into a compact grid; each group lays its own tables out with layered/direction.
    layoutOptions: {
      'elk.algorithm': 'rectpacking',
      'elk.spacing.nodeNode': '48',
      'elk.aspectRatio': '1.7',
    },
    children: [...byGroup.entries()].map(([key, tables]) => ({
      id: `group:${key}`,
      layoutOptions: {
        ...LAYOUT_OPTIONS,
        'elk.direction': dir,
        'elk.padding': '[top=42.0,left=16.0,bottom=16.0,right=16.0]',
      },
      children: tables.map((n) => {
        const { width, height } = nodeSize(n.data)
        return { id: n.id, width, height }
      }),
    })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }
  const laid = await elk.layout(graph)
  const posById = new Map<string, { x: number; y: number }>()
  const groups: GroupBox[] = []
  for (const g of laid.children ?? []) {
    const gx = g.x ?? 0
    const gy = g.y ?? 0
    for (const c of g.children ?? []) posById.set(c.id, { x: gx + (c.x ?? 0), y: gy + (c.y ?? 0) })
    const key = g.id.replace(/^group:/, '')
    const dot = key.indexOf('.')
    groups.push({
      id: g.id,
      catalog: dot >= 0 ? key.slice(0, dot) : key,
      schema: dot >= 0 ? key.slice(dot + 1) : '',
      count: (g.children ?? []).length,
      x: gx,
      y: gy,
      width: g.width ?? 0,
      height: g.height ?? 0,
    })
  }
  return { nodes: applyPositions(posById), groups }
}
