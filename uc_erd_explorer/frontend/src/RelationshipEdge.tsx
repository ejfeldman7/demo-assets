import { useCallback } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  getSmoothStepPath,
  getStraightPath,
  useStore,
  type EdgeProps,
  type ReactFlowState,
} from 'reactflow'
import { formatJoinLabel } from './edgeDisplay'

// Minimal shape of a React Flow internal node we read for floating-edge geometry.
interface FloatingNode {
  width?: number | null
  height?: number | null
  positionAbsolute?: { x: number; y: number }
  position?: { x: number; y: number }
}

// Where the straight line from `node`'s center to `other`'s center crosses `node`'s border.
// Standard React Flow floating-edge intersection math -- lets an edge attach to the side of
// a card that FACES the other card (used in Star/Galaxy, where cards sit in all directions),
// instead of the fixed Left/Right per-column handles that force long wrap-around routes.
function nodeIntersection(node: FloatingNode, other: FloatingNode): { x: number; y: number } {
  const w = node.width ?? 240
  const h = node.height ?? 100
  const p = node.positionAbsolute ?? node.position ?? { x: 0, y: 0 }
  const ow = other.width ?? 240
  const oh = other.height ?? 100
  const op = other.positionAbsolute ?? other.position ?? { x: 0, y: 0 }
  const w2 = w / 2
  const h2 = h / 2
  const cx = p.x + w2
  const cy = p.y + h2
  const ox = op.x + ow / 2
  const oy = op.y + oh / 2
  const xx1 = (ox - cx) / (2 * w2) - (oy - cy) / (2 * h2)
  const yy1 = (ox - cx) / (2 * w2) + (oy - cy) / (2 * h2)
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1)
  const xx3 = a * xx1
  const yy3 = a * yy1
  return { x: w2 * (xx3 + yy3) + cx, y: h2 * (-xx3 + yy3) + cy }
}

// A custom edge, not React Flow's built-in 'smoothstep' type, for one specific reason:
// the built-in edge's `label` prop renders as an SVG <text> inside the edges layer,
// which sits BEHIND the nodes layer in React Flow's stacking order -- so a label that
// happens to fall under a table card is invisible, hidden by the card itself.
// EdgeLabelRenderer portals the label into its own `.react-flow__edgelabel-renderer`
// container, but that container is STILL painted behind `.react-flow__nodes` by default
// (both are position:absolute with no z-index, so DOM order inside `.react-flow__viewport`
// decides paint order, and the nodes container comes after). So the label below sets an
// explicit positive z-index (above React Flow's own default z-index:1000 for an
// elevated/selected node) so it always paints on top.
//
// The label is HOVER-ONLY (App sets data.showLabel from transient hover state, never from
// selection). It's also kept deliberately compact -- a short mapping on one line, a long
// or composite mapping stacked (fk over pk with a vertical arrow) -- so it never becomes a
// wide horizontal bar overhanging the neighboring tables.
export interface RelationshipEdgeData {
  inferred?: boolean
  showLabel?: boolean
  fkCols?: string[]
  pkCols?: string[]
  // dbxmetagen predicted-FK overlay: a distinct violet edge with a confidence score,
  // shown only when the "dbxmetagen FK predictions" layer is toggled on.
  predicted?: boolean
  confidence?: number | null
  // Star/Galaxy only: attach the line to the card border facing the other card (a straight
  // "floating" edge) instead of the fixed Left/Right per-column handles -- avoids the long
  // right-angle wrap-arounds a radial layout produces. LR/TB leave this unset.
  floating?: boolean
}

