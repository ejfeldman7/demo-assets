import { useContext } from 'react'
import type { NodeProps } from 'reactflow'
import type { ColorPair } from './catalogColors'
import { ErdInteractionContext } from './erdContext'

// A background container drawn behind a schema's tables in "Group by schema" mode. It's a
// non-interactive React Flow node (not a parent of the tables -- the tables stay top-level
// so the rest of the app is unaffected; see elkLayout.ts), rendered first/underneath so the
// opaque table cards sit on top and only the padding/gaps show the schema's tint. Its
// header is a click target to collapse/expand the schema; when collapsed the box IS just
// the header (its tables are hidden and dropped from the layout).
export interface GroupBoxData {
  id: string
  catalog: string
  schema: string
  count: number
  collapsed: boolean
  color?: ColorPair
}

export function GroupBoxNode({ data }: NodeProps<GroupBoxData>) {
  const color = data.color ?? { bar: '#475467', soft: 'var(--border-subtle)' }
  const { onToggleGroup } = useContext(ErdInteractionContext)
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        boxSizing: 'border-box',
        border: `1.5px solid ${color.bar}`,
        borderRadius: 12,
        // Collapsed boxes carry no tables, so give the header a solid backdrop; expanded
        // boxes are a faint tint behind their (opaque) tables.
        background: color.soft,
        opacity: data.collapsed ? 1 : 0.55,
      }}
    >
      <button
        className="nodrag"
        onClick={(e) => {
          e.stopPropagation()
          onToggleGroup(data.id)
        }}
        title={data.collapsed ? 'Expand schema' : 'Collapse schema'}
        aria-expanded={!data.collapsed}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          margin: 8,
          padding: '4px 9px',
          borderRadius: 7,
          border: 'none',
          background: color.bar,
          color: 'var(--on-accent)',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: 0.2,
          whiteSpace: 'nowrap',
          cursor: 'pointer',
          maxWidth: 'calc(100% - 16px)',
          overflow: 'hidden',
          // The group box node is pointerEvents:none (so it doesn't swallow edge hover under
          // it -- see App.tsx); re-enable events here so the collapse header stays clickable.
          pointerEvents: 'auto',
        }}
      >
        <span aria-hidden style={{ opacity: 0.9 }}>{data.collapsed ? '▸' : '▾'}</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {data.schema ? `${data.catalog}.${data.schema}` : data.catalog}
        </span>
        <span style={{ opacity: 0.75, fontWeight: 600 }}>· {data.count}</span>
      </button>
    </div>
  )
}
