import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  MarkerType,
  Panel,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from 'reactflow'
import 'reactflow/dist/style.css'

import { fetchConfig, fetchGraph, fetchSchemaTree } from './api'
import { TableNode } from './TableNode'
import { SchemaNode } from './SchemaNode'
import { GeniePanel } from './GeniePanel'
import { CatalogSchemaPicker } from './CatalogSchemaPicker'
import { connectedComponent, directNeighbors, layoutGraph } from './graphUtils'
import { exportGraphAsImage, exportGraphAsMarkdown } from './export'
import type {
  CatalogSchemas,
  FilterMode,
  GraphResponse,
  SchemaNodeData,
  TableNodeData,
} from './types'

const nodeTypes = { table: TableNode, schema: SchemaNode }

function isSchemaNodeData(data: TableNodeData | SchemaNodeData): data is SchemaNodeData {
  return !('columns' in data)
}

function ErdCanvas() {
  const [graph, setGraph] = useState<GraphResponse | null>(null)
  const [tree, setTree] = useState<CatalogSchemas[]>([])
  const [treeUnscoped, setTreeUnscoped] = useState(false)
  const [workspaceName, setWorkspaceName] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // null = "All" -- no filter, everything in scope.
  const [selectedPairs, setSelectedPairs] = useState<Set<string> | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filterMode, setFilterMode] = useState<FilterMode>('neighbors')
  const [search, setSearch] = useState('')
  // Heuristic undeclared-relationship edges are always fetched but hidden by default,
  // so first load renders identically to before this feature existed.
  const [showInferred, setShowInferred] = useState(false)

  const { fitView, setCenter, getNode } = useReactFlow()
  const laidOutRef = useRef<Node<TableNodeData | SchemaNodeData>[]>([])

  // Load the catalog/schema tree once, to populate the picker.
  useEffect(() => {
    fetchSchemaTree()
      .then((t) => {
        setTree(t.catalogs)
        setTreeUnscoped(t.unscoped)
      })
      .catch((e) => setError((e as Error).message))
  }, [])

  // Load the deployment's workspace name once, for the top-bar pill.
  useEffect(() => {
    fetchConfig()
      .then((c) => setWorkspaceName(c.workspace))
      .catch(() => setWorkspaceName(null))
  }, [])

  // Load graph whenever the catalog/schema selection changes.
  useEffect(() => {
    let cancelled = false
    setError(null)
    setSelectedId(null)

    // An explicit, empty selection (nothing checked) means "show nothing" -- not the
    // same as null ("All"). The backend has no way to express "no pairs = empty" (a bare
    // /api/graph means everything in scope), so short-circuit to an empty graph here
    // instead of firing a request that would come back with the full catalog.
    if (selectedPairs && selectedPairs.size === 0) {
      setGraph({ catalogs: [], unscoped: treeUnscoped, pairs: [], view: 'detail', nodes: [], edges: [] })
      setLoading(false)
      return
    }

    setLoading(true)
    fetchGraph(selectedPairs ? Array.from(selectedPairs) : undefined)
      .then((g) => {
        if (!cancelled) setGraph(g)
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedPairs, treeUnscoped])

  // Edges filtered to the current inferred-visibility toggle -- used for both rendering
  // and click-to-filter connectivity, so a hidden inferred edge never silently changes
  // what "connected" means. Default (showInferred=false) matches pre-heuristic behavior
  // exactly, since the backend always returns inferred edges tagged, never omitted.
  const scopedGraphEdges = useMemo(
    () => (graph ? graph.edges.filter((e) => showInferred || !e.inferred) : []),
    [graph, showInferred],
  )

  // Base (laid-out) nodes + edges derived from the loaded graph.
  const { baseNodes, baseEdges } = useMemo(() => {
    if (!graph) {
      return { baseNodes: [] as Node<TableNodeData | SchemaNodeData>[], baseEdges: [] as Edge[] }
    }

    const rawNodes: Node<TableNodeData | SchemaNodeData>[] = graph.nodes.map((n) => ({
      id: n.id,
      type: graph.view === 'schema_summary' ? 'schema' : 'table',
      position: { x: 0, y: 0 },
      data: n,
    }))

    const edges: Edge[] = scopedGraphEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      data: { inferred: e.inferred },
      label: e.inferred
        ? `${e.fk_columns.join(', ')} → ${e.pk_columns.join(', ')} (inferred)`
        : `${e.fk_columns.join(', ')} → ${e.pk_columns.join(', ')}`,
      type: 'smoothstep',
      // Inferred edges render above declared ones so a dashed line that happens to
      // overlap a solid one is never fully hidden underneath it.
      zIndex: e.inferred ? 1 : 0,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color: e.inferred ? 'var(--db-red)' : '#98a2b3',
      },
      labelStyle: { fontSize: 10, fontWeight: e.inferred ? 700 : 400, fill: e.inferred ? 'var(--db-red)' : '#667085' },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.95 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      style: {
        stroke: e.inferred ? 'var(--db-red)' : '#98a2b3',
        strokeWidth: e.inferred ? 2 : 1.5,
        strokeDasharray: e.inferred ? '6 4' : undefined,
      },
    }))

    const laidOut = layoutGraph(rawNodes, edges)
    laidOutRef.current = laidOut
    return { baseNodes: laidOut, baseEdges: edges }
  }, [graph, scopedGraphEdges])

  // Compute the currently-visible set based on selection + mode.
  const visibleSet = useMemo<Set<string> | null>(() => {
    if (!selectedId || !graph) return null
    return filterMode === 'neighbors'
      ? directNeighbors(selectedId, scopedGraphEdges)
      : connectedComponent(selectedId, scopedGraphEdges)
  }, [selectedId, filterMode, graph, scopedGraphEdges])

  // Apply dim/selection styling to nodes.
  const displayNodes = useMemo<Node<TableNodeData | SchemaNodeData>[]>(() => {
    return baseNodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        dimmed: visibleSet ? !visibleSet.has(n.id) : false,
        selected: n.id === selectedId,
      },
    }))
  }, [baseNodes, visibleSet, selectedId])

  const displayEdges = useMemo<Edge[]>(() => {
    return baseEdges.map((e) => {
      const inSet =
        !visibleSet || (visibleSet.has(e.source) && visibleSet.has(e.target))
      const inferred = Boolean((e.data as { inferred?: boolean } | undefined)?.inferred)
      return {
        ...e,
        style: {
          ...e.style,
          opacity: inSet ? 1 : 0.1,
          stroke: inSet ? (inferred ? 'var(--db-red)' : '#98a2b3') : '#e4e7ec',
        },
        animated: Boolean(selectedId) && inSet,
      }
    })
  }, [baseEdges, visibleSet, selectedId])

  const onNodeClick: NodeMouseHandler = useCallback((_, node) => {
    const data = node.data as TableNodeData | SchemaNodeData
    if (isSchemaNodeData(data)) {
      // "Expand" a collapsed schema node by selecting just that schema in the tree
      // picker -- the same pairs-based mechanism the sidebar picker uses, which
      // re-fetches /api/graph scoped to it and always returns full table-level detail.
      setSelectedPairs(new Set([node.id]))
      return
    }
    setSelectedId((cur) => (cur === node.id ? null : node.id))
  }, [])

  const reset = useCallback(() => {
    setSelectedId(null)
    setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 0)
  }, [fitView])

  // Disabled in the collapsed schema-summary view -- there's no table/column detail to
  // export until a schema is expanded, and an image of two big summary boxes isn't
  // useful as a "schema doc" export either.
  const canExport = graph !== null && graph.view === 'detail' && displayNodes.length > 0

  const handleExportImage = useCallback(
    (format: 'png' | 'svg') => {
      if (!canExport) return
      exportGraphAsImage(displayNodes, format, 'erd-export')
    },
    [canExport, displayNodes],
  )

  const handleExportDocs = useCallback(() => {
    if (!canExport || !graph) return
    exportGraphAsMarkdown(graph, 'erd-schema-docs')
  }, [canExport, graph])

  // Fit view when a fresh graph loads.
  useEffect(() => {
    if (baseNodes.length > 0) {
      setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 50)
    }
  }, [baseNodes, fitView])

  const runSearch = useCallback(() => {
    const q = search.trim().toLowerCase()
    if (!q) return
    // Only meaningful for actual table nodes -- in the collapsed schema-summary view
    // there are no table names to search yet (select a schema to expand it first).
    const nodes = laidOutRef.current.filter(
      (n): n is Node<TableNodeData> => !isSchemaNodeData(n.data),
    )
    // Prefer an exact table-name match (so "materials" hits factory.materials,
    // not bill_of_materials), then fall back to a substring match.
    const match =
      nodes.find((n) => n.data.table.toLowerCase() === q) ??
      nodes.find((n) => n.data.table.toLowerCase().includes(q))
    if (match) {
      const node = getNode(match.id)
      if (node) {
        setCenter(node.position.x + 120, node.position.y + 80, {
          zoom: 1.1,
          duration: 500,
        })
        setSelectedId(match.id)
      }
    }
  }, [search, getNode, setCenter])

  const selectedTable = selectedId
    ? selectedId.split('.').slice(1).join('.')
    : null

  const inferredCount = graph ? graph.edges.filter((e) => e.inferred).length : 0
  const declaredCount = graph ? graph.edges.filter((e) => !e.inferred).length : 0

  return (
    <div style={styles.app}>
      {/* Top bar */}
      <header style={styles.topbar}>
        <div style={styles.brandMark}>
          <span style={styles.brandLogo}>❯</span>
          <span style={styles.brandWord}>databricks</span>
        </div>
        <div style={styles.topDivider} />
        <div style={styles.appTitle}>Catalog ERD Explorer</div>
        <div style={styles.topSpacer} />
        {workspaceName && (
          <div style={styles.workspacePill}>
            <span style={styles.workspaceDot} />
            {workspaceName}
          </div>
        )}
        <div style={styles.avatar}>EF</div>
      </header>

      <div style={styles.body}>
        {/* Left sidebar */}
        <aside style={styles.sidebar}>
          <SectionLabel>Catalog</SectionLabel>
          <div style={styles.catalogCard}>
            <div style={styles.catalogName}>
              <span style={styles.catalogIcon}>▦</span>
              {tree.length === 1 ? tree[0].catalog : `${tree.length} catalogs`}
            </div>
            <div style={styles.catalogMeta}>
              Unity Catalog{treeUnscoped ? ' · unscoped (all visible)' : ''}
            </div>
          </div>

          <SectionLabel>Catalogs &amp; Schemas</SectionLabel>
          <div style={styles.card}>
            <CatalogSchemaPicker
              tree={tree}
              selectedPairs={selectedPairs}
              onChange={setSelectedPairs}
            />
          </div>

          <SectionLabel>Search</SectionLabel>
          <div style={styles.card}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runSearch()}
                placeholder="Find a table…"
                style={styles.searchInput}
              />
              <button onClick={runSearch} style={styles.searchBtn}>
                Go
              </button>
            </div>
          </div>

          <SectionLabel>Filter mode</SectionLabel>
          <div style={styles.card}>
            {(['neighbors', 'component'] as FilterMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setFilterMode(m)}
                style={sidebarRow(filterMode === m)}
              >
                <span style={{ flex: 1 }}>
                  {m === 'neighbors' ? 'Direct neighbors' : 'Connected component'}
                </span>
                {filterMode === m && <span style={styles.check}>✓</span>}
              </button>
            ))}
            <div style={styles.hint}>
              Click a table to focus its {filterMode === 'neighbors' ? '1-hop neighbors' : 'connected component'}.
            </div>
            <button
              onClick={reset}
              disabled={!selectedId}
              style={{
                ...styles.resetBtn,
                opacity: selectedId ? 1 : 0.45,
                cursor: selectedId ? 'pointer' : 'default',
              }}
            >
              Reset view
            </button>
          </div>

          <SectionLabel>Undeclared relationships</SectionLabel>
          <div style={styles.card}>
            <Switch
              label="Show inferred edges"
              checked={showInferred}
              onChange={() => setShowInferred((v) => !v)}
            />
            <div style={styles.hint}>
              {inferredCount > 0
                ? `${inferredCount} likely-but-undeclared relationship${inferredCount === 1 ? '' : 's'} found by matching column names/types against primary keys -- a guess, never a real constraint.`
                : 'No undeclared relationships detected via column name/type matching.'}
            </div>
          </div>

          <div style={styles.statsBox}>
            <Stat
              label="Tables"
              value={graph ? String(graph.nodes.length) : '—'}
            />
            <Stat
              label="Relationships"
              value={graph ? String(declaredCount) : '—'}
            />
          </div>
          {selectedTable && (
            <div style={styles.selectedTag}>
              <span style={styles.selectedTagDot} />
              {selectedTable}
            </div>
          )}
        </aside>

        {/* Canvas */}
        <main style={styles.canvasWrap}>
          {loading && <div style={styles.centerMsg}>Loading schema…</div>}
          {error && (
            <div style={{ ...styles.centerMsg, color: 'var(--db-red)' }}>
              Error: {error}
            </div>
          )}
          <ReactFlow
            nodes={displayNodes}
            edges={displayEdges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            onPaneClick={() => setSelectedId(null)}
            fitView
            minZoom={0.1}
            proOptions={{ hideAttribution: true }}
            style={{ background: 'var(--bg)' }}
          >
            <Background color="#e4e7ec" gap={22} />
            <Controls />
            <MiniMap
              nodeColor={(n) =>
                (n.data as TableNodeData | SchemaNodeData)?.schema === 'erp'
                  ? '#7c3aed'
                  : '#2272b4'
              }
              maskColor="rgba(246,247,249,0.7)"
              pannable
              zoomable
            />
            <Panel position="top-right">
              <div style={styles.exportPanel} title={canExport ? undefined : 'Expand a schema first to export'}>
                <span style={styles.exportLabel}>Export</span>
                <button
                  onClick={() => handleExportImage('png')}
                  disabled={!canExport}
                  style={exportBtn(canExport)}
                >
                  PNG
                </button>
                <button
                  onClick={() => handleExportImage('svg')}
                  disabled={!canExport}
                  style={exportBtn(canExport)}
                >
                  SVG
                </button>
                <button
                  onClick={handleExportDocs}
                  disabled={!canExport}
                  style={exportBtn(canExport)}
                >
                  Docs (.md)
                </button>
              </div>
            </Panel>
          </ReactFlow>
        </main>
      </div>

      <GeniePanel />
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={styles.sectionLabel}>{children}</div>
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.stat}>
      <div style={styles.statValue}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  )
}

