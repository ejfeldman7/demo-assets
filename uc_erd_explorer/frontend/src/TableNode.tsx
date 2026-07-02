import { Handle, Position, type NodeProps } from 'reactflow'
import type { TableNodeData } from './types'

const SCHEMA_COLORS: Record<string, { bar: string; soft: string }> = {
  factory: { bar: '#2272b4', soft: '#eaf2fb' },
  erp: { bar: '#7c3aed', soft: '#f1eafe' },
}

export interface TableNodeProps extends NodeProps<TableNodeData> {
  data: TableNodeData & { dimmed?: boolean; selected?: boolean }
}

export function TableNode({ data }: TableNodeProps) {
  const colors = SCHEMA_COLORS[data.schema] ?? { bar: '#475569', soft: '#eef2f6' }
  const dimmed = data.dimmed
  const selected = data.selected

  return (
    <div
      style={{
        width: 240,
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
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: colors.bar, border: 'none' }} />
      <div
        style={{
          background: colors.bar,
          color: '#fff',
          padding: '8px 11px',
          fontWeight: 600,
          fontSize: 13,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span>{data.table}</span>
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 600,
            opacity: 0.9,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            background: 'rgba(255,255,255,0.18)',
            borderRadius: 4,
            padding: '1px 5px',
          }}
        >
          {data.schema}
        </span>
      </div>
      <div>
        {data.columns.map((col) => (
          <div
            key={col.name}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 11px',
              borderTop: '1px solid #f2f4f7',
              lineHeight: '16px',
            }}
          >
            <span style={{ display: 'flex', gap: 3, minWidth: 34 }}>
              {col.is_pk && (
                <span
                  title="Primary key"
                  style={{
                    fontSize: 9,
                    fontWeight: 700,
                    color: '#b54708',
                    background: '#fef0c7',
                    borderRadius: 4,
                    padding: '0 4px',
                  }}
                >
                  PK
                </span>
              )}
              {col.is_fk && (
                <span
                  title="Foreign key"
                  style={{
                    fontSize: 9,
                    fontWeight: 700,
                    color: '#175cd3',
                    background: '#eff8ff',
                    borderRadius: 4,
                    padding: '0 4px',
                  }}
                >
                  FK
                </span>
              )}
            </span>
            <span
              style={{
                flex: 1,
                fontWeight: col.is_pk ? 600 : 400,
                color: '#1d2939',
              }}
            >
              {col.name}
            </span>
            <span style={{ color: '#98a2b3', fontSize: 10 }}>{col.type}</span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: colors.bar, border: 'none' }} />
    </div>
  )
}
