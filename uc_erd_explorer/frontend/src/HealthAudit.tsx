import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { fetchAudit, type AuditFinding, type AuditResponse } from './api'
import type { CatalogEnv } from './types'

// Deterministic schema-health audit panel. Runs on demand (a button, not on every pan) since
// it builds the graph server-side; results clear when the scope changes so they're never
// stale. Read-only and LLM-free -- it just surfaces structural/documentation gaps.
export function HealthAudit({ pairs, env }: { pairs?: string[]; env: CatalogEnv }) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<AuditResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Clear stale results whenever the audited scope changes.
  const scopeKey = `${pairs?.slice().sort().join(',') ?? ''}|${env}`
  useEffect(() => {
    setData(null)
    setError(null)
  }, [scopeKey])

  async function run() {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchAudit(pairs, env))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div style={styles.label}>Schema health</div>
      <div style={styles.card}>
        {!data && !error && (
          <>
            <button onClick={run} disabled={loading} style={styles.runBtn}>
              {loading ? 'Running…' : 'Run audit'}
            </button>
            <div style={styles.hint}>
              Deterministic checks over the current scope — missing keys, orphan tables,
              documentation gaps, possible untagged PII. No writes, no LLM.
            </div>
          </>
        )}
        {error && (
          <>
            <div style={{ ...styles.hint, color: 'var(--db-red)' }}>{error}</div>
            <button onClick={run} style={styles.runBtn}>Retry</button>
          </>
        )}
        {data && !data.available && <div style={styles.hint}>{data.reason}</div>}
        {data && data.available && (
          <>
            <div style={styles.metrics}>
              <Metric label="Doc coverage" value={`${data.summary.column_doc_coverage_pct ?? 0}%`} />
              <Metric label="No PK" value={String(data.summary.tables_without_pk ?? 0)} />
              <Metric label="Orphans" value={String(data.summary.orphan_tables ?? 0)} />
              <Metric label="PII?" value={String(data.summary.possible_pii_untagged ?? 0)} />
            </div>
            {data.findings.length === 0 ? (
              <div style={styles.clean}>✓ No issues found in this scope.</div>
            ) : (
              <div style={styles.findings}>
                {data.findings.map((f) => <Finding key={f.category} f={f} />)}
              </div>
            )}
            <button onClick={run} disabled={loading} style={styles.rerunBtn}>
              {loading ? 'Running…' : 'Re-run'}
            </button>
          </>
        )}
      </div>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.metric}>
      <div style={styles.metricValue}>{value}</div>
      <div style={styles.metricLabel}>{label}</div>
    </div>
  )
}

function Finding({ f }: { f: AuditFinding }) {
  const accent = f.severity === 'warn' ? 'var(--db-red)' : 'var(--db-blue)'
  const shown = f.objects.slice(0, 6).join(', ')
  const more = f.count - Math.min(f.objects.length, 6)
  return (
    <div style={styles.finding}>
      <div style={styles.findingHead}>
        <span aria-hidden style={{ ...styles.dot, background: accent }} />
        <span style={styles.findingTitle}>{f.title}</span>
      </div>
      <div style={styles.findingDetail}>{f.detail}</div>
      {shown && (
        <div style={styles.findingObjects}>
          {shown}{more > 0 ? `, +${more} more` : ''}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  label: {
    fontSize: 11, fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase',
    color: 'var(--text-subtle)', margin: '12px 4px 6px',
  },
  card: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 'var(--radius)', padding: 6, boxShadow: 'var(--shadow-sm)',
    display: 'flex', flexDirection: 'column', gap: 6,
  },
  runBtn: {
    border: 'none', borderRadius: 8, background: 'var(--db-blue)', color: 'var(--on-accent)',
    padding: '8px 12px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
  },
  rerunBtn: {
    border: '1px solid var(--border-strong)', borderRadius: 8, background: 'var(--surface)',
    color: 'var(--text-muted)', padding: '5px 10px', fontSize: 12, cursor: 'pointer', alignSelf: 'flex-start',
  },
  hint: { fontSize: 11.5, color: 'var(--text-muted)', padding: '2px 4px', lineHeight: 1.4 },
  clean: { fontSize: 12.5, color: 'var(--text)', padding: '4px 4px' },
  metrics: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4 },
  metric: {
    background: 'var(--surface-subtle)', border: '1px solid var(--border-subtle)',
    borderRadius: 8, padding: '6px 4px', textAlign: 'center',
  },
  metricValue: { fontSize: 15, fontWeight: 700, color: 'var(--text)' },
  metricLabel: { fontSize: 9.5, color: 'var(--text-muted)', marginTop: 2 },
  findings: { display: 'flex', flexDirection: 'column', gap: 8, marginTop: 2 },
  finding: {
    borderTop: '1px solid var(--border-subtle)', paddingTop: 8,
    display: 'flex', flexDirection: 'column', gap: 3,
  },
  findingHead: { display: 'flex', alignItems: 'baseline', gap: 6 },
  dot: { display: 'inline-block', width: 7, height: 7, borderRadius: '50%', flexShrink: 0 },
  findingTitle: { fontSize: 12.5, fontWeight: 600, color: 'var(--text)' },
  findingDetail: { fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4 },
  findingObjects: { fontSize: 10.5, color: 'var(--text-subtle)', fontFamily: 'ui-monospace, monospace', lineHeight: 1.4 },
}
