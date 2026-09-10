import { Position, type Edge, type Node } from 'reactflow'
import { connectedComponent, nodeSize } from './graphUtils'
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
  collapsed: boolean
}

// A collapsed schema box is just its header -- a small fixed leaf in the layout.
const COLLAPSED_W = 260
const COLLAPSED_H = 46

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
// How tables are clustered into boxes: not at all, one box per catalog.schema, or one
// box per catalog (all its schemas' tables together -- for multi-catalog overviews).
export type GroupBy = 'none' | 'schema' | 'catalog'

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
  groupBy: GroupBy = 'none',
  collapsed: Set<string> = new Set(),
): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes: [], groups: [] }

  // Handle sides follow the flow direction for schema-summary (centered-handle) edges; the
  // per-column detail handles stay Left/Right regardless (see TableNode), which is also
  // what keeps the crow's-foot marker geometry valid in either direction.
  const sourcePosition = direction === 'LR' ? Position.Right : Position.Bottom
  const targetPosition = direction === 'LR' ? Position.Left : Position.Top
  const dir = direction === 'LR' ? 'RIGHT' : 'DOWN'
  const elk = await getElk()

  const applyPositions = (
    posById: Map<string, { x: number; y: number }>,
    list: Node<TableNodeData | SchemaNodeData>[],
  ) =>
    list.map((n) => {
      const { width, height } = nodeSize(n.data)
      const p = posById.get(n.id) ?? { x: 0, y: 0 }
      // Explicit width/height so getNodesBounds() (PNG/SVG export) has a real footprint.
      return { ...n, sourcePosition, targetPosition, width, height, position: p }
    })

  if (groupBy === 'none') {
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
    return { nodes: applyPositions(posById, nodes), groups: [] }
  }

  // Grouped: cluster tables into a compound node per catalog.schema. ELK lays out each
  // group's tables within a padded box (the padding leaves room for the header we draw),
  // and positions the groups relative to each other; INCLUDE_CHILDREN lets it route the
  // cross-group FK edges. We take back each group's box + each table's ABSOLUTE position
  // (group origin + child offset) -- tables stay top-level React Flow nodes, so the rest of
  // the app (column push, edges, selection) is untouched; the box is a separate render node.
  const byGroup = new Map<string, Node<TableNodeData | SchemaNodeData>[]>()
  const groupIdOf = new Map<string, string>() // table id -> its group id
  for (const n of nodes) {
    const key = !('columns' in n.data)
      ? `__ungrouped.${n.id}`
      : groupBy === 'catalog'
        ? n.data.catalog
        : `${n.data.catalog}.${n.data.schema}`
    let list = byGroup.get(key)
    if (!list) byGroup.set(key, (list = []))
    list.push(n)
    groupIdOf.set(n.id, `group:${key}`)
  }
  const isCollapsed = (groupId: string) => collapsed.has(groupId)

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
    children: [...byGroup.entries()].map(([key, tables]) => {
      const gid = `group:${key}`
      // A collapsed group is a fixed-size leaf (header only) -- its tables are omitted.
      if (isCollapsed(gid)) return { id: gid, width: COLLAPSED_W, height: COLLAPSED_H }
      return {
        id: gid,
        layoutOptions: {
          ...LAYOUT_OPTIONS,
          'elk.direction': dir,
          'elk.padding': '[top=42.0,left=16.0,bottom=16.0,right=16.0]',
        },
        children: tables.map((n) => {
          const { width, height } = nodeSize(n.data)
          return { id: n.id, width, height }
        }),
      }
    }),
    // Drop edges touching a collapsed group's (now-hidden) tables; the rest lay out as usual.
    edges: edges
      .filter((e) => !isCollapsed(groupIdOf.get(e.source) ?? '') && !isCollapsed(groupIdOf.get(e.target) ?? ''))
      .map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }
  const laid = await elk.layout(graph)
  const posById = new Map<string, { x: number; y: number }>()
  const groups: GroupBox[] = []
  for (const g of laid.children ?? []) {
    const gx = g.x ?? 0
    const gy = g.y ?? 0
    const collapsedFlag = isCollapsed(g.id)
    if (!collapsedFlag) for (const c of g.children ?? []) posById.set(c.id, { x: gx + (c.x ?? 0), y: gy + (c.y ?? 0) })
    const key = g.id.replace(/^group:/, '')
    const dot = key.indexOf('.')
    groups.push({
      id: g.id,
      catalog: dot >= 0 ? key.slice(0, dot) : key,
      schema: dot >= 0 ? key.slice(dot + 1) : '',
      count: byGroup.get(key)?.length ?? 0,
      x: gx,
      y: gy,
      width: g.width ?? 0,
      height: g.height ?? 0,
      collapsed: collapsedFlag,
    })
  }
  // Only visible tables (collapsed groups' tables are hidden) become React Flow nodes.
  const visibleTables = nodes.filter((n) => !isCollapsed(groupIdOf.get(n.id) ?? ''))
  return { nodes: applyPositions(posById, visibleTables), groups }
}

