import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { rankTables, type TableEntry } from './search'

// Cmd/Ctrl+K quick-find, the pattern Databricks' own Lineage Explorer uses for large
// graphs: type to fuzzy-match a table, ↑/↓ to move, Enter to jump to it, Esc to close.
// Purely a navigation aid over the already-laid-out nodes -- selecting one pans/zooms to
// the table and focuses it, reusing the same selection the sidebar search drives.
export function CommandPalette({
  open,
  tables,
  onSelect,
  onClose,
}: {
  open: boolean
  tables: TableEntry[]
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const results = useMemo(() => rankTables(query, tables, 50), [query, tables])

  // Reset + focus each time it opens.
  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
      // Focus after paint so the input actually receives it.
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Keep the active row in view as you arrow through.
  useEffect(() => {
    if (active < 0) return
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const commit = (idx: number) => {
    const item = results[idx]
    if (item) {
      onSelect(item.id)
      onClose()
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      commit(active)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  return (
    <div style={styles.backdrop} onMouseDown={onClose}>
      <div style={styles.palette} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="Find a table">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          placeholder="Find a table by name…"
          aria-label="Quick-find a table by name"
          style={styles.input}
        />
        <div ref={listRef} style={styles.list}>
          {results.length === 0 ? (
            <div style={styles.empty}>No matching tables</div>
          ) : (
            results.map((r, i) => (
              <div
                key={r.id}
                data-idx={i}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  commit(i)
                }}
                style={{ ...styles.row, ...(i === active ? styles.rowActive : null) }}
              >
                <span style={styles.rowTable}>{r.table}</span>
                <span style={styles.rowQualifier}>
                  {r.catalog}.{r.schema}
                </span>
              </div>
            ))
          )}
        </div>
        <div style={styles.footer}>
          <span><kbd style={styles.kbd}>↑</kbd><kbd style={styles.kbd}>↓</kbd> navigate</span>
          <span><kbd style={styles.kbd}>↵</kbd> jump</span>
          <span><kbd style={styles.kbd}>esc</kbd> close</span>
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(16,24,40,0.35)',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'flex-start',
    paddingTop: '12vh',
    zIndex: 2000,
  },
  palette: {
    width: 'min(560px, 92vw)',
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 12px 40px rgba(16,24,40,0.28)',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
    maxHeight: '64vh',
  },
  input: {
    border: 'none',
    borderBottom: '1px solid #e4e7ec',
    padding: '15px 18px',
    fontSize: 16,
    outline: 'none',
    color: 'var(--text)',
  },
  list: { overflowY: 'auto', padding: 6 },
  row: {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 10,
    padding: '9px 12px',
    borderRadius: 8,
    cursor: 'pointer',
  },
  rowActive: { background: 'var(--db-blue-soft)' },
  rowTable: { fontSize: 14, fontWeight: 600, color: 'var(--text)' },
  rowQualifier: { fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap' },
  empty: { padding: '18px 12px', fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' },
  footer: {
    display: 'flex',
    gap: 16,
    padding: '8px 14px',
    borderTop: '1px solid #f2f4f7',
    fontSize: 11,
    color: 'var(--text-muted)',
    background: '#fbfcfd',
  },
  kbd: {
    display: 'inline-block',
    minWidth: 16,
    textAlign: 'center',
    padding: '1px 5px',
    marginRight: 3,
    border: '1px solid #d0d5dd',
    borderRadius: 4,
    background: '#fff',
    fontSize: 10,
    fontFamily: 'inherit',
  },
}
