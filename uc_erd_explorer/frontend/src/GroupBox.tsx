import type { NodeProps } from 'reactflow'
import type { ColorPair } from './catalogColors'

// A background container drawn behind a schema's tables in "Group by schema" mode. It's a
// non-interactive React Flow node (not a parent of the tables -- the tables stay top-level
// so the rest of the app is unaffected; see elkLayout.ts), rendered first/underneath so the
// opaque table cards sit on top and only the padding/gaps show the schema's tint.
export interface GroupBoxData {
  catalog: string
  schema: string
  count: number
  color?: ColorPair
}

export function GroupBoxNode({ data }: NodeProps<GroupBoxData>) {
  const color = data.color ?? { bar: '#475467', soft: '#f2f4f7' }
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        boxSizing: 'border-box',
        border: `1.5px solid ${color.bar}`,
        borderRadius: 12,
        background: color.soft,
        opacity: 0.55,
      }}
    >
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          margin: 8,
          padding: '3px 9px',
          borderRadius: 7,
          background: color.bar,
          color: '#fff',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: 0.2,
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{ opacity: 0.85 }}>▦</span>
        {data.catalog}.{data.schema}
        <span style={{ opacity: 0.75, fontWeight: 600 }}>· {data.count}</span>
      </div>
    </div>
  )
}