// Galaxy (fact-constellation) layout of the ENTIRE connected component containing `centerId`.
// Placement is FACT-AWARE and deterministic (not a generic force/stress blob), so it reads
// at a glance as "several related stars" rather than an arbitrary graph:
//   - facts are spread around a ring (kept spatially distinct and separated),
//   - each fact's PRIVATE dims (touching only that fact) fan out on the arc facing AWAY from
//     the galaxy center, so each fact reads as its own star,
//   - SHARED dims (touching 2+ facts) sit at the centroid of the facts they connect, i.e.
//     between them (conformed dims stay central to their facts),
//   - snowflake sub-dims / dim-only bridges (touching NO fact) are outriggers just beyond
//     their placed neighbor, pushed further out.
// Shared dims appear ONCE. Optimizes for communicating the model, not a mathematically
// optimal graph. Falls back to an organic stress layout when no facts are detected (a
// non-star component), so it never forces a fake structure or breaks.
export async function layoutGalaxyElk(
  nodes: Node<TableNodeData | SchemaNodeData>[],
  edges: Edge[],
  centerId: string,
  factIds: Set<string>,
): Promise<LayoutResult> {
  if (nodes.length === 0) return { nodes: [], groups: [] }
  const component = connectedComponent(centerId, edges)
  const members = nodes.filter((n) => component.has(n.id))
  if (members.length === 0) return { nodes: [], groups: [] }
  const memberIds = new Set(members.map((n) => n.id))
  const memberEdges = edges.filter((e) => memberIds.has(e.source) && memberIds.has(e.target))

  const facts = members.filter((n) => factIds.has(n.id))
  if (facts.length === 0) return stressGalaxy(members, memberEdges) // non-star -> organic fallback

  // Undirected adjacency among members.
  const adj = new Map<string, Set<string>>()
  for (const n of members) adj.set(n.id, new Set())
  for (const e of memberEdges) {
    adj.get(e.source)!.add(e.target)
    adj.get(e.target)!.add(e.source)
  }
  const factIdSet = new Set(facts.map((f) => f.id))
  const adjacentFacts = (id: string) => [...(adj.get(id) ?? [])].filter((n) => factIdSet.has(n))

  const pos = new Map<string, { x: number; y: number }>()
  const F = facts.length

  // Phase 1: facts on a TIGHT inner ring (galaxy center = origin). Keeping facts inner lets
  // the dimensions spread to a roomy outer ring, so relationships radiate outward into
  // distinct lanes instead of all converging through the center.
  const Rf = F <= 1 ? 0 : Math.max(340, F * 120)
  const factAngle = new Map<string, number>()
  facts.forEach((f, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / F
    factAngle.set(f.id, a)
    pos.set(f.id, F === 1 ? { x: 0, y: 0 } : { x: Rf * Math.cos(a), y: Rf * Math.sin(a) })
  })

  // Phase 2: bucket non-fact members. private = touches one fact; shared = 2+ facts;
  // orphan = touches no fact directly (snowflake sub-dim / dim-only bridge).
  const privateOf = new Map<string, string[]>()
  for (const f of facts) privateOf.set(f.id, [])
  const shared: string[] = []
  const orphans: string[] = []
  for (const n of members) {
    if (factIdSet.has(n.id)) continue
    const fa = adjacentFacts(n.id)
    if (fa.length === 0) orphans.push(n.id)
    else if (fa.length === 1) privateOf.get(fa[0])!.push(n.id)
    else shared.push(n.id)
  }

  // Gap-center angles between adjacent facts -- a dim shared by ALL facts (no single natural
  // direction) drops into its own gap so it still gets a distinct lane rather than the center.
  const sortedAngles = [...factAngle.values()].sort((a, b) => a - b)
  const gapCenters: number[] = []
  for (let i = 0; i < F && F >= 2; i++) {
    const a1 = sortedAngles[i]
    const a2 = i === F - 1 ? sortedAngles[0] + 2 * Math.PI : sortedAngles[i + 1]
    gapCenters.push((a1 + a2) / 2)
  }
  let gapTurn = 0

  // Phase 3: preferred outward angle for each fact-connected dim -- toward the fact it serves
  // (private), the circular mean of its facts (shared by some), or a gap lane (shared by all).
  const outer = [...shared, ...facts.flatMap((f) => privateOf.get(f.id)!)]
  const preferred = new Map<string, number>()
  outer.forEach((id, idx) => {
    if (F < 2) {
      preferred.set(id, -Math.PI / 2 + (idx / Math.max(outer.length, 1)) * 2 * Math.PI)
      return
    }
    const fa = adjacentFacts(id)
    if (fa.length >= F) {
      preferred.set(id, gapCenters[gapTurn++ % gapCenters.length])
      return
    }
    let sx = 0
    let sy = 0
    for (const f of fa) {
      sx += Math.cos(factAngle.get(f)!)
      sy += Math.sin(factAngle.get(f)!)
    }
    preferred.set(id, Math.hypot(sx, sy) < 1e-6 ? gapCenters[gapTurn++ % gapCenters.length] : Math.atan2(sy, sx))
  })

  // Outer ring radius: deliberately roomy (optimize for lane separation, not shortest edges).
  const Rd = Rf + Math.max(520, outer.length * 48)
  // Distribute the outer nodes EVENLY around the full circle, preserving their preferred-angle
  // ORDER. Even spacing gives every dim an equal lane and uses the whole perimeter (so the
  // dims don't bunch into one arc), while order preservation keeps dims that serve the same
  // facts adjacent -- separability and topology readability over shortest-edge.
  const sortedOuter = [...outer].sort((a, b) => preferred.get(a)! - preferred.get(b)!)
  const start = sortedOuter.length ? preferred.get(sortedOuter[0])! : 0
  sortedOuter.forEach((id, k) => {
    const a = start + (k * 2 * Math.PI) / Math.max(sortedOuter.length, 1)
    pos.set(id, { x: Rd * Math.cos(a), y: Rd * Math.sin(a) })
  })

  // Phase 4: orphans (snowflake sub-dims / dim-only bridges) as outriggers just beyond a
  // placed neighbor, pushed further out from the galaxy center. A few passes handle chains.
  for (let pass = 0; pass < 4; pass++) {
    for (const id of orphans) {
      if (pos.has(id)) continue
      const placed = [...(adj.get(id) ?? [])].find((n) => pos.has(n))
      if (!placed) continue
      const np = pos.get(placed)!
      const a = Math.atan2(np.y, np.x) // outward from the galaxy center (~origin)
      pos.set(id, { x: np.x + 360 * Math.cos(a), y: np.y + 360 * Math.sin(a) })
    }
  }
  for (const n of members) if (!pos.has(n.id)) pos.set(n.id, { x: 0, y: 0 })

  // Final pass: separate any overlapping cards (e.g. an outrigger landing on its parent, or
  // two central shared dims colliding). Nudges centers apart along the axis of least overlap
  // so the intentional structure is preserved -- just de-collided.
  const sizes = new Map(members.map((n) => [n.id, nodeSize(n.data)]))
  resolveOverlaps(members.map((n) => n.id), pos, sizes, 44)

  // Position is React Flow's top-left; center each card on its computed point.
  const positioned = members.map((n) => {
    const { width, height } = nodeSize(n.data)
    const p = pos.get(n.id)!
    return { ...n, width, height, position: { x: p.x - width / 2, y: p.y - height / 2 } }
  })
  return { nodes: positioned, groups: [] }
}

