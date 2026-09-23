import { useContext, useEffect, type MouseEvent as ReactMouseEvent } from 'react'
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from 'reactflow'
import type { ColorPair } from './catalogColors'
import { ErdInteractionContext } from './erdContext'
import type { TableNodeData, TagValue } from './types'

// A small key glyph, matching Databricks Catalog Explorer's use of a key icon for PK/FK
// columns (instead of text "PK"/"FK" badges). Color-coded gold (PK) / blue (FK), but the
// distinction is never color-only: each carries a title + aria-label naming the key type.
function KeyIcon({ kind }: { kind: 'pk' | 'fk' }) {
  // PK = gold, FK = blue. The gold is deliberately a true amber-gold (#ca8a04), NOT the
  // red-orange it used to be (#b54708) -- that read as red and clashed with the red brand
  // accent, the red inferred edges, and red-family catalog headers. #ca8a04 clears the 3:1
  // non-text contrast bar on white, and PK vs FK is also cued by shape (filled vs outline),
  // never by color alone.
  const color = kind === 'pk' ? 'var(--pk)' : 'var(--fk)'
  const label = kind === 'pk' ? 'Primary key' : 'Foreign key'
  return (
    <svg
      width={13}
      height={13}
      viewBox="0 0 24 24"
      role="img"
      aria-label={label}
      fill="none"
      stroke={color}
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
    >
      <title>{label}</title>
      {/* An actual key: a ring "bow" + an angled shaft ending in two teeth (bit). The bow
          is filled for a PK, hollow for an FK -- a shape/fill cue so PK vs FK never relies
          on color alone. */}
      <circle cx={7.5} cy={15.5} r={5.5} fill={kind === 'pk' ? color : 'none'} />
      <path d="M21 2 L11.4 11.6" />
      <path d="M15.5 7.5 L18.5 10.5 L22 7 L19 4" />
    </svg>
  )
}

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
  data: TableNodeData & {
    dimmed?: boolean
    selected?: boolean
    color?: ColorPair
    // Columns to highlight because an active (hovered) relationship connects to them --
    // this is what lights up the matching column on the *other* table (extra B).
    highlightedCols?: Set<string>
    // Column-cap state (set in App). `columns` is already the visible (ordered, capped)
    // slice; hiddenColumnCount is how many are hidden; hasColumnFooter gates the footer;
    // expanded says whether the full set is shown.
    hiddenColumnCount?: number
    hasColumnFooter?: boolean
    expanded?: boolean
  }
}

// Reveal the full text as a native tooltip only when the element is actually clipped (its
// text overflows its box, i.e. the ellipsis is showing). Computed lazily on hover -- no
// per-row measurement on render, and the metrics are final by the time the pointer arrives.
// `fallback` preserves a pre-existing tooltip (e.g. a column/table comment) when the text
// itself fits, so a short name still shows its comment on hover rather than nothing.
function revealIfClipped(e: ReactMouseEvent<HTMLElement>, text: string, fallback?: string | null) {
  const el = e.currentTarget
  el.title = el.scrollWidth > el.clientWidth ? text : (fallback ?? '')
}

