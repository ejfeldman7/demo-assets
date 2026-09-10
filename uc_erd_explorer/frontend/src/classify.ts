// Client-side, deterministic classification of each table as a fact / dimension /
// junction / unknown -- the input to the Star layout's "which table is the center?"
// suggestion, and to the sidebar's fact/dim/junction counts. No backend, no LLM: it reads
// only what's already in the GraphResponse payload (names, comments, keys, FK edges).
//
// Design intent (see the ERD README / Star layout): there are TWO confidence tiers.
//  1. EXPLICIT schemas (e.g. Ball's certified gold layer, dim_/fact_ naming + declared,
//     NOT-ENFORCED FKs) resolve on NAMING first -- high confidence, exact.
//  2. LESS-EXPLICIT schemas (normalized OLTP, no dim_/fact_ names) fall back to a
//     STRUCTURAL heuristic over FK degree + measure-column ratio -- best-effort, fuzzy.
// The structural tier is deliberately best-effort: a schema that isn't dimensionally
// modeled will produce approximate labels, and that's fine -- the Star view degrades to a
// "focus on one table + its neighbors" ego view rather than a true star, and never breaks.
import type { GraphEdge, TableNodeData } from './types'

export type TableRole = 'fact' | 'dimension' | 'junction' | 'unknown'

export interface ClassifiedTable {
  /** node id: "catalog.schema.table" */
  id: string
  role: TableRole
  /** 0-1 confidence, used to rank fact candidates for the star-center suggestion. */
  score: number
  /** human-readable reasons -- surfaced for debugging and (optionally) a tooltip. */
  signals: string[]
}

// Whole-segment naming matches (anchored on "_" or string ends) so "dim_plant",
// "fact_moq_exception", "sales_fct", "customer_dimension" match, but a word that merely
// contains the substring (e.g. "dimensional_model") does not.
const FACT_NAME = /(^|_)(fact|fct)(_|$)/
const DIM_NAME = /(^|_)(dim|dimension|lookup)(_|$)/
const JUNCTION_NAME = /(^|_)(bridge|junction|xref)(_|$)/

// Column data types that read as measures/amounts (a fact-table signal). Compared against
// the lowercased type prefix so "decimal(18,3)" and "bigint" both match.
const NUMERIC_TYPE_PREFIXES = ['bigint', 'int', 'smallint', 'tinyint', 'decimal', 'double', 'float', 'numeric']

function isNumericType(fullType: string): boolean {
  const t = fullType.trim().toLowerCase()
  return NUMERIC_TYPE_PREFIXES.some((p) => t.startsWith(p))
}

interface Degree {
  outFk: number // count of DISTINCT tables this one references (fact-ish -> points at dims)
  inFk: number // count of DISTINCT tables that reference this one (dimension-ish)
}

// Degree counts DISTINCT related TABLES, not raw edges: a table with two FK columns both
// pointing at the same table (e.g. a bill-of-materials with parent+component -> materials)
// references ONE table, not two, so it isn't mistaken for a multi-dimension fact.
function degrees(nodes: TableNodeData[], edges: GraphEdge[]): Map<string, Degree> {
  const outTargets = new Map<string, Set<string>>()
  const inSources = new Map<string, Set<string>>()
  for (const n of nodes) {
    outTargets.set(n.id, new Set())
    inSources.set(n.id, new Set())
  }
  for (const e of edges) {
    outTargets.get(e.source)?.add(e.target)
    inSources.get(e.target)?.add(e.source)
  }
  const d = new Map<string, Degree>()
  for (const n of nodes) d.set(n.id, { outFk: outTargets.get(n.id)!.size, inFk: inSources.get(n.id)!.size })
  return d
}

/**
 * Classify every table in a detail-view graph. Evaluation order (first match wins, so the
 * explicit signals short-circuit the fuzzy structural one):
 *   1. naming convention  -> fact / dimension / junction (high confidence)
 *   2. UC comment keywords -> fact / dimension / junction
 *   3. composite PK (>=3 cols) -> junction
 *   4. structural scoring  -> fact / dimension / unknown (best-effort fallback)
 * `edges` should be whatever edges are currently in scope (declared, plus inferred when the
 * user has that toggle on) -- classification then matches what's actually on the diagram.
 */
export function classifyTables(nodes: TableNodeData[], edges: GraphEdge[]): Map<string, ClassifiedTable> {
  const deg = degrees(nodes, edges)
  const result = new Map<string, ClassifiedTable>()

  for (const n of nodes) {
    result.set(n.id, classifyOne(n, deg.get(n.id) ?? { outFk: 0, inFk: 0 }))
  }
  return result
}

