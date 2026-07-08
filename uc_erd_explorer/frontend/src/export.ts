import { dump as yamlDump } from 'js-yaml'
import { toPng, toSvg } from 'html-to-image'
import { zipSync, strToU8 } from 'fflate'
import { getNodesBounds, getViewportForBounds, type Node } from 'reactflow'
import type { ColumnMeta, GraphEdge, GraphResponse, TableNodeData } from './types'
import { buildDdl } from './erstudio/ddlBuilder'
import { buildMetadataCsv } from './erstudio/metadataCsv'
import { buildUnsupportedTypesDoc } from './erstudio/unsupportedTypesDoc'
import type { Dialect } from './erstudio/typeMapping'

function download(href: string, filename: string) {
  const link = document.createElement('a')
  link.download = filename
  link.href = href
  link.click()
}

export interface ExportScope {
  nodeIds: Set<string>
  edgeIds: Set<string>
}

/**
 * Narrow a graph down to an active click-to-filter selection (a specific table's
 * neighbors/connected component) before handing it to any of the text export formats
 * below -- so "export while a table is selected" produces just that subset, not the
 * whole catalog/schema-scoped graph with the selection ignored. `scope: null` (no
 * active selection) returns `graph` unchanged.
 */
export function scopeGraph(graph: GraphResponse, scope: ExportScope | null): GraphResponse {
  if (!scope) return graph
  // graph.nodes is typed as a union of two array types (TableNodeData[] | SchemaNodeData[])
  // rather than an array of a union -- .filter() can't narrow that back cleanly, but
  // every element still has an `id` regardless of which shape it is.
  const nodes = (graph.nodes as Array<{ id: string }>).filter((n) => scope.nodeIds.has(n.id))
  return {
    ...graph,
    nodes: nodes as GraphResponse['nodes'],
    edges: graph.edges.filter((e) => scope.edgeIds.has(e.id)),
  }
}

/**
 * Export the current canvas -- fit to the bounds of the exported nodes, not the whole
 * graph re-framed. When `scope` is given (an active click-to-filter selection), nodes
 * and edges outside it are excluded from the capture entirely via html-to-image's
 * `filter` option -- not just cropped out of frame, since a dimmed-but-still-rendered
 * node could otherwise fall inside the crop rectangle of a nearby selected node and
 * show up anyway. Uses the documented React Flow + html-to-image pattern: temporarily
 * transform `.react-flow__viewport` to fit the target nodes into a fixed-size image,
 * capture it, then let React Flow's own render take back over on the next frame (no
 * lasting DOM/style mutation).
 */
export async function exportGraphAsImage(
  nodes: Node[],
  format: 'png' | 'svg',
  filenameBase: string,
  scope?: ExportScope | null,
): Promise<void> {
  const viewportEl = document.querySelector('.react-flow__viewport') as HTMLElement | null
  const scopedNodes = scope ? nodes.filter((n) => scope.nodeIds.has(n.id)) : nodes
  if (!viewportEl || scopedNodes.length === 0) return

  const bounds = getNodesBounds(scopedNodes)
  const imageWidth = Math.max(bounds.width + 160, 800)
  const imageHeight = Math.max(bounds.height + 160, 600)
  const { x, y, zoom } = getViewportForBounds(bounds, imageWidth, imageHeight, 0.1, 2, 0.1)

  const options = {
    backgroundColor: '#f6f7f9',
    width: imageWidth,
    height: imageHeight,
    style: {
      width: `${imageWidth}px`,
      height: `${imageHeight}px`,
      transform: `translate(${x}px, ${y}px) scale(${zoom})`,
    },
    filter: scope ? (domNode: HTMLElement) => nodeOrLabelInScope(domNode, scope) : undefined,
  }

  // html-to-image clones React Flow's edges <svg> via a native `svg.cloneNode(true)`
  // (see html-to-image's clone-node.js), which deep-copies every descendant in one shot
  // and never invokes `options.filter` on any of them. Out-of-scope edges are only dimmed
  // via inline opacity, not excluded -- so the `filter` option above can hide out-of-scope
  // *nodes* and edge-label divs (plain HTML, walked node-by-node) but can never exclude an
  // out-of-scope *edge line*, which would otherwise survive in the exported file (faint but
  // present in a PNG, fully present as real path/label data in an SVG). Detaching those
  // edges from the live DOM before capture -- then restoring them straight after -- is the
  // only way to actually remove them rather than just dim them.
  const detached = scope ? detachOutOfScopeEdges(viewportEl, scope) : []
  try {
    const dataUrl = format === 'png' ? await toPng(viewportEl, options) : await toSvg(viewportEl, options)
    download(dataUrl, `${filenameBase}.${format}`)
  } finally {
    for (const { el, parent, nextSibling } of detached) {
      parent.insertBefore(el, nextSibling)
    }
  }
}

