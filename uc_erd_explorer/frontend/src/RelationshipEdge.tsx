import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from 'reactflow'

// A custom edge, not React Flow's built-in 'smoothstep' type, for one specific reason:
// the built-in edge's `label` prop renders as an SVG <text> inside the edges layer,
// which sits BEHIND the nodes layer in React Flow's stacking order -- so a label that
// happens to fall under a table card is invisible, hidden by the card itself.
// EdgeLabelRenderer portals the label into its own `.react-flow__edgelabel-renderer`
// container, but that container is STILL painted behind `.react-flow__nodes` by default
// (verified empirically: both are position:absolute with no z-index, so plain DOM order
// inside `.react-flow__viewport` decides paint order, and the nodes container comes
// after the edge-label container in that DOM) -- so EdgeLabelRenderer alone does NOT
// fix the overlap, only moving where in the DOM the label lives. The label below sets an
// explicit positive z-index (above React Flow's own default z-index:1000 for an
// elevated/selected node) so it always paints on top regardless of DOM order.
export function RelationshipEdge({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  style,
  markerEnd,
  label,
  data,
}: EdgeProps<{ inferred?: boolean; showLabel?: boolean }>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  const inferred = Boolean(data?.inferred)
  // Labels are hover/selection-only (App.tsx sets showLabel), not painted on every edge
  // at once: the always-on labels overlapped table content and cluttered dense graphs.
  // The line + arrow still show the relationship exists; the column mapping appears on
  // demand when you hover an edge or focus a table.
  const showLabel = Boolean(data?.showLabel)

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label && showLabel && (
        <EdgeLabelRenderer>
          <div
            className="erd-edge-label"
            data-edge-id={id}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
              zIndex: 1001,
              background: '#ffffff',
              borderRadius: 4,
              padding: '1px 5px',
              fontSize: 9,
              fontWeight: inferred ? 700 : 500,
              color: inferred ? 'var(--db-red)' : '#667085',
              whiteSpace: 'nowrap',
              boxShadow: '0 1px 3px rgba(16,24,40,0.14)',
              // Match the line's dim/highlight opacity (set by App.tsx's displayEdges)
              // so a de-emphasized edge's label dims along with it, not just the line.
              opacity: style?.opacity ?? 1,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
