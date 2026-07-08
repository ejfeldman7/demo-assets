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
import { RelationshipEdge } from './RelationshipEdge'
import { GeniePanel } from './GeniePanel'
import { CatalogSchemaPicker } from './CatalogSchemaPicker'
import { connectedComponent, directNeighbors, layoutGraph } from './graphUtils'
import {
  exportGraphAsErStudioZip,
  exportGraphAsImage,
  exportGraphAsJson,
  exportGraphAsMarkdown,
  exportGraphAsYaml,
  scopeGraph,
  type ExportScope,
} from './export'
import { buildCatalogColorMap, lookupCatalogColor } from './catalogColors'
import type { Dialect } from './erstudio/typeMapping'
import type {
  CatalogEnv,
  CatalogSchemas,
  FilterMode,
  GraphResponse,
  SchemaNodeData,
  TableNodeData,
} from './types'

const nodeTypes = { table: TableNode, schema: SchemaNode }
const edgeTypes = { relationship: RelationshipEdge }

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
  // Prod/Test toggle: 'test' queries each configured catalog with testCatalogSuffix
  // appended (e.g. edp_customer -> edp_customer_ts) -- two distinct real Unity Catalog
  // catalogs, not an alias. Only meaningful for a scoped deployment (testAvailable).
  const [env, setEnv] = useState<CatalogEnv>('prod')
  const [testAvailable, setTestAvailable] = useState(false)
  const [testCatalogSuffix, setTestCatalogSuffix] = useState('_ts')
  // null = "All" -- no filter, everything in scope.
  const [selectedPairs, setSelectedPairs] = useState<Set<string> | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filterMode, setFilterMode] = useState<FilterMode>('neighbors')
  const [search, setSearch] = useState('')
  // Heuristic undeclared-relationship edges are always fetched but hidden by default,
  // so first load renders identically to before this feature existed.
  const [showInferred, setShowInferred] = useState(false)
  const [erStudioDialect, setErStudioDialect] = useState<Dialect>('sqlserver')

  const { fitView, setCenter, getNode } = useReactFlow()
  const laidOutRef = useRef<Node<TableNodeData | SchemaNodeData>[]>([])

  // Load the catalog/schema tree whenever the Prod/Test environment changes, to
  // populate the picker with that environment's actual (possibly _ts-suffixed) catalogs.
  useEffect(() => {
    fetchSchemaTree(env)
      .then((t) => {
        setTree(t.catalogs)
        setTreeUnscoped(t.unscoped)
      })
      .catch((e) => setError((e as Error).message))
  }, [env])

  // Load the deployment's workspace name + Prod/Test toggle availability once.
  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setWorkspaceName(c.workspace)
        setTestAvailable(c.test_available)
        setTestCatalogSuffix(c.test_catalog_suffix)
      })
      .catch(() => setWorkspaceName(null))
  }, [])

  // Toggling Prod/Test resets the catalog/schema selection -- a selection made under one
  // environment names catalogs (possibly _ts-suffixed) that don't exist under the other.
  const handleEnvChange = useCallback(() => {
    setSelectedPairs(null)
    setEnv((e) => (e === 'prod' ? 'test' : 'prod'))
  }, [])

  // Load graph whenever the catalog/schema selection or environment changes.
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
    fetchGraph(selectedPairs ? Array.from(selectedPairs) : undefined, env)
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
  }, [selectedPairs, treeUnscoped, env])

  // Colors assigned per the catalogs actually present in the current (already
  // catalog/schema-scoped) graph -- see catalogColors.ts for why this is computed fresh
  // per graph rather than from a fixed name->color lookup.
  const catalogColorMap = useMemo(() => buildCatalogColorMap(graph?.catalogs ?? []), [graph])

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
      // Custom type, not the built-in 'smoothstep' -- its label renders through
      // EdgeLabelRenderer (a layer above nodes) instead of inline SVG <text> (a layer
      // below nodes), so labels aren't hidden under table cards. See RelationshipEdge.tsx.
      type: 'relationship',
      // Inferred edges render above declared ones so a dashed line that happens to
      // overlap a solid one is never fully hidden underneath it.
      zIndex: e.inferred ? 1 : 0,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color: e.inferred ? 'var(--db-red)' : '#98a2b3',
      },
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

  // Apply dim/selection styling + catalog color to nodes.
  const displayNodes = useMemo<Node<TableNodeData | SchemaNodeData>[]>(() => {
    return baseNodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        dimmed: visibleSet ? !visibleSet.has(n.id) : false,
        selected: n.id === selectedId,
        color: lookupCatalogColor(catalogColorMap, n.data.catalog),
      },
    }))
  }, [baseNodes, visibleSet, selectedId, catalogColorMap])

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

  // The active click-to-filter selection, translated into what the export functions
  // need (node ids + the specific edge ids connecting them) -- null when nothing's
  // selected, meaning exports cover the full catalog/schema-scoped graph as before.
  const exportScope = useMemo<ExportScope | null>(() => {
    if (!visibleSet) return null
    return {
      nodeIds: visibleSet,
      edgeIds: new Set(
        displayEdges.filter((e) => visibleSet.has(e.source) && visibleSet.has(e.target)).map((e) => e.id),
      ),
    }
  }, [visibleSet, displayEdges])

  const handleExportImage = useCallback(
    (format: 'png' | 'svg') => {
      if (!canExport) return
      exportGraphAsImage(displayNodes, format, 'erd-export', exportScope)
    },
    [canExport, displayNodes, exportScope],
  )

  const handleExportDocs = useCallback(
    (format: 'md' | 'json' | 'yaml') => {
      if (!canExport || !graph) return
      const scoped = scopeGraph(graph, exportScope)
      if (format === 'md') exportGraphAsMarkdown(scoped, 'erd-schema-docs')
      else if (format === 'json') exportGraphAsJson(scoped, 'erd-schema-docs')
      else exportGraphAsYaml(scoped, 'erd-schema-docs')
    },
    [canExport, graph, exportScope],
  )

  const handleExportErStudio = useCallback(() => {
    if (!canExport || !graph) return
    const scoped = scopeGraph(graph, exportScope)
    exportGraphAsErStudioZip(scoped, erStudioDialect, 'erd-erstudio-export')
  }, [canExport, graph, exportScope, erStudioDialect])

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

          <SectionLabel>Environment</SectionLabel>
          <div style={styles.card}>
            <Switch
              label={env === 'test' ? 'Test' : 'Prod'}
              checked={env === 'test'}
              onChange={handleEnvChange}
              disabled={!testAvailable}
            />
            <div style={styles.hint}>
              {testAvailable
                ? `Test appends "${testCatalogSuffix}" to each configured catalog (e.g. a catalog named "edp_customer" becomes "edp_customer${testCatalogSuffix}").`
                : 'Prod/Test toggle is only available for a deployment scoped to specific catalogs (ERD_CATALOGS).'}
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
            edgeTypes={edgeTypes}
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
                lookupCatalogColor(catalogColorMap, (n.data as TableNodeData | SchemaNodeData)?.catalog ?? '').bar
              }
              maskColor="rgba(246,247,249,0.7)"
              pannable
              zoomable
            />
            <Panel position="top-right">
              <div
                style={styles.exportPanel}
                title={
                  !canExport
                    ? 'Expand a schema first to export'
                    : exportScope
                      ? 'Exporting just the selected table and its connections'
                      : undefined
                }
              >
                <div style={styles.exportHeader}>
                  <span style={styles.exportLabel}>Export</span>
                  {exportScope && <span style={styles.exportScopeTag}>selection only</span>}
                </div>

                <div style={styles.exportRow}>
                  <span style={styles.exportGroupTag}>Image</span>
                  <button onClick={() => handleExportImage('png')} disabled={!canExport} style={exportBtn(canExport)}>
                    PNG
                  </button>
                  <button onClick={() => handleExportImage('svg')} disabled={!canExport} style={exportBtn(canExport)}>
                    SVG
                  </button>
                </div>

                <div style={styles.exportRow}>
                  <span style={styles.exportGroupTag}>Docs</span>
                  <button onClick={() => handleExportDocs('md')} disabled={!canExport} style={exportBtn(canExport)}>
                    MD
                  </button>
                  <button onClick={() => handleExportDocs('yaml')} disabled={!canExport} style={exportBtn(canExport)}>
                    YAML
                  </button>
                  <button onClick={() => handleExportDocs('json')} disabled={!canExport} style={exportBtn(canExport)}>
                    JSON
                  </button>
                </div>

                <div style={styles.exportRow}>
                  <span style={styles.exportGroupTag}>Model</span>
                  <select
                    value={erStudioDialect}
                    onChange={(e) => setErStudioDialect(e.target.value as Dialect)}
                    disabled={!canExport}
                    style={exportDialectSelect(canExport)}
                  >
                    <option value="sqlserver">SQL Server</option>
                    <option value="oracle">Oracle</option>
                  </select>
                  <button
                    onClick={handleExportErStudio}
                    disabled={!canExport}
                    style={{ ...exportBtn(canExport), flex: 1 }}
                    title="DDL + column metadata, for ER/Studio's reverse-engineer-from-DDL import"
                  >
                    ER/Studio
                  </button>
                </div>
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
  disabled,
}: {
  label: string
  checked: boolean
  onChange: () => void
  disabled?: boolean
}) {
  return (
    <button
      onClick={onChange}
      disabled={disabled}
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
        cursor: disabled ? 'default' : 'pointer',
        textAlign: 'left',
        opacity: disabled ? 0.5 : 1,
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
    whiteSpace: 'nowrap',
  }
}

function exportDialectSelect(enabled: boolean): CSSProperties {
  return {
    border: '1px solid var(--border, #e4e7ec)',
    borderRadius: 6,
    background: '#fff',
    color: enabled ? 'var(--text)' : '#c0c5cd',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 6px',
    cursor: enabled ? 'pointer' : 'default',
  }
}

const styles: Record<string, CSSProperties> = {
  exportPanel: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    width: 236,
    background: '#fff',
    border: '1px solid #e4e7ec',
    borderRadius: 8,
    padding: '8px',
    boxShadow: '0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.1)',
  },
  exportHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 2,
  },
  exportLabel: {
    fontSize: 10.5,
    fontWeight: 700,
    color: '#98a2b3',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  exportScopeTag: {
    fontSize: 9.5,
    fontWeight: 600,
    color: 'var(--db-blue)',
    background: 'var(--db-blue-soft)',
    borderRadius: 4,
    padding: '1px 6px',
  },
  exportRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
  },
  exportGroupTag: {
    width: 38,
    flexShrink: 0,
    fontSize: 10,
    fontWeight: 600,
    color: '#98a2b3',
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
