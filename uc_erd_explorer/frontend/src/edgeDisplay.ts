// Pure, framework-free logic for how relationships render given the current transient
// hover state and persistent selection state. Extracted from App.tsx so the interaction
// rules -- which are the whole point of the "hover to inspect, click to focus" model --
// are unit-testable without a DOM/React Flow harness.
//
// Two state axes, deliberately kept separate:
//   * TRANSIENT hover  -> reveals a relationship's join-key detail (label) and highlights
//                         the two columns it connects. Cleared the instant the pointer
//                         leaves. Never persists.
//   * PERSISTENT select -> a clicked table focuses its neighborhood (dim the rest,
//                         animate its edges). Does NOT reveal labels -- that was the old
//                         behavior we removed, because a hub table's labels overlapped
//                         everything once clicked.

import type { GraphEdge } from './types'

/** The column the pointer is currently over, if any -- a (node, column) pair. */
export interface HoveredKey {
  nodeId: string
  column: string
}

/**
 * Edge ids that a hovered key column participates in. A key is "in" an edge when the
 * hovered node is the edge's FK side and the column is one of its fk_columns, or the
 * hovered node is the PK side and the column is one of its pk_columns. Composite keys
 * (multiple columns) mean one column can light up the whole relationship -- that's
 * intended: you're inspecting the relationship, not a single column in isolation.
 */
export function edgesForKey(key: HoveredKey | null, edges: GraphEdge[]): Set<string> {
  const result = new Set<string>()
  if (!key) return result
  for (const e of edges) {
    const onFkSide = e.source === key.nodeId && e.fk_columns.includes(key.column)
    const onPkSide = e.target === key.nodeId && e.pk_columns.includes(key.column)
    if (onFkSide || onPkSide) result.add(e.id)
  }
  return result
}

/**
 * The set of edges considered "active" (detail revealed) right now: the directly hovered
 * edge plus any edges lit by a hovered key column. Union, because a key hover and an edge
 * hover can't both be live at once in practice, but treating it as a union keeps the
 * function total and order-independent.
 */
export function activeEdgeIds(
  hoveredEdgeId: string | null,
  hoveredKey: HoveredKey | null,
  edges: GraphEdge[],
): Set<string> {
  const ids = edgesForKey(hoveredKey, edges)
  if (hoveredEdgeId) ids.add(hoveredEdgeId)
  return ids
}

/**
 * Which columns to highlight, keyed by node id. For every active edge, both endpoints'
 * columns are highlighted -- so hovering an edge (or one of its key columns) lights up the
 * matching column on the *other* table too, making the join legible at a glance (extra B).
 */
export function highlightedColumnsByNode(
  active: Set<string>,
  edges: GraphEdge[],
): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>()
  const add = (nodeId: string, col: string) => {
    let s = map.get(nodeId)
    if (!s) map.set(nodeId, (s = new Set()))
    s.add(col)
  }
  for (const e of edges) {
    if (!active.has(e.id)) continue
    for (const c of e.fk_columns) add(e.source, c)
    for (const c of e.pk_columns) add(e.target, c)
  }
  return map
}

/**
 * Whether a single edge should render its join-key label. HOVER-ONLY: an edge shows its
 * label iff it is currently active (hovered directly, or via a hovered key). Selection is
 * intentionally NOT a trigger -- this is the core behavior change from the review feedback.
 */
export function shouldShowLabel(edgeId: string, active: Set<string>): boolean {
  return active.has(edgeId)
}

/**
 * Is an edge inside the current click-to-focus selection? An edge is "in set" when there's
 * no selection (visibleSet null => everything shown), or both its endpoints are visible.
 */
export function edgeInSelection(
  edge: Pick<GraphEdge, 'source' | 'target'>,
  visibleSet: Set<string> | null,
): boolean {
  return !visibleSet || (visibleSet.has(edge.source) && visibleSet.has(edge.target))
}

export interface EdgeVisual {
  showLabel: boolean
  opacity: number
  /** true when de-emphasized (outside the active selection) -> render greyed. */
  dimmed: boolean
  animated: boolean
}

/**
 * Full visual state for one edge given hover + selection. Kept pure and returning plain
 * data (not React Flow style objects) so tests can assert on it directly; App.tsx maps
 * this onto concrete stroke colors, which depend on inferred-ness handled at the call site.
 */
export function computeEdgeVisual(params: {
  edge: Pick<GraphEdge, 'id' | 'source' | 'target'>
  active: Set<string>
  visibleSet: Set<string> | null
  hasSelection: boolean
}): EdgeVisual {
  const { edge, active, visibleSet, hasSelection } = params
  const inSet = edgeInSelection(edge, visibleSet)
  return {
    showLabel: shouldShowLabel(edge.id, active),
    opacity: inSet ? 1 : 0.1,
    dimmed: !inSet,
    animated: hasSelection && inSet,
  }
}

// -- Join-key label formatting -------------------------------------------------------

/**
 * When the combined "fk → pk" mapping is short it reads fine on one line. Past a threshold
 * (long or composite keys), a single nowrap line becomes a wide bar that overhangs
 * neighboring tables -- so we stack it: FK columns on top, PK columns below, connected by a
 * vertical arrow (per the review's explicit tooltip requirement).
 */
// Composite keys always stack (see below); this only governs single-column mappings with
// long names. Set above a typical same-name single-column join (e.g.
// "customer_id → customer_id", ~25 chars) so those stay on one tidy line, and only a
// genuinely long name stacks before it would wrap inside the label's max-width.
export const LABEL_STACK_THRESHOLD = 30

export interface JoinLabel {
  fk: string
  pk: string
  inferred: boolean
  /** true => render stacked (fk over pk, vertical arrow); false => single inline line. */
  stacked: boolean
}

export function formatJoinLabel(
  fkCols: string[],
  pkCols: string[],
  inferred: boolean,
): JoinLabel {
  const fk = fkCols.join(', ')
  const pk = pkCols.join(', ')
  // Stack when either side is composite, or the inline "fk → pk" would be long.
  const inlineLen = fk.length + pk.length + 3 // " → "
  const stacked =
    fkCols.length > 1 || pkCols.length > 1 || inlineLen > LABEL_STACK_THRESHOLD
  return { fk, pk, inferred, stacked }
}