function edgeIdOf(el: Element): string {
  return (el.getAttribute('data-testid') ?? '').replace(/^rf__edge-/, '')
}

function detachOutOfScopeEdges(
  viewportEl: HTMLElement,
  scope: ExportScope,
): Array<{ el: Element; parent: globalThis.Node; nextSibling: globalThis.Node | null }> {
  const edges = Array.from(viewportEl.querySelectorAll('.react-flow__edge'))
  const detached: Array<{ el: Element; parent: globalThis.Node; nextSibling: globalThis.Node | null }> = []
  edges.forEach((el, i) => {
    if (scope.edgeIds.has(edgeIdOf(el)) || !el.parentNode) return
    // Anchor on the next edge (in original order) that WON'T be detached, so restoring
    // this element never references a sibling that is itself mid-removal -- if two
    // out-of-scope edges sit next to each other, the naive `el.nextSibling` would point
    // at a node that's no longer attached by the time we try to restore it.
    const nextSibling = edges.slice(i + 1).find((later) => scope.edgeIds.has(edgeIdOf(later))) ?? null
    detached.push({ el, parent: el.parentNode, nextSibling })
    el.remove()
  })
  return detached
}

function nodeOrLabelInScope(domNode: HTMLElement, scope: ExportScope): boolean {
  const classList = domNode.classList
  if (!classList) return true // text nodes etc. -- nothing to filter on, keep
  if (classList.contains('react-flow__node')) {
    const id = domNode.getAttribute('data-id')
    return id ? scope.nodeIds.has(id) : true
  }
  if (classList.contains('erd-edge-label')) {
    // Edge labels render through EdgeLabelRenderer -- a portal into a separate
    // container, not inside the .react-flow__edge element itself -- so they need their
    // own scope check (RelationshipEdge.tsx tags each with data-edge-id) or an
    // out-of-scope edge's label would render on its own, ownerless, even with the edge
    // line itself detached from the DOM above.
    const edgeId = domNode.getAttribute('data-edge-id') ?? ''
    return scope.edgeIds.has(edgeId)
  }
  return true
}

function columnLine(col: ColumnMeta): string {
  const flags = [col.is_pk && 'PK', col.is_fk && 'FK'].filter(Boolean).join(', ')
  const flagPart = flags ? ` _(${flags})_` : ''
  const commentPart = col.comment ? ` -- ${col.comment}` : ''
  const tagsPart = col.tags.length > 0 ? ` \`[${col.tags.map((t) => t.name).join(', ')}]\`` : ''
  return `| \`${col.name}\` | ${col.type} |${flagPart}${tagsPart}${commentPart} |`
}

function edgeLine(e: GraphEdge): string {
  const cols = `${e.fk_columns.join(', ')} → ${e.pk_columns.join(', ')}`
  return e.inferred
    ? `- ⚠️ **${e.source}** → **${e.target}** (\`${cols}\`) -- _inferred, not a declared constraint_`
    : `- **${e.source}** → **${e.target}** (\`${cols}\`)${e.constraint_name ? ` -- \`${e.constraint_name}\`` : ''}`
}

/**
 * A standalone Markdown schema doc for `graph` as given -- callers pass it through
 * scopeGraph() first if a click-to-filter selection should narrow the export. Table
 * data only -- `graph.nodes` must be TableNodeData, not the collapsed SchemaNodeData
 * shape (callers should disable this in schema_summary view).
 */
export function graphToMarkdown(graph: GraphResponse): string {
  const nodes = graph.nodes as TableNodeData[]
  const declaredEdges = graph.edges.filter((e) => !e.inferred)
  const inferredEdges = graph.edges.filter((e) => e.inferred)
  const scopeLabel = graph.unscoped ? 'all catalogs visible to this deployment' : graph.catalogs.join(', ')

  const lines: string[] = []
  lines.push(`# Schema documentation`)
  lines.push('')
  lines.push(`Generated by Catalog ERD Explorer. Scope: ${scopeLabel}.`)
  lines.push('')
  lines.push(`## Tables (${nodes.length})`)
  lines.push('')

  for (const node of nodes) {
    lines.push(`### ${node.catalog}.${node.schema}.${node.table}`)
    lines.push('')
    if (node.comment) lines.push(`> ${node.comment}`)
    if (node.tags.length > 0) {
      lines.push(`Tags: ${node.tags.map((t) => `\`${t.name}${t.value !== 'true' ? `:${t.value}` : ''}\``).join(', ')}`)
    }
    lines.push('')
    lines.push('| Column | Type | Notes |')
    lines.push('|---|---|---|')
    for (const col of node.columns) lines.push(columnLine(col))
    lines.push('')
  }

  lines.push(`## Relationships (${declaredEdges.length})`)
  lines.push('')
  if (declaredEdges.length === 0) {
    lines.push('_No declared foreign keys in this scope._')
  } else {
    for (const e of declaredEdges) lines.push(edgeLine(e))
  }
  lines.push('')

  if (inferredEdges.length > 0) {
    lines.push(`## Inferred (undeclared) relationships (${inferredEdges.length})`)
    lines.push('')
    lines.push('_Heuristic guesses based on column name/type matching -- not real constraints._')
    lines.push('')
    for (const e of inferredEdges) lines.push(edgeLine(e))
    lines.push('')
  }

  return lines.join('\n')
}

