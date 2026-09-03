import { useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

/**
 * "?" trigger in the top bar + a centered modal documenting how to use the app and how
 * to reconfigure its deployment (e.g. adding a catalog). Static content, not fetched from
 * the server -- this is operator/user documentation, not runtime config, so it lives
 * here rather than in server/config.py. Keep this in sync with README.md's "Route 1/2"
 * and "Configuration reference" sections if either changes.
 */
export function InfoPanel() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Help & info"
        style={styles.trigger}
      >
        ?
      </button>

      {open && (
        <div style={styles.overlay} onClick={() => setOpen(false)} onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Catalog ERD Explorer guide">
            <div style={styles.header}>
              <div>
                <div style={styles.title}>Catalog ERD Explorer — Guide</div>
                <div style={styles.subtitle}>How to use this app, what it does, and how to update its configuration</div>
              </div>
              <button onClick={() => setOpen(false)} aria-label="Close" style={styles.closeBtn}>
                ×
              </button>
            </div>

            <div style={styles.body}>
              <Section title="Overview">
                <p style={styles.p}>
                  Catalog ERD Explorer renders your Unity Catalog schema as an interactive
                  entity-relationship diagram, generated live from{' '}
                  <code style={styles.code}>information_schema</code> — no manual modeling.
                  A built-in Genie Space lets you ask natural-language questions about table
                  structure alongside the diagram.
                </p>
              </Section>

              <Section title="Using the app">
                <Item title="Catalogs &amp; Schemas (left sidebar)">
                  Check the catalog/schema pairs you want rendered. Nothing checked = &quot;All&quot;,
                  everything currently in scope for this deployment.
                </Item>
                <Item title="Environment toggle">
                  Switches between Prod and a parallel Test catalog set (each configured
                  catalog with a suffix, e.g. <code style={styles.code}>_ts</code>). Only enabled
                  when this deployment is scoped to specific catalogs and test catalogs exist.
                </Item>
                <Item title="Search">
                  Jump straight to a table by name; centers and selects it on the canvas.
                </Item>
                <Item title="Filter mode + click-to-focus">
                  Click any table to spotlight either its <strong>direct neighbors</strong> or its
                  whole <strong>connected component</strong>, dimming the rest. &quot;Reset view&quot;
                  clears the selection.
                </Item>
                <Item title="Undeclared relationships">
                  Toggle on to show dashed edges: likely-but-undeclared foreign keys, inferred by
                  matching column names/types against primary keys. A heuristic guess, never a
                  real constraint — off by default.
                </Item>
                <Item title="Export panel (top-right of canvas)">
                  PNG/SVG images, Markdown/YAML/JSON schema docs, or an ER/Studio-importable DDL
                  bundle (SQL Server or Oracle dialect). Scoped to your current click-selection if
                  one is active, otherwise the whole visible graph. Requires a schema to be
                  expanded first (not available in the collapsed summary view).
                </Item>
                <Item title="Ask Genie (bottom-right)">
                  Chats with a Genie Space scoped to just this deployment's table/column/FK
                  metadata — it can answer structural questions (&quot;which tables reference
                  X?&quot;) but never sees your actual business data.
                </Item>
              </Section>

              <Section title="Features">
                <ul style={styles.ul}>
                  <li>Live ERD from Unity Catalog metadata (refreshed on the server's cache TTL)</li>
                  <li>Multi-catalog / multi-schema support with per-catalog color coding</li>
                  <li>Schema-summary collapse for large schemas — click a summary node to expand</li>
                  <li>Declared foreign keys plus optional inferred (heuristic) relationships</li>
                  <li>Prod/Test catalog toggle for side-by-side environments</li>
                  <li>Export to images, schema docs, or a modeling-tool-ready DDL bundle</li>
                  <li>Natural-language Genie chat scoped to schema structure only</li>
                </ul>
              </Section>

              <Section title="Updating this deployment">
                <p style={styles.p}>
                  These are maintainer actions — done from the Databricks CLI or the
                  <code style={styles.code}> notebooks/install.py</code> notebook, against the
                  <code style={styles.code}> demo-assets</code> git checkout, not from inside this
                  running app. There&apos;s intentionally no in-app &quot;add a catalog&quot;
                  control — doing that from here would mean giving this app&apos;s service
                  principal grant-issuing UC permissions and the ability to redeploy itself, a much
                  bigger permission footprint than an ERD viewer needs.
                </p>

                <Item title="Add a new catalog to the scope">
                  <ol style={styles.ol}>
                    <li>
                      Redeploy with the catalog added to <code style={styles.code}>erd_catalogs</code>
                      {' '}— CLI: <code style={styles.code}>databricks bundle deploy --var=&quot;erd_catalogs=...,newcatalog&quot;</code>,
                      or notebook: edit the <code style={styles.code}>erd_catalogs</code> widget and
                      Run all. The ERD graph picks this up immediately.
                    </li>
                    <li>
                      Grant the app&apos;s service principal access to the new catalog — run{' '}
                      <code style={styles.code}>setup/grant_catalog_access.py</code> (idempotent; safe
                      to pass the full catalog list, old and new). The notebook route does this for
                      you automatically.
                    </li>
                    <li>
                      Resync the Genie Space — <code style={styles.code}>databricks bundle run
                      setup_genie_space</code>, or automatic via the notebook. Unlike the graph, the
                      Genie Space's view/table list is saved config and won't pick up the new catalog
                      until you resync it.
                    </li>
                  </ol>
                </Item>

                <p style={styles.p}>
                  See the repo README&apos;s &quot;Route 1/Route 2&quot; and &quot;Configuration
                  reference&quot; sections for full walkthroughs, including unscoped mode and setting
                  up Prod/Test test catalogs.
                </p>
              </Section>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionTitle}>{title}</div>
      {children}
    </div>
  )
}

function Item({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div style={styles.item}>
      <div style={styles.itemTitle}>{title}</div>
      <div style={styles.itemBody}>{children}</div>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  trigger: {
    width: 30,
    height: 30,
    flexShrink: 0,
    borderRadius: '50%',
    border: '1px solid rgba(255,255,255,0.28)',
    background: 'rgba(255,255,255,0.1)',
    color: 'var(--on-accent)',
    fontSize: 14,
    fontWeight: 700,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(16,24,40,0.45)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 40,
  },
  modal: {
    width: 640,
    maxWidth: '92vw',
    maxHeight: '86vh',
    background: 'var(--surface)',
    borderRadius: 'var(--radius)',
    boxShadow: 'var(--shadow-lg)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
    padding: '16px 20px',
    background: 'var(--db-navy)',
    color: 'var(--on-accent)',
    flexShrink: 0,
  },
  title: { fontSize: 16, fontWeight: 700 },
  subtitle: { fontSize: 12, opacity: 0.75, marginTop: 3 },
  closeBtn: {
    border: 'none',
    background: 'rgba(255,255,255,0.12)',
    color: 'var(--on-accent)',
    width: 28,
    height: 28,
    borderRadius: 7,
    cursor: 'pointer',
    fontSize: 16,
    lineHeight: 1,
    flexShrink: 0,
  },
  body: {
    flex: 1,
    overflowY: 'auto',
    padding: '4px 20px 20px',
  },
  section: { marginTop: 18 },
  sectionTitle: {
    fontSize: 12.5,
    fontWeight: 700,
    letterSpacing: 0.3,
    textTransform: 'uppercase',
    color: 'var(--db-red)',
    marginBottom: 8,
  },
  item: { marginBottom: 10 },
  itemTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 2 },
  itemBody: { fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.55 },
  p: { fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.55, margin: '0 0 8px' },
  ul: { margin: '0 0 8px', paddingLeft: 18, fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6 },
  ol: { margin: '6px 0 0', paddingLeft: 18, fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6 },
  code: {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontSize: 12,
    background: 'var(--db-blue-soft)',
    color: 'var(--db-blue)',
    padding: '1px 5px',
    borderRadius: 4,
  },
}
