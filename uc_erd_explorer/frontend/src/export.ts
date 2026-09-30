import { dump as yamlDump } from 'js-yaml'
import { toPng, toSvg } from 'html-to-image'
import { zipSync, strToU8 } from 'fflate'
import { getNodesBounds, getViewportForBounds, type Node } from 'reactflow'
import type { GraphResponse } from './types'
import { buildDdl } from './erstudio/ddlBuilder.ts'
import { buildMetadataCsv } from './erstudio/metadataCsv.ts'
import { buildUnsupportedTypesDoc } from './erstudio/unsupportedTypesDoc.ts'
import type { Dialect } from './erstudio/typeMapping.ts'
import type { ExportScope } from './graphScope.ts'
export { scopeGraph } from './graphScope.ts'
export type { ExportScope }
export { graphToMarkdown, graphToExportData } from './exportDocs.ts'
export type { ExportData } from './exportDocs.ts'

function download(href: string, filename: string) {
  const link = document.createElement('a')
  link.download = filename
  link.href = href
  link.click()
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

import { graphToMarkdown, graphToExportData } from './exportDocs.ts'

export function exportGraphAsMarkdown(graph: GraphResponse, filenameBase: string): void {
  const blob = new Blob([graphToMarkdown(graph)], { type: 'text/markdown' })
  download(URL.createObjectURL(blob), `${filenameBase}.md`)
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
