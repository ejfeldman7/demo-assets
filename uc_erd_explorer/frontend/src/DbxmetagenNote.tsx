import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { fetchDbxmetagen, type DbxmetagenStatus } from './api'
import type { CatalogEnv } from './types'

// A small sidebar footer that reflects the dbxmetagen relationship: when its output is
// present, note that its reviewed comments/tags already show on the diagram; when it's not,
// recommend it (with a link) as the write-side companion for AI-generated metadata. Purely
// informational and best-effort -- it renders nothing until the check resolves, and never
// on an error.
export function DbxmetagenNote({ env }: { env: CatalogEnv }) {
  const [status, setStatus] = useState<DbxmetagenStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchDbxmetagen(env)
      .then((s) => { if (!cancelled) setStatus(s) })
      .catch(() => { /* best-effort: stay silent if the check fails */ })
    return () => { cancelled = true }
  }, [env])

  if (!status) return null

  if (status.present) {
    return (
      <div style={styles.present}>
        <div style={styles.title}>✓ dbxmetagen metadata detected</div>
        <div style={styles.body}>
          Reviewed comments and tags it applied to Unity Catalog show on the diagram
          automatically. Source: <code style={styles.code}>{status.location}</code>.
        </div>
      </div>
    )
  }

  return (
    <div style={styles.card}>
      <div style={styles.title}>Want AI-generated docs &amp; PII tagging?</div>
      <div style={styles.body}>
        This app stays read-only. For AI-generated table/column descriptions, PII/PHI/PCI
        tagging, and confidence-scored foreign-key prediction — with human review before
        anything is applied — deploy{' '}
        <a href={status.repo_url} target="_blank" rel="noreferrer" style={styles.link}>
          dbxmetagen&nbsp;↗
        </a>{' '}
        against the same catalogs. Its results then surface here.
      </div>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  card: {
    marginTop: 12,
    background: 'var(--surface-subtle)',
    border: '1px dashed var(--border-strong)',
    borderRadius: 'var(--radius)',
    padding: '10px 12px',
  },
  present: {
    marginTop: 12,
    background: 'var(--surface-subtle)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '10px 12px',
  },
  title: { fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 4 },
  body: { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 },
  code: { fontFamily: 'ui-monospace, monospace', fontSize: 10.5, color: 'var(--text)' },
  link: { color: 'var(--db-blue)', fontWeight: 600, textDecoration: 'none' },
}