function Switch({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: () => void
}) {
  return (
    <button
      onClick={onChange}
      role="switch"
      aria-checked={checked}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 10,
        width: '100%',
        padding: '7px 8px',
        borderRadius: 7,
        border: 'none',
        background: 'transparent',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{label}</span>
      <span
        style={{
          position: 'relative',
          width: 34,
          height: 19,
          flexShrink: 0,
          borderRadius: 999,
          background: checked ? 'var(--db-red)' : '#d0d5dd',
          transition: 'background 0.15s ease',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 2,
            left: checked ? 17 : 2,
            width: 15,
            height: 15,
            borderRadius: '50%',
            background: '#fff',
            boxShadow: '0 1px 2px rgba(16,24,40,0.2)',
            transition: 'left 0.15s ease',
          }}
        />
      </span>
    </button>
  )
}

function sidebarRow(active: boolean): CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    padding: '8px 10px',
    borderRadius: 8,
    border: 'none',
    background: active ? 'var(--db-blue-soft)' : 'transparent',
    color: active ? 'var(--db-blue)' : 'var(--text)',
    fontWeight: active ? 600 : 500,
    fontSize: 13,
    cursor: 'pointer',
    textAlign: 'left',
  }
}

function exportBtn(enabled: boolean): CSSProperties {
  return {
    border: '1px solid var(--border, #e4e7ec)',
    borderRadius: 6,
    background: '#fff',
    color: enabled ? 'var(--text)' : '#c0c5cd',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 8px',
    cursor: enabled ? 'pointer' : 'default',
  }
}