function classifyOne(n: TableNodeData, deg: Degree): ClassifiedTable {
  const id = n.id
  const name = n.table.toLowerCase()

  // 1. Naming convention -- the primary, high-confidence path for dimensionally-modeled
  //    schemas (dim_/fact_/bridge_). Whole-segment match, so no substring false positives.
  if (FACT_NAME.test(name)) return { id, role: 'fact', score: 0.95, signals: ['naming: fact prefix/infix'] }
  if (DIM_NAME.test(name)) return { id, role: 'dimension', score: 0.95, signals: ['naming: dimension prefix/infix'] }
  if (JUNCTION_NAME.test(name)) return { id, role: 'junction', score: 0.9, signals: ['naming: junction/bridge prefix/infix'] }

  // 2. UC comment keywords -- next best when a table is documented but not conventionally
  //    named. Lowercased substring match on the table's COMMENT.
  const comment = (n.comment ?? '').toLowerCase()
  if (comment) {
    if (comment.includes('fact table') || comment.startsWith('fact '))
      return { id, role: 'fact', score: 0.85, signals: ['comment: mentions fact table'] }
    if (comment.includes('dimension') || comment.includes('lookup table') || comment.includes('reference table'))
      return { id, role: 'dimension', score: 0.85, signals: ['comment: mentions dimension/lookup'] }
    if (comment.includes('junction') || comment.includes('bridge') || comment.includes('many-to-many'))
      return { id, role: 'junction', score: 0.85, signals: ['comment: mentions junction/bridge'] }
  }

  // 3. Composite PK (>=3 columns) -- the classic associative/junction-table signature.
  //    Runs before structural scoring so a bridge isn't scored as a weak fact.
  const pkCount = n.columns.filter((c) => c.is_pk).length
  if (pkCount >= 3) return { id, role: 'junction', score: 0.8, signals: [`composite PK (${pkCount} columns)`] }

  // 4. Structural fallback (best-effort, fuzzy). Leans on OUTGOING FK count -- a fact table
  //    references many dimensions -- with measure-column ratio and out/in balance as
  //    secondary signals. Weights favor outFk so a fact is caught even on data that isn't
  //    heavily numeric; a schema that isn't a star produces "unknown" rather than a wrong
  //    strong label.
  const { outFk, inFk } = deg
  const totalDegree = outFk + inFk

  // Measure columns: numeric-typed and NOT structural (PK/FK) -- those are keys, not measures.
  const measureCols = n.columns.filter((c) => !c.is_pk && !c.is_fk && isNumericType(c.type)).length
  const nonKeyCols = n.columns.filter((c) => !c.is_pk && !c.is_fk).length
  const numericRatio = nonKeyCols > 0 ? measureCols / nonKeyCols : 0
  const outRatio = totalDegree > 0 ? outFk / totalDegree : 0

  const factScore = Math.min(outFk / 3, 1) * 0.6 + numericRatio * 0.2 + outRatio * 0.2

  if (factScore >= 0.5 && outFk >= 2) {
    return {
      id,
      role: 'fact',
      score: factScore,
      signals: [`structural: references ${outFk} tables, ${(numericRatio * 100).toFixed(0)}% measure columns`],
    }
  }
  if (inFk > 0 && outFk === 0) {
    return { id, role: 'dimension', score: 0.6, signals: [`structural: referenced by ${inFk}, references none`] }
  }
  if (inFk > outFk * 2) {
    return { id, role: 'dimension', score: 0.55, signals: [`structural: referenced (${inFk}) far more than referencing (${outFk})`] }
  }
  return { id, role: 'unknown', score: 0, signals: ['structural: no clear fact/dimension signal'] }
}

/** Roll up role counts for the sidebar's "Facts / Dimensions / Junctions" line. */
export function roleCounts(classified: Map<string, ClassifiedTable>): Record<TableRole, number> {
  const counts: Record<TableRole, number> = { fact: 0, dimension: 0, junction: 0, unknown: 0 }
  for (const c of classified.values()) counts[c.role] += 1
  return counts
}

/**
 * Suggest the best table to center a star on: the highest-scoring FACT, breaking ties by
 * outgoing-FK count (the "widest" fact). If nothing classifies as a fact (a non-star
 * schema), fall back to the highest-degree hub so the star still centers something useful,
 * and finally to the first node -- so the Star view always has a valid center and never
 * breaks, even on data that isn't dimensionally modeled. Returns null only when there are
 * no tables at all.
 */
export function suggestStarCenter(
  nodes: TableNodeData[],
  edges: GraphEdge[],
  classified?: Map<string, ClassifiedTable>,
): string | null {
  if (nodes.length === 0) return null
  const cls = classified ?? classifyTables(nodes, edges)
  const deg = degrees(nodes, edges)

  const facts = nodes
    .map((n) => ({ id: n.id, c: cls.get(n.id) }))
    .filter((x): x is { id: string; c: ClassifiedTable } => x.c?.role === 'fact')
  if (facts.length > 0) {
    facts.sort((a, b) => b.c.score - a.c.score || (deg.get(b.id)?.outFk ?? 0) - (deg.get(a.id)?.outFk ?? 0))
    return facts[0].id
  }

  // No facts -> pick the most-connected table (a hub), so the star is still informative.
  let best = nodes[0].id
  let bestDegree = -1
  for (const n of nodes) {
    const d = deg.get(n.id)
    const total = (d?.outFk ?? 0) + (d?.inFk ?? 0)
    if (total > bestDegree) {
      bestDegree = total
      best = n.id
    }
  }
  return best
}
