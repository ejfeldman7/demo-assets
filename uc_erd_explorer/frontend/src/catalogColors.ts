export interface ColorPair {
  bar: string
  soft: string
}

const FALLBACK: ColorPair = { bar: '#475467', soft: '#f2f4f7' }

function hslToHex(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => {
    const k = (n + h / 30) % 12
    const color = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
    return Math.round(255 * color)
      .toString(16)
      .padStart(2, '0')
  }
  return `#${f(0)}${f(8)}${f(4)}`
}

/**
 * Assign each catalog currently in view a color, spacing hues evenly around the wheel
 * based on how many distinct catalogs are actually present -- not a fixed lookup table
 * keyed by catalog name. A static hash-to-palette mapping (the previous approach) is
 * only ever *coincidentally* well-separated for whichever specific catalogs a customer
 * happens to have: two arbitrary catalog names can always hash into adjacent palette
 * slots and render in near-identical colors. Computing hues on the fly for exactly the
 * N catalogs on screen guarantees every pair is separated by 360/N degrees -- the most
 * distinguishable arrangement possible for that view. The tradeoff: a catalog's color
 * can shift if the set of catalogs in view changes (e.g. narrowing the picker selection
 * down to one catalog) -- deliberate, since in-view distinguishability matters more here
 * than a catalog keeping one fixed color across every possible view.
 */
export function buildCatalogColorMap(catalogs: string[], dark = false): Map<string, ColorPair> {
  const sorted = Array.from(new Set(catalogs)).sort()
  const map = new Map<string, ColorPair>()
  const n = sorted.length
  if (n === 0) return map
  sorted.forEach((catalog, i) => {
    const hue = (360 / n) * i
    map.set(catalog, {
      // Header bar stays dark enough that the white (--on-accent) label reads in both
      // themes; the soft tint (group-box fill, selected ring, highlighted row) flips from
      // a near-white wash in light to a dark, low-lightness tint in dark so it doesn't
      // become a glowing patch.
      bar: dark ? hslToHex(hue, 0.5, 0.46) : hslToHex(hue, 0.62, 0.38),
      soft: dark ? hslToHex(hue, 0.45, 0.2) : hslToHex(hue, 0.62, 0.95),
    })
  })
  return map
}

export function lookupCatalogColor(map: Map<string, ColorPair>, catalog: string): ColorPair {
  return map.get(catalog) ?? FALLBACK
}