const styles: Record<string, CSSProperties> = {
  exportPanel: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    background: '#fff',
    border: '1px solid #e4e7ec',
    borderRadius: 8,
    padding: '6px 8px',
    boxShadow: '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.1)',
  },
  exportLabel: {
    fontSize: 10.5,
    fontWeight: 700,
    color: '#98a2b3',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    marginRight: 2,
  },
  app: {
    width: '100vw',
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--bg)',
  },
  topbar: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    height: 52,
    padding: '0 18px',
    background: 'var(--db-navy)',
    color: '#fff',
    flexShrink: 0,
  },
  brandMark: { display: 'flex', alignItems: 'center', gap: 8 },
  brandLogo: {
    color: 'var(--db-red)',
    fontWeight: 800,
    fontSize: 18,
    transform: 'scaleX(1.1)',
  },
  brandWord: { fontWeight: 700, letterSpacing: -0.2, fontSize: 15 },
  topDivider: { width: 1, height: 22, background: 'rgba(255,255,255,0.18)' },
  appTitle: { fontSize: 14, fontWeight: 500, opacity: 0.92 },
  topSpacer: { flex: 1 },
  workspacePill: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    fontSize: 12,
    padding: '5px 10px',
    borderRadius: 8,
    background: 'rgba(255,255,255,0.1)',
  },
  workspaceDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: '#32d583',
  },
  avatar: {
    width: 30,
    height: 30,
    borderRadius: '50%',
    background: 'var(--db-red)',
    color: '#fff',
    fontSize: 11,
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  body: { flex: 1, display: 'flex', minHeight: 0 },
  sidebar: {
    width: 268,
    flexShrink: 0,
    background: 'var(--surface)',
    borderRight: '1px solid var(--border)',
    padding: '16px 14px',
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    color: 'var(--text-subtle)',
    margin: '12px 4px 6px',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: 6,
    boxShadow: 'var(--shadow-sm)',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  catalogCard: {
    background: 'linear-gradient(135deg,#ffffff,#faf6f4)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '12px 14px',
    boxShadow: 'var(--shadow-sm)',
  },
  catalogName: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 15,
    fontWeight: 700,
    color: 'var(--text)',
  },
  catalogIcon: { color: 'var(--db-red)', fontSize: 14 },
  catalogMeta: { fontSize: 11.5, color: 'var(--text-muted)', marginTop: 4 },
  check: { color: 'var(--db-blue)', fontSize: 12, fontWeight: 700 },
  searchInput: {
    flex: 1,
    padding: '8px 10px',
    borderRadius: 8,
    border: '1px solid var(--border-strong)',
    fontSize: 13,
    outline: 'none',
    color: 'var(--text)',
  },
  searchBtn: {
    border: 'none',
    borderRadius: 8,
    background: 'var(--db-blue)',
    color: '#fff',
    padding: '0 14px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
  },
  hint: {
    fontSize: 11.5,
    color: 'var(--text-muted)',
    padding: '6px 10px 2px',
    lineHeight: 1.4,
  },
  resetBtn: {
    marginTop: 6,
    border: '1px solid var(--db-red)',
    borderRadius: 8,
    background: 'var(--surface)',
    color: 'var(--db-red)',
    padding: '8px 10px',
    fontSize: 13,
    fontWeight: 600,
    width: '100%',
  },
  statsBox: {
    display: 'flex',
    gap: 8,
    marginTop: 14,
  },
  stat: {
    flex: 1,
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '10px 12px',
    boxShadow: 'var(--shadow-sm)',
  },
  statValue: { fontSize: 20, fontWeight: 700, color: 'var(--text)' },
  statLabel: {
    fontSize: 11,
    color: 'var(--text-muted)',
    marginTop: 2,
  },
  selectedTag: {
    marginTop: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--db-blue)',
    background: 'var(--db-blue-soft)',
    border: '1px solid #cfe0f4',
    borderRadius: 8,
    padding: '7px 10px',
  },
  selectedTagDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: 'var(--db-blue)',
  },
  canvasWrap: { flex: 1, position: 'relative', minWidth: 0 },
  centerMsg: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    fontSize: 15,
    color: 'var(--text-muted)',
    zIndex: 10,
  },
}

export default function App() {
  return (
    <ReactFlowProvider>
      <ErdCanvas />
    </ReactFlowProvider>
  )
}
