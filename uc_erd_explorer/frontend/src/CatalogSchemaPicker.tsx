import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import type { CatalogSchemas } from './types'

interface CatalogSchemaPickerProps {
  tree: CatalogSchemas[]
  /** null means "All" -- no filter, everything in scope. */
  selectedPairs: Set<string> | null
  onChange: (pairs: Set<string> | null) => void
}

function pairKey(catalog: string, schema: string): string {
  return `${catalog}.${schema}`
}

function allPairs(tree: CatalogSchemas[]): Set<string> {
  const s = new Set<string>()
  for (const c of tree) for (const schema of c.schemas) s.add(pairKey(c.catalog, schema))
  return s
}

type CatalogState = 'checked' | 'indeterminate' | 'unchecked'

function catalogState(catalog: CatalogSchemas, effective: Set<string>): CatalogState {
  const selectedCount = catalog.schemas.filter((s) => effective.has(pairKey(catalog.catalog, s))).length
  if (selectedCount === 0) return 'unchecked'
  if (selectedCount === catalog.schemas.length) return 'checked'
  return 'indeterminate'
}

export function CatalogSchemaPicker({ tree, selectedPairs, onChange }: CatalogSchemaPickerProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // `tree` arrives asynchronously (fetched after mount), so a useState initializer alone
  // would capture an empty array and nothing would ever end up expanded. Add each
  // catalog to `expanded` as it appears, without clobbering any manual collapse the user
  // has already done on catalogs we've seen before.
  useEffect(() => {
    setExpanded((prev) => {
      const next = new Set(prev)
      for (const c of tree) next.add(c.catalog)
      return next
    })
  }, [tree])

  const all = allPairs(tree)
  const effective = selectedPairs ?? all
  const isAll = selectedPairs === null

  function commit(next: Set<string>) {
    // Collapse back to the "All" sentinel if the explicit set now covers everything --
    // keeps the request unfiltered (and the UI state simple) instead of sending a huge
    // pairs list that means the same thing.
    onChange(next.size === all.size ? null : next)
  }

  function toggleCatalog(catalog: CatalogSchemas) {
    const next = new Set(effective)
    const state = catalogState(catalog, effective)
    for (const schema of catalog.schemas) {
      const key = pairKey(catalog.catalog, schema)
      if (state === 'unchecked') next.add(key)
      else next.delete(key)
    }
    commit(next)
  }

  function toggleSchema(catalog: string, schema: string) {
    const key = pairKey(catalog, schema)
    const next = new Set(effective)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    commit(next)
  }

  function toggleExpanded(catalog: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(catalog)) next.delete(catalog)
      else next.add(catalog)
      return next
    })
  }

  return (
    <div style={styles.tree}>
      <button onClick={() => onChange(null)} style={rowStyle(isAll)}>
        <Checkbox state={isAll ? 'checked' : 'unchecked'} />
        <span style={{ flex: 1, fontWeight: 700 }}>All</span>
        {isAll && <span style={styles.check}>✓</span>}
      </button>

      {tree.map((catalog) => {
        const state = catalogState(catalog, effective)
        const isExpanded = expanded.has(catalog.catalog)
        return (
          <div key={catalog.catalog}>
            <div style={styles.catalogRow}>
              <button
                onClick={() => toggleExpanded(catalog.catalog)}
                style={styles.chevronBtn}
                aria-label={isExpanded ? 'Collapse' : 'Expand'}
              >
                {isExpanded ? '▾' : '▸'}
              </button>
              <button onClick={() => toggleCatalog(catalog)} style={rowStyle(state !== 'unchecked')}>
                <Checkbox state={state} />
                <span style={{ flex: 1 }}>{catalog.catalog}</span>
                <span style={styles.countBadge}>{catalog.schemas.length}</span>
              </button>
            </div>
            {isExpanded && (
              <div style={styles.schemaList}>
                {catalog.schemas.map((schema) => {
                  const key = pairKey(catalog.catalog, schema)
                  const checked = effective.has(key)
                  return (
                    <button
                      key={key}
                      onClick={() => toggleSchema(catalog.catalog, schema)}
                      style={rowStyle(checked)}
                    >
                      <Checkbox state={checked ? 'checked' : 'unchecked'} />
                      <span style={{ flex: 1 }}>{schema}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Checkbox({ state }: { state: CatalogState }) {
  return (
    <span
      style={{
        ...styles.checkbox,
        background: state === 'unchecked' ? '#fff' : 'var(--db-blue)',
        borderColor: state === 'unchecked' ? 'var(--border-strong)' : 'var(--db-blue)',
      }}
    >
      {state === 'checked' && '✓'}
      {state === 'indeterminate' && '–'}
    </span>
  )
}

function rowStyle(active: boolean): CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    padding: '7px 8px',
    borderRadius: 7,
    border: 'none',
    background: active ? 'var(--db-blue-soft)' : 'transparent',
    color: active ? 'var(--db-blue)' : 'var(--text)',
    fontWeight: active ? 600 : 500,
    fontSize: 13,
    cursor: 'pointer',
    textAlign: 'left',
  }
}

const styles: Record<string, CSSProperties> = {
  tree: { display: 'flex', flexDirection: 'column', gap: 2 },
  catalogRow: { display: 'flex', alignItems: 'center', gap: 2 },
  chevronBtn: {
    border: 'none',
    background: 'transparent',
    color: 'var(--text-muted)',
    fontSize: 11,
    width: 16,
    flexShrink: 0,
    cursor: 'pointer',
  },
  schemaList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    marginLeft: 22,
    borderLeft: '1px solid var(--border)',
    paddingLeft: 6,
  },
  checkbox: {
    width: 15,
    height: 15,
    borderRadius: 4,
    border: '1.5px solid var(--border-strong)',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 10,
    fontWeight: 800,
    color: '#fff',
  },
  countBadge: {
    fontSize: 10.5,
    color: 'var(--text-muted)',
    background: 'var(--bg)',
    borderRadius: 6,
    padding: '1px 6px',
  },
  check: { color: 'var(--db-blue)', fontSize: 12, fontWeight: 700 },
}