export function RelationshipEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  style,
  data,
}: EdgeProps<RelationshipEdgeData>) {
  const floating = Boolean(data?.floating)
  // Node internals are read unconditionally (hooks can't be conditional) but only used in
  // floating mode. Cheap selectors keyed on the node ids.
  const sourceNode = useStore(useCallback((s: ReactFlowState) => s.nodeInternals.get(source) as FloatingNode | undefined, [source]))
  const targetNode = useStore(useCallback((s: ReactFlowState) => s.nodeInternals.get(target) as FloatingNode | undefined, [target]))

  let sx = sourceX
  let sy = sourceY
  let tx = targetX
  let ty = targetY
  let path: string
  let labelX: number
  let labelY: number
  if (floating && sourceNode && targetNode) {
    const s = nodeIntersection(sourceNode, targetNode)
    const t = nodeIntersection(targetNode, sourceNode)
    sx = s.x
    sy = s.y
    tx = t.x
    ty = t.y
    ;[path, labelX, labelY] = getStraightPath({ sourceX: sx, sourceY: sy, targetX: tx, targetY: ty })
  } else {
    ;[path, labelX, labelY] = getSmoothStepPath({
      sourceX,
      sourceY,
      sourcePosition,
      targetX,
      targetY,
      targetPosition,
    })
  }
  const inferred = Boolean(data?.inferred)
  const predicted = Boolean(data?.predicted)
  const showLabel = Boolean(data?.showLabel)
  const label = showLabel
    ? formatJoinLabel(data?.fkCols ?? [], data?.pkCols ?? [], inferred)
    : null
  // Confidence shown on a predicted edge's hover label (e.g. "98%").
  const confidencePct =
    predicted && typeof data?.confidence === 'number' ? `${Math.round(data.confidence * 100)}%` : null
  const accent = predicted ? 'var(--predicted)' : inferred ? 'var(--db-red)' : 'var(--text-muted)'

  // Crow's-foot cardinality, drawn directly in the edge layer rather than via SVG <marker>
  // (whose auto-orientation rendered the "many" foot as a plain arrowhead). The layout is
  // always left-to-right with per-column handles on Right (source/FK) and Left (target/PK),
  // so the line is horizontal at both endpoints -- which lets us draw fixed-orientation
  // glyphs: a splayed three-prong "many" foot planted at the FK/source node, and a single
  // perpendicular "one" bar at the PK/target node. Passive (pointerEvents none) and dimmed
  // in step with the line.
  const cardStroke = predicted ? 'var(--predicted)' : inferred ? 'var(--db-red)' : 'var(--edge)'
  const markerOpacity = style?.opacity ?? 1
  // The crow's-foot/one-bar glyphs are drawn assuming a HORIZONTAL line at both endpoints
  // (the fixed Left/Right per-column handles). Schema-summary edges in top-to-bottom
  // layout use Bottom/Top handles (a vertical line), where these fixed shapes would point
  // sideways -- so skip the cardinality glyphs there rather than draw them wrong.
  const horizontalEnds =
    (sourcePosition === Position.Left || sourcePosition === Position.Right) &&
    (targetPosition === Position.Left || targetPosition === Position.Right)
  // Draw the cardinality glyphs for horizontal (LR) per-column edges AND for floating
  // (Star/Galaxy) edges; skip only the vertical schema-summary (TB) edges, where the fixed
  // glyph shapes would point sideways.
  const showMarkers = floating || horizontalEnds
  // The glyphs are authored for a rightward line (source at left, target at right). In
  // floating mode the line runs at any angle, so rotate each glyph group about its own
  // endpoint to align with the line; angle is 0 (identity) for the horizontal LR case.
  const angleDeg = floating ? (Math.atan2(ty - sy, tx - sx) * 180) / Math.PI : 0
  // "many" foot: toes splay by the source node, converging outward toward the line.
  const manyPath =
    `M${sx + 15},${sy} L${sx + 1},${sy - 6}` +
    ` M${sx + 15},${sy} L${sx + 1},${sy}` +
    ` M${sx + 15},${sy} L${sx + 1},${sy + 6}`
  // "one" bar: a single tick perpendicular to the line, just before the target node.
  const onePath = `M${tx - 11},${ty - 6} L${tx - 11},${ty + 6}`
  const markerStyle = { opacity: markerOpacity, pointerEvents: 'none' as const }

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {showMarkers && (
        <>
          <path d={manyPath} transform={`rotate(${angleDeg} ${sx} ${sy})`} stroke={cardStroke} strokeWidth={1.6} fill="none" strokeLinecap="round" strokeLinejoin="round" style={markerStyle} />
          <path d={onePath} transform={`rotate(${angleDeg} ${tx} ${ty})`} stroke={cardStroke} strokeWidth={1.6} fill="none" strokeLinecap="round" style={markerStyle} />
        </>
      )}
      {label && (
        <EdgeLabelRenderer>
          <div
            className="erd-edge-label"
            data-edge-id={id}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              // Never intercept the pointer -- the label is a passive readout floating over
              // the canvas; hover/click must keep reaching the edge and nodes beneath it.
              pointerEvents: 'none',
              zIndex: 1001,
              background: 'var(--surface)',
              borderRadius: 5,
              padding: label.stacked ? '3px 7px' : '1px 6px',
              // Cap the footprint so even a pathological name can't sprawl across neighbors;
              // long single tokens wrap instead of pushing the box wider.
              maxWidth: 180,
              fontSize: 9,
              fontWeight: label.inferred ? 700 : 500,
              color: accent,
              lineHeight: 1.25,
              textAlign: 'center',
              overflowWrap: 'anywhere',
              boxShadow: '0 1px 3px rgba(16,24,40,0.14)',
              border: '1px solid var(--border)',
              // Match the line's dim/highlight opacity (set by App's displayEdges) so a
              // de-emphasized edge's label dims with it.
              opacity: style?.opacity ?? 1,
            }}
          >
            {label.stacked ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                <span>{label.fk}</span>
                <span aria-hidden style={{ opacity: 0.7, lineHeight: 1 }}>↓</span>
                <span>{label.pk}</span>
                {label.inferred && <span style={{ fontSize: 8, opacity: 0.9 }}>(inferred)</span>}
                {predicted && <span style={{ fontSize: 8, opacity: 0.9 }}>predicted{confidencePct ? ` · ${confidencePct}` : ''}</span>}
              </div>
            ) : (
              <span style={{ whiteSpace: 'nowrap' }}>
                {label.fk} → {label.pk}
                {label.inferred ? ' (inferred)' : ''}
                {predicted ? ` · predicted${confidencePct ? ` ${confidencePct}` : ''}` : ''}
              </span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
