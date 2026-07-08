import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from 'reactflow'

// A custom edge, not React Flow's built-in 'smoothstep' type, for one specific reason:
// the built-in edge's `label` prop renders as an SVG <text> inside the edges layer,
// which sits BEHIND the nodes layer in React Flow's stacking order -- so a label that
// happens to fall under a table card is invisible, hidden by the card itself. Rendering
// the label through EdgeLabelRenderer (a portal into a layer that sits above nodes,
// purpose-built by React Flow for exactly this) fixes that; the path itself still
// renders via BaseEdge in the normal edges layer.
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
}: EdgeProps<{ inferred?: boolean }>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })
  const inferred = Boolean(data?.inferred)

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="erd-edge-label"
            data-edge-id={id}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
              background: '#ffffff',
              borderRadius: 4,
              padding: '1px 5px',
              fontSize: 10,
              fontWeight: inferred ? 700 : 400,
              color: inferred ? 'var(--db-red)' : '#667085',
              whiteSpace: 'nowrap',
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