export function TableNode({ id, data }: TableNodeProps) {
  const colors = data.color ?? { bar: '#475467', soft: '#f2f4f7' }
  const dimmed = data.dimmed
  const selected = data.selected
  const highlightedCols = data.highlightedCols
  const { onKeyEnter, onKeyLeave, onToggleExpand } = useContext(ErdInteractionContext)
  const updateNodeInternals = useUpdateNodeInternals()

  // App already delivers `columns` ordered (PK -> FK -> rest) and capped via
  // visibleColumns(), so the node renders them as-is. (Ordering is display-only; the
  // exported model/DDL still uses the payload's true ordinal order via export.ts.)
  const displayColumns = data.columns

  // Each column row carries its own source/target handle (id = column name) so edges
  // anchor to the actual related field, not the card's vertical center. React Flow caches
  // handle geometry, so when the visible column set changes (the keys-only toggle, a new
  // graph load, or the reorder above) we must ask it to re-measure -- otherwise edges keep
  // pointing at stale row positions. The key is the visible column names in render order.
  const colKey = displayColumns.map((c) => c.name).join('|')
  useEffect(() => {
    updateNodeInternals(id)
  }, [id, colKey, updateNodeInternals])

  // Boundary stub: a declared FK's target table that's outside the current view. Rendered
  // as an inert, greyed, dashed card (no columns, no tags, no expand) so the relationship
  // has something to point at without pulling the whole parent table into scope. Carries a
  // default target handle so the cross-scope edge can anchor to it.
  if (data.is_boundary) {
    return (
      <div
        title={`${data.catalog}.${data.schema}.${data.table} — referenced by a foreign key but outside the current view. Add its schema to the selection to see it in full.`}
        style={{
          width: 200,
          background: 'var(--surface-subtle)',
          border: '1.5px dashed var(--border)',
          borderRadius: 10,
          padding: '9px 12px',
          opacity: dimmed ? 0.22 : 0.9,
          fontSize: 12,
        }}
      >
        <Handle type="target" position={Position.Left} isConnectable={false} style={columnHandleStyle('#98a2b3')} />
        <Handle type="source" position={Position.Right} isConnectable={false} style={columnHandleStyle('#98a2b3')} />
        <div
          onMouseEnter={(e) => revealIfClipped(e, data.table)}
          style={{ fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {data.table}
        </div>
        <div style={{ color: 'var(--text-subtle)', fontSize: 10, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {data.catalog}.{data.schema}
        </div>
        <div style={{ color: 'var(--text-subtle)', fontSize: 9.5, marginTop: 4, textTransform: 'uppercase', letterSpacing: 0.4 }}>
          ↗ referenced · not in view
        </div>
      </div>
    )
  }

  return (
    <div
      style={{
        width: 240,
        background: 'var(--surface)',
        border: selected ? `2px solid ${colors.bar}` : '1px solid var(--border)',
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
      <div
        style={{
          background: colors.bar,
          color: 'var(--on-accent)',
          padding: '8px 11px',
          fontWeight: 600,
          fontSize: 13,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
          <span
            // Long table names hard-clip at the card edge otherwise; ellipsis + hover-reveal
            // (only when actually clipped) shows the full name without a permanent tooltip.
            onMouseEnter={(e) => revealIfClipped(e, data.table, data.comment)}
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {data.table}
          </span>
          {data.comment && <span title={data.comment} style={{ opacity: 0.75, fontSize: 11, flexShrink: 0 }}>ⓘ</span>}
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
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--surface-subtle)',
          }}
        >
          {data.tags.map((tag) => (
            <TagBadge key={tag.name} tag={tag} />
          ))}
        </div>
      )}
      <div>
        {displayColumns.map((col) => {
          const isKey = col.is_pk || col.is_fk
          const highlighted = highlightedCols?.has(col.name) ?? false
          // What this FK points at (declared FKs only). When the target is off-canvas we
          // also show a small "↗" so the key reads as "declared, but pointing out of view".
          const ref = col.references
          const refTitle = ref
            ? `References ${ref.table}.${ref.column}${ref.in_view ? '' : ' — not in current view'}`
            : undefined
          const refOutOfView = Boolean(ref && !ref.in_view)
          return (
          <div
            key={col.name}
            // Hovering a PK/FK column reveals just that relationship (and highlights the
            // matching column on the other table). Non-key columns take no handlers, so
            // hovering them does nothing -- detail comes only from lines and keys.
            onMouseEnter={isKey ? () => onKeyEnter({ nodeId: id, column: col.name }) : undefined}
            onMouseLeave={isKey ? onKeyLeave : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 11px',
              borderTop: '1px solid var(--border-subtle)',
              lineHeight: '16px',
              // Highlight the row when an active relationship connects to this column.
              background: highlighted ? colors.soft : undefined,
              boxShadow: highlighted ? `inset 3px 0 0 ${colors.bar}` : undefined,
              cursor: isKey ? 'pointer' : 'default',
              transition: 'background 0.12s ease',
              // Anchor point for this row's per-column handles (positioned absolutely at
              // the row's left/right center by React Flow).
              position: 'relative',
            }}
          >
            {/* Per-column edge anchors: incoming FKs target this row's left handle,
                outgoing FKs leave from its right handle. id = column name (unique within a
                table). Non-connectable -- these are display anchors, not drag points. */}
            <Handle
              type="target"
              position={Position.Left}
              id={col.name}
              isConnectable={false}
              style={columnHandleStyle(colors.bar)}
            />
            <Handle
              type="source"
              position={Position.Right}
              id={col.name}
              isConnectable={false}
              style={columnHandleStyle(colors.bar)}
            />
            <span title={refTitle} style={{ display: 'flex', alignItems: 'center', gap: 3, minWidth: 26 }}>
              {col.is_pk && <KeyIcon kind="pk" />}
              {col.is_fk && <KeyIcon kind="fk" />}
              {refOutOfView && (
                <span aria-label="references a table outside the current view" style={{ color: 'var(--text-subtle)', fontSize: 10, fontWeight: 700 }}>
                  ↗
                </span>
              )}
            </span>
            <span
              title={col.comment ?? undefined}
              style={{
                flex: 1,
                fontWeight: col.is_pk ? 600 : 400,
                color: 'var(--text)',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                minWidth: 0,
              }}
            >
              <span
                // Same reveal-on-clip as the table name: full column name on hover only when
                // it's actually truncated; otherwise fall back to the column comment tooltip.
                onMouseEnter={(e) => revealIfClipped(e, col.name, col.comment)}
                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
                {col.name}
              </span>
              {col.comment && <span style={{ color: 'var(--text-subtle)', fontSize: 10, flexShrink: 0 }}>ⓘ</span>}
            </span>
            {col.tags.length > 0 && (
              <span style={{ display: 'flex', gap: 3, flexShrink: 0 }}>
                {col.tags.map((tag) => (
                  <TagBadge key={tag.name} tag={tag} small />
                ))}
              </span>
            )}
            <span style={{ color: 'var(--text-subtle)', fontSize: 10 }}>{col.type}</span>
          </div>
          )
        })}
      </div>
      {data.hasColumnFooter && (
        // "nodrag" so clicking the control doesn't start a node drag; stopPropagation so it
        // doesn't also fire the table's click-to-focus. Toggling re-runs layout in App.
        <button
          className="nodrag"
          onClick={(e) => {
            e.stopPropagation()
            onToggleExpand(id)
          }}
          title={data.expanded ? 'Collapse to the capped view' : 'Show all columns'}
          aria-expanded={data.expanded}
          style={{
            width: '100%',
            border: 'none',
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--surface-subtle)',
            color: 'var(--db-blue)',
            fontSize: 10.5,
            fontWeight: 600,
            padding: '5px 11px',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          {data.expanded
            ? '− Show fewer'
            : `+ ${data.hiddenColumnCount} more column${data.hiddenColumnCount === 1 ? '' : 's'}`}
        </button>
      )}
    </div>
  )
}

// Small, subtle per-column handle -- a little dot at the row's edge that the edge
// connects to. Kept understated so a table with many keys doesn't look busy.
function columnHandleStyle(bar: string) {
  return {
    width: 7,
    height: 7,
    minWidth: 0,
    minHeight: 0,
    background: bar,
    border: '1.5px solid var(--surface)',
    opacity: 0.85,
  }
}
