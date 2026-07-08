import type { Dialect } from './typeMapping'
import type { UnsupportedTypeEntry } from './ddlBuilder'

export function buildUnsupportedTypesDoc(unsupported: UnsupportedTypeEntry[], dialect: Dialect): string {
  const lines: string[] = []
  lines.push('# Unsupported type fallbacks')
  lines.push('')
  lines.push(
    `The following columns have a Unity Catalog type with no direct ${dialect} equivalent ` +
      'and were mapped to a wide text column in physical_model.sql. Review these before ' +
      "import -- ER/Studio will show them as plain text, not their original shape.",
  )
  lines.push('')
  if (unsupported.length === 0) {
    lines.push('_None -- every column type had a direct mapping._')
    return lines.join('\n')
  }
  lines.push('| Table | Column | Original type | Mapped to |')
  lines.push('|---|---|---|---|')
  for (const entry of unsupported) {
    lines.push(`| \`${entry.table}\` | \`${entry.column}\` | \`${entry.originalType}\` | \`${entry.mappedTo}\` |`)
  }
  return lines.join('\n')
}
