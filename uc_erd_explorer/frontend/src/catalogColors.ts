export interface ColorPair {
  bar: string
  soft: string
}

// A palette of visually distinct, brand-adjacent colors assigned to catalogs -- not
// schemas, since a real deployment scopes many schemas under one catalog and the useful
// visual grouping for a multi-catalog customer is "which catalog is this table in,"
// not "which schema." Deliberately more entries than the 2-schema megacorp demo needed,
// since customers with "a variety of catalogs" need this to hold up past just 2-3.
const CATALOG_PALETTE: ColorPair[] = [
  { bar: '#2272b4', soft: '#eaf2fb' }, // blue
  { bar: '#7c3aed', soft: '#f1eafe' }, // purple
  { bar: '#1a7f37', soft: '#e6f4ea' }, // green
  { bar: '#c2410c', soft: '#fdf2e9' }, // orange
  { bar: '#b42318', soft: '#fee4e2' }, // red
  { bar: '#0e7490', soft: '#e0f7fa' }, // teal
  { bar: '#a16207', soft: '#fef9e7' }, // amber
  { bar: '#be185d', soft: '#fce7f3' }, // pink
]

function hashString(s: string): number {
  let hash = 0
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0
  return hash
}

/** Deterministic catalog -> color mapping, stable across renders/reloads for the same
 * catalog name. Hash-based (not index-based) so it doesn't require every node to know
 * the full set of catalogs in the current graph just to pick a color. */
export function catalogColor(catalog: string): ColorPair {
  return CATALOG_PALETTE[hashString(catalog) % CATALOG_PALETTE.length]
}
