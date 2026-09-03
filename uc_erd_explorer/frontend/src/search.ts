// Pure table-search ranking for the Cmd+K quick-find palette. Kept framework-free so the
// match/rank rules are unit-testable (node --test). The palette itself (CommandPalette.tsx)
// only renders the ranked result and handles keyboard/pointer selection.

export interface TableEntry {
  id: string // catalog.schema.table (node id)
  catalog: string
  schema: string
  table: string
}

// Match tiers, best first. A table's score is the best tier any of its facets earns; the
// table name is weighted above schema/catalog so "orders" ranks factory.orders over a
// table merely living in an "orders_*" schema. A plain const object (not a TS `enum`) so
// it survives `erasableSyntaxOnly` and Node's type-stripping test runner.
const Tier = {
  ExactTable: 0,
  PrefixTable: 1,
  WordBoundaryTable: 2,
  SubstringTable: 3,
  SubstringQualified: 4, // matches in "schema.table" or "catalog.schema.table"
  Subsequence: 5,
  None: 6,
} as const
type Tier = (typeof Tier)[keyof typeof Tier]

/** Does `needle`'s chars appear in order (not necessarily contiguous) within `hay`? */
export function isSubsequence(needle: string, hay: string): boolean {
  if (!needle) return true
  let i = 0
  for (let j = 0; j < hay.length && i < needle.length; j++) {
    if (hay[j] === needle[i]) i++
  }
  return i === needle.length
}

function tableTier(q: string, table: string): Tier {
  if (table === q) return Tier.ExactTable
  if (table.startsWith(q)) return Tier.PrefixTable
  // Word boundary: start of a `_`- or `.`-delimited segment.
  if (table.split(/[._]/).some((seg) => seg.startsWith(q))) return Tier.WordBoundaryTable
  if (table.includes(q)) return Tier.SubstringTable
  return Tier.None
}

function entryTier(q: string, e: TableEntry): Tier {
  const t = tableTier(q, e.table.toLowerCase())
  if (t !== Tier.None) return t
  const qualified = `${e.schema}.${e.table}`.toLowerCase()
  const full = e.id.toLowerCase()
  if (qualified.includes(q) || full.includes(q)) return Tier.SubstringQualified
  if (isSubsequence(q, e.table.toLowerCase()) || isSubsequence(q, qualified)) return Tier.Subsequence
  return Tier.None
}

/**
 * Rank tables for a query. Empty query returns everything in natural (given) order so the
 * palette shows the full list to browse. Ties within a tier keep input order (stable), and
 * as a secondary key shorter table names win (a closer match to what was typed).
 */
export function rankTables(query: string, tables: TableEntry[], limit = 50): TableEntry[] {
  const q = query.trim().toLowerCase()
  if (!q) return tables.slice(0, limit)
  const scored = tables
    .map((e, i) => ({ e, i, tier: entryTier(q, e) }))
    .filter((x) => x.tier !== Tier.None)
    .sort((a, b) => a.tier - b.tier || a.e.table.length - b.e.table.length || a.i - b.i)
  return scored.slice(0, limit).map((x) => x.e)
}
