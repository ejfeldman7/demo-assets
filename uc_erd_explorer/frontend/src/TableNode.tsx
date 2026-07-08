import { Handle, Position, type NodeProps } from 'reactflow'
import type { ColorPair } from './catalogColors'
import type { TableNodeData, TagValue } from './types'

// Deterministic tag -> color mapping. Well-known governance keywords get a fixed,
// attention-appropriate color; anything else falls back to a stable hash-based pick
// from a small palette, so arbitrary customer tag taxonomies still render distinctly
// without us hardcoding every possible tag name.
const KNOWN_TAG_COLORS: Record<string, { bg: string; fg: string }> = {
  pii: { bg: '#fee4e2', fg: '#b42318' },
  contains_pii: { bg: '#fee4e2', fg: '#b42318' },
  deprecated: { bg: '#f2f4f7', fg: '#667085' },
  sensitivity: { bg: '#fef0c7', fg: '#b54708' },
  confidential: { bg: '#fef0c7', fg: '#b54708' },
}
const TAG_PALETTE = [
  { bg: '#eaf2fb', fg: '#2272b4' },
  { bg: '#f1eafe', fg: '#7c3aed' },
  { bg: '#e6f4ea', fg: '#1a7f37' },
  { bg: '#fdf2e9', fg: '#c2410c' },
]

function tagColor(name: string): { bg: string; fg: string } {
  const key = name.toLowerCase()
  if (KNOWN_TAG_COLORS[key]) return KNOWN_TAG_COLORS[key]
  let hash = 0
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  return TAG_PALETTE[hash % TAG_PALETTE.length]
}

function TagBadge({ tag, small }: { tag: TagValue; small?: boolean }) {
  const colors = tagColor(tag.name)
  const label = tag.value && tag.value !== 'true' ? `${tag.name}:${tag.value}` : tag.name
  return (
    <span
      title={`${tag.name} = ${tag.value}`}
      style={{
        fontSize: small ? 8.5 : 9.5,
        fontWeight: 700,
        color: colors.fg,
        background: colors.bg,
        borderRadius: 4,
        padding: small ? '0 4px' : '1px 5px',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  )
}

export interface TableNodeProps extends NodeProps<TableNodeData> {
  data: TableNodeData & { dimmed?: boolean; selected?: boolean; color?: ColorPair }
}

export function TableNode({ data }: TableNodeProps) {
  const colors = data.color ?? { bar: '#475467', soft: '#f2f4f7' }
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
        <span title={data.comment ?? undefined} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          {data.table}
          {data.comment && <span style={{ opacity: 0.75, fontSize: 11 }}>ⓘ</span>}
        </span>
        <span
          title={`${data.catalog}.${data.schema}`}
          style={{
            fontSize: 9.5,
            fontWeight: 600,
            opacity: 0.9,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            background: 'rgba(255,255,255,0.18)',
            borderRadius: 4,
            padding: '1px 5px',
            maxWidth: 110,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {data.catalog}.{data.schema}
        </span>
      </div>
      {data.tags.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 4,
            padding: '6px 11px',
            borderTop: '1px solid #f2f4f7',
            background: '#fbfcfd',
          }}
        >
          {data.tags.map((tag) => (
            <TagBadge key={tag.name} tag={tag} />
          ))}
        </div>
      )}
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
              title={col.comment ?? undefined}
              style={{
                flex: 1,
                fontWeight: col.is_pk ? 600 : 400,
                color: '#1d2939',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                minWidth: 0,
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{col.name}</span>
              {col.comment && <span style={{ color: '#98a2b3', fontSize: 10, flexShrink: 0 }}>ⓘ</span>}
            </span>
            {col.tags.length > 0 && (
              <span style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                {col.tags.map((tag) => (
                  <TagBadge key={tag.name} tag={tag} small />
                ))}
              </span>
            )}
            <span style={{ color: '#98a2b3', fontSize: 10 }}>{col.type}</span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: colors.bar, border: 'none' }} />
    </div>
  )
}
