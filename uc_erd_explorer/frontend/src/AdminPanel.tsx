import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  fetchRefreshRunStatus,
  fetchSnapshotStatus,
  triggerSnapshotRefresh,
  type SnapshotStatus,
} from './api'

/**
 * Top-bar "⚙" control + modal for the metadata snapshot. Shows whether the app is reading
 * live information_schema or the materialized snapshot, when the snapshot was last rebuilt,
 * and (in snapshot mode) a "Refresh now" button that triggers the refresh_erd_snapshot job
 * and polls its run to completion. The refresh action only appears when snapshot mode is
 * actually configured with a wired-up job -- otherwise the panel is purely informational.
 */
const TERMINAL = new Set(['TERMINATED', 'SKIPPED', 'INTERNAL_ERROR'])

export function AdminPanel() {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<SnapshotStatus | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  // Refresh-run tracking.
  const [runId, setRunId] = useState<number | null>(null)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<string | null>(null)
  const [runUrl, setRunUrl] = useState<string | null>(null)
  const [actionErr, setActionErr] = useState<string | null>(null)

  const loadStatus = () => {
    fetchSnapshotStatus()
      .then((s) => {
        setStatus(s)
        setLoadErr(null)
      })
      .catch((e) => setLoadErr((e as Error).message))
  }

  // Load status when the panel opens.
  useEffect(() => {
    if (open) loadStatus()
  }, [open])

  // Poll the refresh run until it reaches a terminal state -- but bounded: stop after
  // MAX_POLLS so a run that never terminates (or a Jobs API stuck non-terminal) doesn't
  // poll every 3s forever while the modal is open. On timeout we stop and point the user
  // at the run page rather than hammering the endpoint.
  useEffect(() => {
    if (runId === null || !running) return
    const MAX_POLLS = 100 // ~5 min at 3s
    let cancelled = false
    let attempts = 0
    const tick = () => {
      attempts += 1
      fetchRefreshRunStatus(runId)
        .then((r) => {
          if (cancelled) return
          setRunUrl(r.run_page_url)
          if (r.life_cycle_state && TERMINAL.has(r.life_cycle_state)) {
            setRunning(false)
            setRunResult(r.result_state ?? r.life_cycle_state)
            loadStatus() // refresh freshness after a completed run
          } else if (attempts >= MAX_POLLS) {
            setRunning(false)
            setRunResult('still running — check the run page')
          } else {
            timer = setTimeout(tick, 3000)
          }
        })
        .catch((e) => {
          if (cancelled) return
          setRunning(false)
          setActionErr((e as Error).message)
        })
    }
    let timer = setTimeout(tick, 2000)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [runId, running])

  const startRefresh = () => {
    setActionErr(null)
    setRunResult(null)
    setRunUrl(null)
    setRunning(true)
    triggerSnapshotRefresh()
      .then((r) => setRunId(r.run_id))
      .catch((e) => {
        setRunning(false)
        setActionErr((e as Error).message)
      })
  }

  const snapshotMode = status?.source_mode === 'snapshot'
  const canRefresh = snapshotMode && status?.job_configured

  return (
    <>
      <button onClick={() => setOpen(true)} aria-label="Metadata snapshot settings" style={styles.trigger}>
        ⚙
      </button>

      {open && (
        <div style={styles.overlay} onClick={() => setOpen(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <div style={styles.header}>
              <div>
                <div style={styles.title}>Metadata snapshot</div>
                <div style={styles.subtitle}>Where the ERD reads its metadata, and when it was last refreshed</div>
              </div>
              <button onClick={() => setOpen(false)} aria-label="Close" style={styles.closeBtn}>
                ×
              </button>
            </div>

            <div style={styles.body}>
              {loadErr && <div style={styles.error}>Couldn’t load status: {loadErr}</div>}

              {status && (
                <>
                  <Row label="Source">
                    <span style={styles.badge}>
                      {snapshotMode ? 'Snapshot (materialized)' : 'Live information_schema'}
                    </span>
                  </Row>
                  <Row label="Last refreshed">
                    {status.snapshot?.refreshed_at ? (
                      <span>{status.snapshot.refreshed_at} UTC</span>
                    ) : (
                      <span style={styles.muted}>
                        {snapshotMode ? 'never built yet — reading live until first refresh' : 'not applicable in live mode'}
                      </span>
                    )}
                  </Row>
                  {status.snapshot?.catalogs && (
                    <Row label="Catalogs">
                      <span style={styles.muted}>{status.snapshot.catalogs}</span>
                    </Row>
                  )}

                  {!snapshotMode && (
                    <p style={styles.note}>
                      This deployment reads <code style={styles.code}>information_schema</code> live.
                      To use the faster materialized snapshot, deploy with{' '}
                      <code style={styles.code}>erd_metadata_source=snapshot</code> and run the
                      refresh job (see the guide).
                    </p>
                  )}

                  {snapshotMode && !status.job_configured && (
                    <p style={styles.note}>
                      Snapshot mode is on, but no refresh job is wired to this deployment, so it
                      can’t be refreshed from here. Run <code style={styles.code}>build_erd_snapshot</code> manually.
                    </p>
                  )}

                  {canRefresh && (
                    <div style={styles.actionRow}>
                      <button onClick={startRefresh} disabled={running} style={styles.refreshBtn}>
                        {running ? 'Refreshing…' : 'Refresh snapshot now'}
                      </button>
                      {running && <span style={styles.muted}>rebuilding the snapshot tables…</span>}
                      {runResult && (
                        <span style={runResult === 'SUCCESS' ? styles.ok : styles.error}>
                          {runResult === 'SUCCESS' ? '✓ refreshed' : `run ended: ${runResult}`}
                        </span>
                      )}
                      {runUrl && (
                        <a href={runUrl} target="_blank" rel="noreferrer" style={styles.link}>
                          view run ↗
                        </a>
                      )}
                    </div>
                  )}
                  {actionErr && <div style={styles.error}>{actionErr}</div>}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={styles.row}>
      <span style={styles.rowLabel}>{label}</span>
      <span style={styles.rowValue}>{children}</span>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  trigger: {
    border: 'none',
    background: 'rgba(255,255,255,0.1)',
    color: '#fff',
    width: 30,
    height: 30,
    borderRadius: 8,
    cursor: 'pointer',
    fontSize: 15,
    lineHeight: 1,
  },
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(16,24,40,0.45)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 50,
  },
  modal: {
    width: 520,
    maxWidth: '92vw',
    maxHeight: '85vh',
    overflowY: 'auto',
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 20px 60px rgba(16,24,40,0.3)',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    padding: '18px 20px',
    background: 'var(--db-navy)',
    color: '#fff',
    borderRadius: '12px 12px 0 0',
  },
  title: { fontWeight: 700, fontSize: 16 },
  subtitle: { fontSize: 12, opacity: 0.8, marginTop: 3 },
  closeBtn: {
    border: 'none',
    background: 'rgba(255,255,255,0.12)',
    color: '#fff',
    width: 28,
    height: 28,
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: 16,
    lineHeight: 1,
  },
  body: { padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 10 },
  row: { display: 'flex', gap: 12, fontSize: 13 },
  rowLabel: { width: 110, flexShrink: 0, color: 'var(--text-muted)', fontWeight: 600 },
  rowValue: { color: 'var(--text)' },
  badge: {
    fontSize: 12,
    fontWeight: 700,
    color: 'var(--db-blue)',
    background: 'var(--db-blue-soft)',
    borderRadius: 6,
    padding: '2px 8px',
  },
  muted: { color: 'var(--text-muted)' },
  note: {
    fontSize: 12.5,
    color: 'var(--text-muted)',
    lineHeight: 1.5,
    background: 'var(--bg)',
    borderRadius: 8,
    padding: '10px 12px',
    margin: 0,
  },
  code: {
    fontFamily: 'ui-monospace, monospace',
    fontSize: 11.5,
    background: 'var(--bg)',
    padding: '1px 5px',
    borderRadius: 4,
  },
  actionRow: { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 4 },
  refreshBtn: {
    border: 'none',
    borderRadius: 8,
    background: 'var(--db-red)',
    color: '#fff',
    padding: '8px 14px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  ok: { color: '#1a7f37', fontSize: 12.5, fontWeight: 600 },
  error: { color: 'var(--db-red)', fontSize: 12.5, fontWeight: 500 },
  link: { color: 'var(--db-blue)', fontSize: 12.5, fontWeight: 600 },
}
