// Radial "star" layout: place one center table at the origin and its direct FK neighbors
// evenly around a circle. A deterministic, synchronous alternative to the ELK layout, used
// only for the Star layout mode. Built entirely from nodes/edges already on the diagram --
// no backend, no ELK.
//
// Scope note: this positions the center + its 1-hop neighbors and returns ONLY those nodes.
// Everything else is dropped from the returned set, so the canvas shows just the star. Edge
// filtering is handled downstream (App's displayEdges keeps only edges whose endpoints are
// both present), so nothing here has to touch edges beyond reading adjacency.
import { type Edge, type Node, type Position } from 'reactflow'
import type { TableNodeData } from './types'
// .ts extension (allowed by allowImportingTsExtensions) so this module resolves under the
// node --test runner too, not only the Vite bundler -- see starLayout.test.ts.
import { directNeighbors, nodeSize } from './graphUtils.ts'

// Position is a string enum in React Flow (Left='left', Right='right', ...). We use the
// literals rather than importing the enum VALUE so this module stays free of any reactflow
// runtime import -- which lets it be unit-tested in the node runner without loading the lib.
const LEFT = 'left' as Position
const RIGHT = 'right' as Position

export interface StarLayoutResult {
  nodes: Node<TableNodeData>[]
  centerNodeId: string
}

export function layoutStar(
  centerNodeId: string,
  allNodes: Node<TableNodeData>[],
  allEdges: Edge[],
): StarLayoutResult {
  const center = allNodes.find((n) => n.id === centerNodeId)
  // Defensive: an unknown center id yields an empty star rather than throwing. Callers
  // resolve a valid center first (see suggestStarCenter), so this is a guard, not a path.
  if (!center) return { nodes: [], centerNodeId }

  // directNeighbors returns the 1-hop set INCLUDING the node itself -- drop the center so
  // it isn't also placed on the ring.
  const neighborIds = new Set(directNeighbors(centerNodeId, allEdges))
  neighborIds.delete(centerNodeId)

  const neighbors = allNodes.filter((n) => neighborIds.has(n.id))
  const n = neighbors.length

  // Center: its geometric center sits at the origin, so the ring is symmetric around it.
  const centerSize = nodeSize(center.data)
  const placedCenter: Node<TableNodeData> = {
    ...center,
    position: { x: -centerSize.width / 2, y: -centerSize.height / 2 },
    width: centerSize.width,
    height: centerSize.height,
  }
  if (n === 0) return { nodes: [placedCenter], centerNodeId }

  // Radius scales with neighbor count so cards don't collide as the ring gets crowded (the
  // chord between adjacent slots stays wider than a card). Keys-only mode (forced on in
  // star view) keeps neighbor heights small, so the dominant constraint is card width.
  const radius = Math.max(320, n * 64)
  const step = (2 * Math.PI) / n

  const placedNeighbors = neighbors.map((node, i) => {
    // Start at the top (-pi/2) and go clockwise.
    const angle = -Math.PI / 2 + i * step
    const size = nodeSize(node.data)
    // Position is React Flow's top-left; place each neighbor's CENTER on the ring point.
    const cx = radius * Math.cos(angle)
    const cy = radius * Math.sin(angle)
    // Nodes on the left half present their right edge toward the center, and vice versa --
    // sensible defaults for any edge that isn't pinned to a per-column handle.
    const onRight = Math.cos(angle) >= 0
    return {
      ...node,
      position: { x: cx - size.width / 2, y: cy - size.height / 2 },
      width: size.width,
      height: size.height,
      sourcePosition: onRight ? LEFT : RIGHT,
      targetPosition: onRight ? LEFT : RIGHT,
    }
  })

  return { nodes: [placedCenter, ...placedNeighbors], centerNodeId }
}