export function exportGraphAsMarkdown(graph: GraphResponse, filenameBase: string): void {
  const blob = new Blob([graphToMarkdown(graph)], { type: 'text/markdown' })
  download(URL.createObjectURL(blob), `${filenameBase}.md`)
}

/** The structured data model shared by the JSON and YAML exports -- a machine-readable
 * equivalent of the Markdown doc above (same fields, no prose). */
export interface ExportData {
  scope: string
  tables: Array<{
    catalog: string
    schema: string
    table: string
    comment: string | null
    tags: Array<{ name: string; value: string }>
    columns: Array<{
      name: string
      type: string
      is_primary_key: boolean
      is_foreign_key: boolean
      comment: string | null
      tags: Array<{ name: string; value: string }>
    }>
  }>
  relationships: Array<{
    source: string
    target: string
    fk_columns: string[]
    pk_columns: string[]
    constraint_name: string | null
    inferred: boolean
  }>
}

export function graphToExportData(graph: GraphResponse): ExportData {
  const nodes = graph.nodes as TableNodeData[]
  return {
    scope: graph.unscoped ? 'all catalogs visible to this deployment' : graph.catalogs.join(', '),
    tables: nodes.map((node) => ({
      catalog: node.catalog,
      schema: node.schema,
      table: node.table,
      comment: node.comment,
      tags: node.tags,
      columns: node.columns.map((col) => ({
        name: col.name,
        type: col.type,
        is_primary_key: col.is_pk,
        is_foreign_key: col.is_fk,
        comment: col.comment,
        tags: col.tags,
      })),
    })),
    relationships: graph.edges.map((e) => ({
      source: e.source,
      target: e.target,
      fk_columns: e.fk_columns,
      pk_columns: e.pk_columns,
      constraint_name: e.constraint_name,
      inferred: e.inferred,
    })),
  }
}

export function exportGraphAsJson(graph: GraphResponse, filenameBase: string): void {
  const blob = new Blob([JSON.stringify(graphToExportData(graph), null, 2)], { type: 'application/json' })
  download(URL.createObjectURL(blob), `${filenameBase}.json`)
}

export function exportGraphAsYaml(graph: GraphResponse, filenameBase: string): void {
  const blob = new Blob([yamlDump(graphToExportData(graph))], { type: 'application/yaml' })
  download(URL.createObjectURL(blob), `${filenameBase}.yaml`)
}

/**
 * A .zip with the three files a data modeler needs to reverse-engineer this schema into
 * ER/Studio (or any tool that imports from DDL): physical_model.sql, metadata.csv, and
 * unsupported_types.md. Built entirely client-side from the already-scoped `graph` (the
 * same object the MD/YAML/JSON exports use) -- no separate backend endpoint, so this
 * automatically inherits the exact same catalog/schema AND click-to-filter scoping as
 * every other export, rather than being limited to whatever a server route's query
 * params happen to express.
 */
export function exportGraphAsErStudioZip(graph: GraphResponse, dialect: Dialect, filenameBase: string): void {
  const { sql, unsupported } = buildDdl(graph, dialect)
  const zipped = zipSync({
    'physical_model.sql': strToU8(sql),
    'metadata.csv': strToU8(buildMetadataCsv(graph)),
    'unsupported_types.md': strToU8(buildUnsupportedTypesDoc(unsupported, dialect)),
  })
  const blob = new Blob([zipped as BlobPart], { type: 'application/zip' })
  download(URL.createObjectURL(blob), `${filenameBase}.zip`)
}
