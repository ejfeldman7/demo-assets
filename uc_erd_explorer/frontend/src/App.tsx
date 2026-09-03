import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
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
import { InfoPanel } from './InfoPanel'
import { AdminPanel } from './AdminPanel'
import { ThemeToggle, useResolvedDark } from './ThemeToggle'
import { CatalogSchemaPicker } from './CatalogSchemaPicker'
import { COLUMN_CAP, ROW_HEIGHT, connectedComponent, directNeighbors, nodeSize, shortestPath, visibleColumns } from './graphUtils'
import { layoutGraphElk, type GroupBy, type LayoutDirection } from './elkLayout'
import {
  activeEdgeIds,
  computeEdgeVisual,
  highlightedColumnsByNode,
  type HoveredKey,
} from './edgeDisplay'
import { ErdInteractionContext } from './erdContext'
import { CommandPalette } from './CommandPalette'
import { GroupBoxNode } from './GroupBox'
import type { GroupBox } from './elkLayout'
import type { TableEntry } from './search'
// Only the ExportScope type is imported eagerly (types are erased at build time, so this
// pulls no code). The export implementation -- which drags in html-to-image, js-yaml and
// fflate (~hundreds of KB) -- is dynamically imported inside the export handlers, so it's
// off the initial bundle and only fetched the first time someone actually exports.
import type { ExportScope } from './export'
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

