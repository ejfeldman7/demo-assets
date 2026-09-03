import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from 'reactflow'
import { formatJoinLabel } from './edgeDisplay'

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
}

export function RelationshipEdge({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  style,
  data,
}: EdgeProps<RelationshipEdgeData>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  const inferred = Boolean(data?.inferred)
  const showLabel = Boolean(data?.showLabel)
  const label = showLabel
    ? formatJoinLabel(data?.fkCols ?? [], data?.pkCols ?? [], inferred)
    : null
  const accent = inferred ? 'var(--db-red)' : '#667085'

  // Crow's-foot cardinality, drawn directly in the edge layer rather than via SVG <marker>
  // (whose auto-orientation rendered the "many" foot as a plain arrowhead). The layout is
  // always left-to-right with per-column handles on Right (source/FK) and Left (target/PK),
  // so the line is horizontal at both endpoints -- which lets us draw fixed-orientation
  // glyphs: a splayed three-prong "many" foot planted at the FK/source node, and a single
  // perpendicular "one" bar at the PK/target node. Passive (pointerEvents none) and dimmed
  // in step with the line.
  const cardStroke = inferred ? 'var(--db-red)' : '#98a2b3'
  const markerOpacity = style?.opacity ?? 1
  // "many" foot: toes splay by the source node, converging outward toward the line.
  const manyPath =
    `M${sourceX + 15},${sourceY} L${sourceX + 1},${sourceY - 6}` +
    ` M${sourceX + 15},${sourceY} L${sourceX + 1},${sourceY}` +
    ` M${sourceX + 15},${sourceY} L${sourceX + 1},${sourceY + 6}`
  // "one" bar: a single tick perpendicular to the line, just before the target node.
  const onePath = `M${targetX - 11},${targetY - 6} L${targetX - 11},${targetY + 6}`
  const markerStyle = { opacity: markerOpacity, pointerEvents: 'none' as const }

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <path d={manyPath} stroke={cardStroke} strokeWidth={1.6} fill="none" strokeLinecap="round" strokeLinejoin="round" style={markerStyle} />
      <path d={onePath} stroke={cardStroke} strokeWidth={1.6} fill="none" strokeLinecap="round" style={markerStyle} />
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
              background: '#ffffff',
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
              border: '1px solid #eaecf0',
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
              </div>
            ) : (
              <span style={{ whiteSpace: 'nowrap' }}>
                {label.fk} → {label.pk}
                {label.inferred ? ' (inferred)' : ''}
              </span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