// Iterative axis-aligned overlap removal on a map of node CENTERS. For each overlapping pair
// (bounding boxes closer than half their combined size + margin), push both apart along the
// axis where they overlap least (the smaller nudge, least disruptive to the layout). A few
// dozen passes converge for the small node counts a star/galaxy shows; stops early once a
// pass moves nothing. Deterministic (fixed pair order).
function resolveOverlaps(
  ids: string[],
  pos: Map<string, { x: number; y: number }>,
  sizes: Map<string, { width: number; height: number }>,
  margin: number,
  iterations = 60,
): void {
  for (let it = 0; it < iterations; it++) {
    let moved = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i])!
        const b = pos.get(ids[j])!
        const sa = sizes.get(ids[i])!
        const sb = sizes.get(ids[j])!
        const minDX = (sa.width + sb.width) / 2 + margin
        const minDY = (sa.height + sb.height) / 2 + margin
        const dx = b.x - a.x
        const dy = b.y - a.y
        const ox = minDX - Math.abs(dx)
        const oy = minDY - Math.abs(dy)
        if (ox > 0 && oy > 0) {
          if (ox <= oy) {
            const push = (ox / 2 + 1) * (dx >= 0 ? 1 : -1)
            a.x -= push
            b.x += push
          } else {
            const push = (oy / 2 + 1) * (dy >= 0 ? 1 : -1)
            a.y -= push
            b.y += push
          }
          moved = true
        }
      }
    }
    if (!moved) break
  }
}

// Organic ELK stress/force fallback for a non-star component (no facts to anchor a
// constellation). Deterministic; force is the fallback if a build lacks stress.
async function stressGalaxy(
  members: Node<TableNodeData | SchemaNodeData>[],
  memberEdges: Edge[],
): Promise<LayoutResult> {
  const elk = await getElk()
  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'org.eclipse.elk.stress',
      'elk.stress.desiredEdgeLength': '300',
      'elk.spacing.nodeNode': '70',
    },
    children: members.map((n) => {
      const { width, height } = nodeSize(n.data)
      return { id: n.id, width, height }
    }),
    edges: memberEdges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  }
  let laid: { children?: ElkLaidOutNode[] }
  try {
    laid = await elk.layout(graph)
  } catch {
    laid = await elk.layout({ ...graph, layoutOptions: { 'elk.algorithm': 'org.eclipse.elk.force', 'elk.spacing.nodeNode': '80' } })
  }
  const posById = new Map((laid.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]))
  const positioned = members.map((n) => {
    const { width, height } = nodeSize(n.data)
    return { ...n, width, height, position: posById.get(n.id) ?? { x: 0, y: 0 } }
  })
  return { nodes: positioned, groups: [] }
}
