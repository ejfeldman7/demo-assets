import { Handle, Position, type NodeProps } from 'reactflow'
import { catalogColor } from './catalogColors'
import type { SchemaNodeData } from './types'

export interface SchemaNodeProps extends NodeProps<SchemaNodeData> {
  data: SchemaNodeData & { dimmed?: boolean; selected?: boolean }
}

// Rendered instead of TableNode when the catalog has more tables than
// ERD_SCHEMA_COLLAPSE_THRESHOLD (see server/graph.py build_schema_summary). Clicking one
// selects that schema in the tree picker -- the existing click-to-filter mechanism --
// which re-fetches /api/graph scoped to it and always gets full table-level detail.
export function SchemaNode({ data }: SchemaNodeProps) {
  const colors = catalogColor(data.catalog)
  const dimmed = data.dimmed
  const selected = data.selected

  return (
    <div
      style={{
        width: 220,
        background: '#fff',
        border: selected ? `2px solid ${colors.bar}` : '1px solid #e4e7ec',
        borderRadius: 10,
        boxShadow: selected
          ? `0 0 0 4px ${colors.soft}, 0 8px 24px rgba(16,24,40,0.14)`
          : '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.1)',
        opacity: dimmed ? 0.22 : 1,
        transition: 'opacity 0.2s ease, box-shadow 0.2s ease',
        fontSize: 12,
        overflow: 'hidden',
        cursor: 'pointer',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: colors.bar, border: 'none' }} />
      <div
        style={{
          background: colors.bar,
          color: '#fff',
          padding: '10px 12px',
          fontWeight: 700,
          fontSize: 14,
        }}
      >
        {data.catalog}.{data.schema}
      </div>
      <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span style={{ fontSize: 20, fontWeight: 700, color: '#1d2939' }}>{data.table_count}</span>
        <span style={{ color: '#667085', fontSize: 11 }}>
          table{data.table_count === 1 ? '' : 's'} -- click to expand
        </span>
      </div>
      <Handle type="source" position={Position.Right} style={{ background: colors.bar, border: 'none' }} />
    </div>
  )
}