const nodeTypes = { table: TableNode, schema: SchemaNode, groupBox: GroupBoxNode }
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
  // Transient hover state -- what the pointer is currently over. Either a relationship edge
  // or a PK/FK key column reveals that relationship's join-key detail and highlights the
  // two columns it connects. Cleared on mouse-out; never persisted (clicking a table
  // focuses it but does NOT reveal labels -- see displayEdges).
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null)
  const [hoveredKey, setHoveredKey] = useState<HoveredKey | null>(null)
  const [filterMode, setFilterMode] = useState<FilterMode>('neighbors')
  // Join-path tracing: when `tracing` is on, clicking two tables sets the from/to endpoints
  // and the shortest FK path between them is highlighted (rest dimmed).
  const [tracing, setTracing] = useState(false)
  const [traceFrom, setTraceFrom] = useState<string | null>(null)
  const [traceTo, setTraceTo] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  // Heuristic undeclared-relationship edges are always fetched but hidden by default,
  // so first load renders identically to before this feature existed.
  const [showInferred, setShowInferred] = useState(false)
  // "Keys only" collapses each table to just its PK/FK columns -- purely a client-side
  // view filter (the backend always returns every column, flagged is_pk/is_fk), so
  // toggling is instant and never re-queries. A table with no declared PK/FK renders as
  // a header-only card (no columns); that's expected and called out in the sidebar hint.
  const [keysOnly, setKeysOnly] = useState(false)
  const [erStudioDialect, setErStudioDialect] = useState<Dialect>('sqlserver')
  const [paletteOpen, setPaletteOpen] = useState(false)
  // ELK layout flow direction: 'LR' (left-to-right, default -- reads best for these wide
  // column-listing cards) or 'TB' (top-to-bottom).
  const [layoutDir, setLayoutDir] = useState<LayoutDirection>('LR')
  // Tables whose full column list is expanded (past the COLUMN_CAP row cap). Per-table so
  // an analyst can pin a wide fact table open while everything else stays compact.
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set())
  // Grouping dimension: 'none' (flat, the baseline), 'schema' (a box per catalog.schema),
  // or 'catalog' (a box per catalog -- for multi-catalog overviews). ELK compound layout.
  const [groupBy, setGroupBy] = useState<GroupBy>('none')
  const [groupBoxes, setGroupBoxes] = useState<GroupBox[]>([])
  // Collapsed schema group ids ("group:catalog.schema") -- their tables are hidden.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())

  const { fitView, setCenter, getNode } = useReactFlow()
  const laidOutRef = useRef<Node<TableNodeData | SchemaNodeData>[]>([])
  // One-shot flag: skip the next fit-view (set when a baseNodes change came from an
  // expand/collapse push rather than a fresh layout, so toggling doesn't re-zoom).
  const skipFitRef = useRef(false)

  // Load the catalog/schema tree whenever the Prod/Test environment changes, to
  // populate the picker with that environment's actual (possibly _ts-suffixed) catalogs.
  useEffect(() => {
    let cancelled = false
    fetchSchemaTree(env)
      .then((t) => {
        if (cancelled) return
        setTree(t.catalogs)
        setTreeUnscoped(t.unscoped)
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message)
      })
    return () => {
      cancelled = true
    }
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
    setHoveredEdgeId(null)
    setHoveredKey(null)
    // Drop trace endpoints too -- they name tables in the OLD scope; carrying them into a
    // new catalog/schema/env would trace stale ids (misleading "different components", or
    // silently highlighting same-named tables the user never picked here).
    setTraceFrom(null)
    setTraceTo(null)
    // Fresh data scope -> fresh view: clear any schema collapses (they name schemas in the
    // OLD scope, and a schema the user just picked shouldn't load collapsed/empty).
    setCollapsedGroups(new Set())

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
    // Debounce: toggling several catalogs/schemas in the picker changes selectedPairs
    // rapidly, and each change would otherwise fire its own (expensive) /api/graph query.
    // Waiting ~250ms and cancelling any pending timer collapses a burst of toggles into a
    // single request for the final selection. env/pairs are captured together from the
    // same render, so a Prod/Test switch (which also resets pairs) stays consistent.
    const handle = setTimeout(() => {
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
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [selectedPairs, treeUnscoped, env])

  // Colors assigned per the catalogs actually present in the current (already
  // catalog/schema-scoped) graph -- see catalogColors.ts for why this is computed fresh
  // per graph rather than from a fixed name->color lookup.
  const isDark = useResolvedDark()
  const catalogColorMap = useMemo(() => buildCatalogColorMap(graph?.catalogs ?? [], isDark), [graph, isDark])

  // Edges filtered to the current inferred-visibility toggle -- used for both rendering
  // and click-to-filter connectivity, so a hidden inferred edge never silently changes
  // what "connected" means. Default (showInferred=false) matches pre-heuristic behavior
  // exactly, since the backend always returns inferred edges tagged, never omitted.
  const scopedGraphEdges = useMemo(
    () => (graph ? graph.edges.filter((e) => showInferred || !e.inferred) : []),
    [graph, showInferred],
  )

  // The column each visible edge anchors to, per node (fk_columns[0] on the source,
  // pk_columns[0] on the target). Fed to visibleColumns so the cap never slices off an
  // anchor column and dangles its edge -- crucial for inferred edges, whose columns aren't
  // flagged is_fk. Keyed on scopedGraphEdges so it tracks the inferred-visibility toggle.
  const anchorColsByNode = useMemo(() => {
    const m = new Map<string, Set<string>>()
    const add = (id: string, col: string | undefined) => {
      if (!col) return
      let s = m.get(id)
      if (!s) m.set(id, (s = new Set()))
      s.add(col)
    }
    for (const e of scopedGraphEdges) {
      add(e.source, e.fk_columns[0])
      add(e.target, e.pk_columns[0])
    }
    return m
  }, [scopedGraphEdges])

  // The relationship(s) whose detail is revealed right now -- driven purely by transient
  // hover (a hovered edge, or a hovered PK/FK key column), never by selection.
  const activeEdgeSet = useMemo(
    () => activeEdgeIds(hoveredEdgeId, hoveredKey, scopedGraphEdges),
    [hoveredEdgeId, hoveredKey, scopedGraphEdges],
  )
  // Columns to highlight on both endpoints of each active edge (matching-column highlight).
  const highlightColsByNode = useMemo(
    () => highlightedColumnsByNode(activeEdgeSet, scopedGraphEdges),
    [activeEdgeSet, scopedGraphEdges],
  )

  // Stable interaction handlers passed to table nodes via context (see erdContext.ts).
  const interaction = useMemo(
    () => ({
      onKeyEnter: (key: HoveredKey) => setHoveredKey(key),
      onKeyLeave: () => setHoveredKey(null),
      onToggleExpand: (nodeId: string) =>
        // The local-push effect keys off this state change to grow the card in place and
        // nudge only its lane -- no re-layout, no re-fit.
        setExpandedTables((prev) => {
          const next = new Set(prev)
          next.has(nodeId) ? next.delete(nodeId) : next.add(nodeId)
          return next
        }),
      onToggleGroup: (groupId: string) =>
        // Collapsing/expanding a schema re-clusters (via runLayout's collapsedGroups dep).
        setCollapsedGroups((prev) => {
          const next = new Set(prev)
          next.has(groupId) ? next.delete(groupId) : next.add(groupId)
          return next
        }),
    }),
    [],
  )

  // Un-positioned nodes + edges derived from the loaded graph (synchronous). ELK then
  // positions them asynchronously in the effect below -> baseNodes state.
  const { rawNodes, baseEdges } = useMemo(() => {
    if (!graph) {
      return { rawNodes: [] as Node<TableNodeData | SchemaNodeData>[], baseEdges: [] as Edge[] }
    }

    // "Keys only" filters each table's columns to PK/FK before layout, so node heights
    // (and therefore the layout) reflect the collapsed view. Only applies to the detail
    // view -- schema-summary nodes have no columns. Tables with no PK/FK keep an empty
    // columns array and render as header-only cards.
    const keysOnlyActive = keysOnly && graph.view === 'detail'

    // Source columns of currently-VISIBLE inferred edges, keyed by source node id.
    // In keys-only mode these are revealed alongside real PK/FK columns, so a shown
    // dashed inferred edge isn't left pointing at a table with no visible column. This
    // reads from scopedGraphEdges (not graph.edges), which already excludes inferred
    // edges when "Show inferred edges" is off -- so when it's off this map is empty and
    // keys-only stays strictly PK/FK, matching the declared-only behavior.
    const inferredFkColsByNode: Record<string, Set<string>> = {}
    if (keysOnlyActive) {
      for (const e of scopedGraphEdges) {
        if (!e.inferred) continue
        const set = (inferredFkColsByNode[e.source] ??= new Set())
        for (const col of e.fk_columns) set.add(col)
      }
    }

    const rawNodes: Node<TableNodeData | SchemaNodeData>[] = graph.nodes.map((n) => {
      // Schema-summary nodes have no columns -- pass through unchanged.
      if (!('columns' in n)) {
        return { id: n.id, type: 'schema', position: { x: 0, y: 0 }, data: n }
      }
      // Keys-only first (a view filter), then the row cap. baseCols is the full set this
      // card would show uncapped; visibleColumns orders PK->FK->rest and caps it.
      const baseCols = keysOnlyActive
        ? n.columns.filter((c) => c.is_pk || c.is_fk || inferredFkColsByNode[n.id]?.has(c.name))
        : n.columns
      const expanded = expandedTables.has(n.id)
      const { visible, hidden } = visibleColumns(baseCols, expanded, anchorColsByNode.get(n.id))
      return {
        id: n.id,
        type: 'table',
        position: { x: 0, y: 0 },
        data: {
          ...n,
          columns: visible,
          hiddenColumnCount: hidden,
          // Footer (the +N more / show fewer control) shown whenever the uncapped set
          // exceeds the cap -- so an expanded card keeps a "show fewer" control.
          hasColumnFooter: baseCols.length > COLUMN_CAP,
          expanded,
        },
      }
    })

    const edges: Edge[] = scopedGraphEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      // Anchor each edge to the actual related columns' handles (id = column name in
      // TableNode) instead of the card center. First column only for composite keys --
      // enough to point at the right region without a bundle of near-parallel lines.
      // Only in the detail view; schema-summary nodes have no per-column handles, so
      // those edges fall back to the SchemaNode's default centered handle (undefined).
      sourceHandle: graph.view === 'detail' ? e.fk_columns[0] : undefined,
      targetHandle: graph.view === 'detail' ? e.pk_columns[0] : undefined,
      // Structured join columns (not a pre-joined string) so RelationshipEdge can reflow a
      // long/composite mapping onto stacked lines, and draw crow's-foot cardinality (the
      // FK/source end is "many", the PK/target end is "one").
      data: {
        inferred: e.inferred,
        fkCols: e.fk_columns,
        pkCols: e.pk_columns,
      },
      // Custom type, not the built-in 'smoothstep' -- its label renders through
      // EdgeLabelRenderer (a layer above nodes) instead of inline SVG <text> (a layer
      // below nodes), so labels aren't hidden under table cards. See RelationshipEdge.tsx.
      type: 'relationship',
      // Inferred edges render above declared ones so a dashed line that happens to
      // overlap a solid one is never fully hidden underneath it.
      zIndex: e.inferred ? 1 : 0,
      style: {
        stroke: e.inferred ? 'var(--db-red)' : 'var(--text-subtle)',
        strokeWidth: e.inferred ? 2 : 1.5,
        strokeDasharray: e.inferred ? '6 4' : undefined,
      },
    }))

    return { rawNodes, baseEdges: edges }
  }, [graph, scopedGraphEdges, keysOnly, expandedTables, anchorColsByNode])

  // rawNodes/baseEdges reflect the current column-expansion state, so they change on every
  // expand toggle. The ELK layout reads them through refs (not effect deps) so a single
  // table's expand/collapse does NOT trigger a full re-layout -- only structural changes do
  // (see the local-push effect for what expansion does instead).
  const rawNodesRef = useRef(rawNodes)
  rawNodesRef.current = rawNodes
  const baseEdgesRef = useRef(baseEdges)
  baseEdgesRef.current = baseEdges

  // Positioned nodes from ELK's async layout (+ the schema group boxes when grouping is on).
  const [baseNodes, setBaseNodes] = useState<Node<TableNodeData | SchemaNodeData>[]>([])

  // A layout "generation" so a superseded async layout never overwrites a newer one (two
  // effects can now trigger a layout, so a shared counter replaces per-effect cancel flags).
  const layoutGenRef = useRef(0)
  const runLayout = useCallback(
    (fit: boolean) => {
      const nodes = rawNodesRef.current
      if (nodes.length === 0) {
        laidOutRef.current = []
        setBaseNodes([])
        setGroupBoxes([])
        return
      }
      const gen = ++layoutGenRef.current
      // Grouping is only meaningful in the detail view; in schema-summary the nodes ARE
      // schemas (no columns), so grouping them would emit bogus "__ungrouped" boxes.
      const effectiveGroupBy = graph?.view === 'detail' ? groupBy : 'none'
      layoutGraphElk(nodes, baseEdgesRef.current, layoutDir, effectiveGroupBy, collapsedGroups)
        .then((r) => {
          if (gen !== layoutGenRef.current) return
          // Set the skip-fit flag here (not synchronously) so ONLY the layout that actually
          // applies decides whether to fit -- a superseded no-fit layout can't suppress the
          // fit a later structural layout intended.
          skipFitRef.current = !fit
          laidOutRef.current = r.nodes
          setBaseNodes(r.nodes)
          setGroupBoxes(r.groups)
        })
        .catch((e) => {
          if (gen === layoutGenRef.current) setError((e as Error).message)
        })
    },
    [graph, layoutDir, groupBy, collapsedGroups],
  )

  // Structural re-layout: new graph, keys-only, inferred toggle, direction, grouping
  // toggle, or a schema collapse/expand (the last three arrive via runLayout's deps). NOT
  // per-table column expansion.
  useEffect(() => {
    runLayout(true)
  }, [graph, keysOnly, showInferred, runLayout])

  // Expand/collapse a single table. FLAT mode: keep every card in place and apply a local
  // vertical push -- shift only the cards below the toggled one in its lane by the exact
  // height delta (no re-layout, no re-fit, no overlap). GROUPED mode: a height change must
  // also resize the schema box, so re-cluster instead (still no re-fit).
  const prevExpandedRef = useRef(expandedTables)
  useEffect(() => {
    const prev = prevExpandedRef.current
    prevExpandedRef.current = expandedTables
    const added = [...expandedTables].filter((id) => !prev.has(id))
    const removed = [...prev].filter((id) => !expandedTables.has(id))
    // Only a single-table toggle acts; bulk/none changes (e.g. a graph reload) fall through
    // to the fresh layout the structural effect produced.
    if (added.length + removed.length !== 1) return
    if (groupBy !== 'none') {
      runLayout(false) // re-cluster so the schema box grows/shrinks with the table
      return
    }
    const toggledId = added[0] ?? removed[0]
    const expanding = added.length === 1

    setBaseNodes((nodes) => {
      const toggled = nodes.find((n) => n.id === toggledId)
      const raw = rawNodesRef.current.find((n) => n.id === toggledId)
      if (!toggled || !raw || !('columns' in raw.data)) return nodes
      const rawData = raw.data as TableNodeData & { hiddenColumnCount?: number }
      // |full columns - cap| rows appear/disappear. On expand, rawData.columns is the full
      // set (hidden 0); on collapse it's the capped set + hiddenColumnCount.
      const fullCount = rawData.columns.length + (expanding ? 0 : rawData.hiddenColumnCount ?? 0)
      const delta = Math.max(0, fullCount - COLUMN_CAP) * ROW_HEIGHT
      const { width: newW, height: newH } = nodeSize(rawData)
      skipFitRef.current = true
      if (delta === 0) {
        return nodes.map((n) => (n.id === toggledId ? { ...n, data: raw.data, width: newW, height: newH } : n))
      }
      const shift = expanding ? delta : -delta
      const tW = toggled.width ?? 240
      const inLane = (n: Node<TableNodeData | SchemaNodeData>) => {
        if (n.id === toggledId || n.position.y <= toggled.position.y) return false
        if (layoutDir === 'TB') return true // block-shift everything below the toggled card
        const nW = n.width ?? 240 // LR: same column = horizontal overlap with the toggled card
        return n.position.x < toggled.position.x + tW && n.position.x + nW > toggled.position.x
      }
      return nodes.map((n) => {
        if (n.id === toggledId) return { ...n, data: raw.data, width: newW, height: newH }
        return inLane(n) ? { ...n, position: { x: n.position.x, y: n.position.y + shift } } : n
      })
    })
  }, [expandedTables, layoutDir, groupBy, runLayout])

  // Compute the currently-visible set based on selection + mode.
  // The shortest join path between the two chosen endpoints (tracing mode), or null.
  const tracePath = useMemo(
    () => (tracing && traceFrom && traceTo ? shortestPath(traceFrom, traceTo, scopedGraphEdges) : null),
    [tracing, traceFrom, traceTo, scopedGraphEdges],
  )
  const pathEdgeIds = useMemo(() => new Set(tracePath?.edgeIds ?? []), [tracePath])

  const visibleSet = useMemo<Set<string> | null>(() => {
    // Tracing takes over the dim/focus: show only the path's tables.
    if (tracePath) return new Set(tracePath.nodeIds)
    if (!selectedId || !graph) return null
    return filterMode === 'neighbors'
      ? directNeighbors(selectedId, scopedGraphEdges)
      : connectedComponent(selectedId, scopedGraphEdges)
  }, [tracePath, selectedId, filterMode, graph, scopedGraphEdges])

  // Schema group boxes (grouped mode only) rendered FIRST so the opaque table cards paint
  // on top; inert (non-selectable/draggable) background containers, not parents of the
  // tables. Then the tables with dim/selection/highlight/color applied.
  const displayNodes = useMemo<Node[]>(() => {
    const boxes: Node[] = groupBoxes.map((g) => ({
      id: g.id,
      type: 'groupBox',
      position: { x: g.x, y: g.y },
      width: g.width,
      height: g.height,
      style: { width: g.width, height: g.height },
      selectable: false,
      draggable: false,
      zIndex: 0,
      data: {
        id: g.id,
        catalog: g.catalog,
        schema: g.schema,
        count: g.count,
        collapsed: g.collapsed,
        color: lookupCatalogColor(catalogColorMap, g.catalog),
      },
    }))
    const tables = baseNodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        dimmed: visibleSet ? !visibleSet.has(n.id) : false,
        selected: n.id === selectedId,
        color: lookupCatalogColor(catalogColorMap, n.data.catalog),
        highlightedCols: highlightColsByNode.get(n.id),
      },
    }))
    return [...boxes, ...tables]
  }, [baseNodes, groupBoxes, visibleSet, selectedId, catalogColorMap, highlightColsByNode])

  const displayEdges = useMemo<Edge[]>(() => {
    const hasSelection = Boolean(selectedId) || Boolean(tracePath)
    // In grouped mode a collapsed schema's tables aren't rendered (not in baseNodes), so
    // drop any edge that would dangle on a hidden endpoint.
    const visibleNodeIds = new Set(baseNodes.map((n) => n.id))
    return baseEdges
      .filter((e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
      .map((e) => {
      const inferred = Boolean((e.data as { inferred?: boolean } | undefined)?.inferred)
      // Label visibility is HOVER-ONLY (computeEdgeVisual reads activeEdgeSet, not
      // selection): clicking a table focuses it but no longer paints its join-key labels
      // persistently -- the core behavior change from the review.
      const v = computeEdgeVisual({ edge: e, active: activeEdgeSet, visibleSet, hasSelection })
      const onPath = pathEdgeIds.has(e.id)
      // In trace mode ONLY the path edges are prominent -- every other edge is dimmed, even
      // one whose endpoints both happen to be path tables (a chord), so the highlighted
      // path is unambiguous. Otherwise, dim/emphasis follow the click-to-focus selection.
      const dimmed = tracePath ? !onPath : v.dimmed
      return {
        ...e,
        data: { ...(e.data as object), showLabel: v.showLabel },
        style: {
          ...e.style,
          opacity: dimmed ? 0.1 : 1,
          stroke: dimmed ? 'var(--edge-dim)' : onPath ? 'var(--db-blue)' : inferred ? 'var(--db-red)' : 'var(--edge)',
          strokeWidth: onPath ? 2.5 : e.style?.strokeWidth,
        },
        animated: tracePath ? onPath : v.animated,
      }
    })
  }, [baseEdges, baseNodes, visibleSet, selectedId, activeEdgeSet, tracePath, pathEdgeIds])

  const onNodeClick: NodeMouseHandler = useCallback(
    (_, node) => {
      // Group boxes collapse via their own header button; a click anywhere else on the box
      // must be ignored. (They carry no `columns`, so without this they'd fall into the
      // schema-summary branch below and get sent to /api/graph as a bogus pair.)
      if (node.type === 'groupBox') return
      const data = node.data as TableNodeData | SchemaNodeData
      if (isSchemaNodeData(data)) {
        // "Expand" a collapsed schema node by selecting just that schema in the tree
        // picker -- the same pairs-based mechanism the sidebar picker uses, which
        // re-fetches /api/graph scoped to it and always returns full table-level detail.
        setSelectedPairs(new Set([node.id]))
        return
      }
      if (tracing) {
        // First click sets "from"; second sets "to"; a third starts a fresh trace.
        if (!traceFrom || (traceFrom && traceTo)) {
          setTraceFrom(node.id)
          setTraceTo(null)
        } else if (node.id !== traceFrom) {
          setTraceTo(node.id)
        }
        return
      }
      setSelectedId((cur) => (cur === node.id ? null : node.id))
    },
    [tracing, traceFrom, traceTo],
  )

  const reset = useCallback(() => {
    setSelectedId(null)
    setTraceFrom(null)
    setTraceTo(null)
    setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 0)
  }, [fitView])

  const toggleTracing = useCallback(() => {
    setTraceFrom(null)
    setTraceTo(null)
    if (!tracing) setSelectedId(null) // entering trace mode: drop any click-to-focus selection
    setTracing((on) => !on)
  }, [tracing])

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
    async (format: 'png' | 'svg') => {
      if (!canExport) return
      const m = await import('./export')
      m.exportGraphAsImage(displayNodes, format, 'erd-export', exportScope)
    },
    [canExport, displayNodes, exportScope],
  )

  const handleExportDocs = useCallback(
    async (format: 'md' | 'json' | 'yaml') => {
      if (!canExport || !graph) return
      const m = await import('./export')
      const scoped = m.scopeGraph(graph, exportScope)
      if (format === 'md') m.exportGraphAsMarkdown(scoped, 'erd-schema-docs')
      else if (format === 'json') m.exportGraphAsJson(scoped, 'erd-schema-docs')
      else m.exportGraphAsYaml(scoped, 'erd-schema-docs')
    },
    [canExport, graph, exportScope],
  )

  const handleExportErStudio = useCallback(async () => {
    if (!canExport || !graph) return
    const m = await import('./export')
    const scoped = m.scopeGraph(graph, exportScope)
    m.exportGraphAsErStudioZip(scoped, erStudioDialect, 'erd-erstudio-export')
  }, [canExport, graph, exportScope, erStudioDialect])

  // Fit view when a fresh layout lands -- but NOT when the re-layout was an expand/collapse
  // (skipFitRef), so toggling a table's columns doesn't re-zoom the whole canvas.
  useEffect(() => {
    if (baseNodes.length === 0) return
    if (skipFitRef.current) {
      skipFitRef.current = false
      return
    }
    setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 50)
  }, [baseNodes, fitView])

  const runSearch = useCallback(() => {
    const q = search.trim().toLowerCase()
    if (!q) return
    // Search the currently-VISIBLE table nodes (baseNodes) -- always up to date, and it
    // excludes tables inside a collapsed group, which have no node to pan to anyway.
    const nodes = baseNodes.filter(
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
        if (!tracing) setSelectedId(match.id)
      }
    }
  }, [search, getNode, setCenter, tracing, baseNodes])

  // Table list for the Cmd+K quick-find palette -- the currently-VISIBLE table nodes, so a
  // pick always has a node to pan to (tables in a collapsed group are excluded, matching
  // what's on the canvas; schema-summary nodes have no columns and are skipped).
  const tableEntries = useMemo<TableEntry[]>(() => {
    return baseNodes
      .filter((n): n is Node<TableNodeData> => !isSchemaNodeData(n.data))
      .map((n) => ({ id: n.id, catalog: n.data.catalog, schema: n.data.schema, table: n.data.table }))
  }, [baseNodes])

  // Pan/zoom to a table and focus it -- shared by the palette and the sidebar search.
  const jumpToNode = useCallback(
    (id: string) => {
      const node = getNode(id)
      if (node) {
        setCenter(node.position.x + 120, node.position.y + 80, { zoom: 1.1, duration: 500 })
        // In trace mode we only pan to the table (so the user can click it as an endpoint);
        // applying click-to-focus here would fight the trace dimming.
        if (!tracing) setSelectedId(id)
      }
    },
    [getNode, setCenter, tracing],
  )

  // Cmd/Ctrl+K opens the quick-find palette (Lineage-Explorer-style navigation for large
  // graphs). Registered globally so it works regardless of focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const selectedTable = selectedId
    ? selectedId.split('.').slice(1).join('.')
    : null

  const inferredCount = graph ? graph.edges.filter((e) => e.inferred).length : 0
  const declaredCount = graph ? graph.edges.filter((e) => !e.inferred).length : 0

  // How many tables have no declared PK/FK -- these render as header-only (no columns)
  // in "keys only" mode, so we surface the count in the hint to explain the empty cards.
  const keylessTableCount = useMemo(() => {
    if (!graph || graph.view !== 'detail') return 0
    return graph.nodes.filter(
      (n) => 'columns' in n && !n.columns.some((c) => c.is_pk || c.is_fk),
    ).length
  }, [graph])

  return (
    <ErdInteractionContext.Provider value={interaction}>
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
        <ThemeToggle />
        <AdminPanel />
        <InfoPanel />
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
            <div style={styles.hint}>
              {tree.length} catalog{tree.length === 1 ? '' : 's'} visible. Only catalogs
              this app has permission to read are listed — if one is missing, its access
              needs to be granted.
            </div>
          </div>

          <SectionLabel>Search</SectionLabel>
          <div style={styles.card}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runSearch()}
                placeholder="Find a table…"
                aria-label="Find a table by name"
                style={styles.searchInput}
              />
              <button onClick={runSearch} style={styles.searchBtn}>
                Go
              </button>
            </div>
            <div style={styles.hint}>
              Press <kbd style={styles.kbdHint}>⌘K</kbd> / <kbd style={styles.kbdHint}>Ctrl K</kbd> for quick find.
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

          <SectionLabel>Join path</SectionLabel>
          <div style={styles.card}>
            <Switch label="Trace join path" checked={tracing} onChange={toggleTracing} />
            <div style={styles.hint}>
              {!tracing
                ? 'Highlight the shortest FK path between two tables.'
                : !traceFrom
                  ? 'Click the first table…'
                  : !traceTo
                    ? `From ${traceFrom.split('.').pop()} — now click the target table.`
                    : tracePath
                      ? tracePath.nodeIds.map((n) => n.split('.').pop()).join(' → ')
                      : `No path between ${traceFrom.split('.').pop()} and ${traceTo.split('.').pop()} — they're in different components.`}
            </div>
            {tracing && (traceFrom || traceTo) && (
              <button
                onClick={() => {
                  setTraceFrom(null)
                  setTraceTo(null)
                }}
                style={styles.resetBtn}
              >
                Clear path
              </button>
            )}
          </div>

          <SectionLabel>Layout</SectionLabel>
          <div style={styles.card}>
            {(['LR', 'TB'] as LayoutDirection[]).map((d) => (
              <button key={d} onClick={() => setLayoutDir(d)} style={sidebarRow(layoutDir === d)}>
                <span style={{ flex: 1 }}>{d === 'LR' ? 'Left → right' : 'Top → bottom'}</span>
                {layoutDir === d && <span style={styles.check}>✓</span>}
              </button>
            ))}
            <div style={styles.hint}>
              Auto-arranged with ELK. Left → right suits these wide table cards; top → bottom
              stacks them vertically.
            </div>
          </div>

          <SectionLabel>Group by</SectionLabel>
          <div style={styles.card}>
            <div style={{ display: 'flex', gap: 4 }}>
              {(
                [
                  ['none', 'None'],
                  ['schema', 'Schema'],
                  ['catalog', 'Catalog'],
                ] as [GroupBy, string][]
              ).map(([g, label]) => (
                <button
                  key={g}
                  onClick={() => setGroupBy(g)}
                  style={{ ...sidebarRow(groupBy === g), flex: 1, justifyContent: 'center' }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div style={styles.hint}>
              {groupBy === 'none'
                ? 'Cluster tables into a labeled box per schema, or per catalog for a multi-catalog overview.'
                : groupBy === 'schema'
                  ? 'A box per catalog.schema. Click a box header to collapse that schema.'
                  : 'A box per catalog (all its schemas together). Click a box header to collapse it.'}
            </div>
          </div>

          <SectionLabel>Columns</SectionLabel>
          <div style={styles.card}>
            <Switch
              label="Keys only (PK / FK)"
              checked={keysOnly}
              onChange={() => setKeysOnly((v) => !v)}
            />
            <div style={styles.hint}>
              {keysOnly
                ? `Showing only primary- and foreign-key columns to keep wide tables readable.${
                    keylessTableCount > 0
                      ? ` ${keylessTableCount} table${keylessTableCount === 1 ? '' : 's'} have no declared PK/FK, so ${keylessTableCount === 1 ? 'it appears' : 'they appear'} with no columns — that's expected.`
                      : ''
                  }`
                : 'Collapse wide tables to just their key columns. Tables without a declared PK/FK will appear with no columns.'}
            </div>
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
            onPaneClick={() => {
              // Clicking empty canvas clears focus AND any traced path (in trace mode this
              // resets the endpoints so the next node click starts a fresh trace).
              setSelectedId(null)
              setTraceFrom(null)
              setTraceTo(null)
            }}
            onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(edge.id)}
            onEdgeMouseLeave={() => setHoveredEdgeId(null)}
            fitView
            minZoom={0.1}
            proOptions={{ hideAttribution: true }}
            style={{ background: 'var(--bg)' }}
          >
            <Background color="var(--canvas-dot)" gap={22} />
            <Controls />
            <MiniMap
              nodeColor={(n) =>
                lookupCatalogColor(catalogColorMap, (n.data as TableNodeData | SchemaNodeData)?.catalog ?? '').bar
              }
              maskColor={isDark ? 'rgba(15,20,24,0.7)' : 'rgba(246,247,249,0.7)'}
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
                    aria-label="ER/Studio SQL dialect"
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
      <CommandPalette
        open={paletteOpen}
        tables={tableEntries}
        onSelect={jumpToNode}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
    </ErdInteractionContext.Provider>
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
          background: checked ? 'var(--db-red)' : 'var(--border-strong)',
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
            background: 'var(--on-accent)',
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
    border: '1px solid var(--border)',
    borderRadius: 6,
    background: 'var(--surface)',
    color: enabled ? 'var(--text)' : 'var(--text-subtle)',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 8px',
    cursor: enabled ? 'pointer' : 'default',
    whiteSpace: 'nowrap',
  }
}

function exportDialectSelect(enabled: boolean): CSSProperties {
  return {
    border: '1px solid var(--border)',
    borderRadius: 6,
    background: 'var(--surface)',
    color: enabled ? 'var(--text)' : 'var(--text-subtle)',
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
    background: 'var(--surface)',
    border: '1px solid var(--border)',
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
    color: 'var(--text-subtle)',
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
    color: 'var(--text-subtle)',
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
    color: 'var(--on-accent)',
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
    color: 'var(--on-accent)',
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
    background: 'linear-gradient(135deg,var(--surface),var(--surface-subtle))',
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
    color: 'var(--on-accent)',
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
  kbdHint: {
    display: 'inline-block',
    padding: '0 4px',
    border: '1px solid var(--border-strong)',
    borderRadius: 4,
    background: 'var(--surface)',
    fontSize: 10,
    fontFamily: 'inherit',
    color: 'var(--text-muted)',
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
