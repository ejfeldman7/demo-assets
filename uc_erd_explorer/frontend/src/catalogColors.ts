export interface ColorPair {
  bar: string
  soft: string
}

// A palette of visually distinct, brand-adjacent colors assigned to catalogs -- not
// schemas, since a real deployment scopes many schemas under one catalog and the useful
// visual grouping for a multi-catalog customer is "which catalog is this table in,"
// not "which schema." Deliberately more entries than the 2-schema megacorp demo needed,
// since customers with "a variety of catalogs" need this to hold up past just 2-3.
//
// Hues are spaced exactly 45° apart around the color wheel (0/45/90/.../315) rather than
// picked ad hoc -- an earlier version had two hues only 13° apart (a muted red and a
// muted orange), which any two catalogs whose names happened to hash into those two
// buckets would render in near-identical colors, defeating the entire point of
// color-by-catalog. A fixed 45° minimum separation guarantees every pair in the palette
// stays visually distinguishable no matter which two a given set of catalog names hash to.
const CATALOG_PALETTE: ColorPair[] = [
  { bar: '#b81414', soft: '#fbe9e9' }, // red (0°)
  { bar: '#b88f14', soft: '#fbf7e9' }, // gold (45°)
  { bar: '#66b814', soft: '#f2fbe9' }, // lime (90°)
  { bar: '#14b83d', soft: '#e9fbee' }, // green (135°)
  { bar: '#14b8b8', soft: '#e9fbfb' }, // teal (180°)
  { bar: '#143db8', soft: '#e9eefb' }, // blue (225°)
  { bar: '#6614b8', soft: '#f2e9fb' }, // purple (270°)
  { bar: '#b8148f', soft: '#fbe9f7' }, // pink (315°)
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
