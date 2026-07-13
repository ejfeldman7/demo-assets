/**
 * Pure Unity Catalog (Spark SQL) -> target-dialect type mapping -- no I/O, no
 * dependency on the graph shape, unit-testable on its own. Used by ddlBuilder.ts to
 * generate a physical_model.sql that a traditional RDBMS-oriented tool like ER/Studio
 * can reverse-engineer, since it has no concept of Delta/Spark's semi-structured types.
 */
export type Dialect = 'sqlserver' | 'oracle'

export interface TypeMappingResult {
  sqlType: string
  unsupported: boolean
  // Only set when unsupported -- the original Unity Catalog type string, for the
  // unsupported_types.md report and an inline DDL comment.
  originalType?: string
}

const DECIMAL_RE = /^DECIMAL\(\s*(\d+)\s*,\s*(\d+)\s*\)$/i
const VARCHAR_RE = /^VARCHAR\(\s*(\d+)\s*\)$/i

function mapSimpleType(upper: string, dialect: Dialect): string | null {
  switch (upper) {
    case 'STRING':
      return dialect === 'sqlserver' ? 'NVARCHAR(MAX)' : 'VARCHAR2(4000)'
    case 'TINYINT':
      return dialect === 'sqlserver' ? 'TINYINT' : 'NUMBER(3)'
    case 'SMALLINT':
      return dialect === 'sqlserver' ? 'SMALLINT' : 'NUMBER(5)'
    case 'INT':
    case 'INTEGER':
      return dialect === 'sqlserver' ? 'INT' : 'NUMBER(10)'
    case 'BIGINT':
      return dialect === 'sqlserver' ? 'BIGINT' : 'NUMBER(19)'
    case 'FLOAT':
    case 'REAL':
      return dialect === 'sqlserver' ? 'REAL' : 'BINARY_FLOAT'
    case 'DOUBLE':
      return dialect === 'sqlserver' ? 'FLOAT' : 'BINARY_DOUBLE'
    case 'BOOLEAN':
      return dialect === 'sqlserver' ? 'BIT' : 'NUMBER(1)'
    case 'DATE':
      return 'DATE'
    case 'TIMESTAMP':
    case 'TIMESTAMP_NTZ':
      return dialect === 'sqlserver' ? 'DATETIME2' : 'TIMESTAMP'
    case 'BINARY':
      return dialect === 'sqlserver' ? 'VARBINARY(MAX)' : 'BLOB'
    default:
      return null
  }
}

/** Maps a single Unity Catalog column type to `dialect`'s closest equivalent.
 * ARRAY/MAP/STRUCT/VARIANT (and anything else unrecognized) have no faithful relational
 * equivalent -- rather than dropping the column or guessing a lossy decomposition, they
 * fall back to a wide text column and are flagged `unsupported: true` so the caller can
 * surface them in unsupported_types.md and an inline DDL comment. */
export function mapColumnType(ucType: string, dialect: Dialect): TypeMappingResult {
  const trimmed = ucType.trim()
  const upper = trimmed.toUpperCase()

  const simple = mapSimpleType(upper, dialect)
  if (simple) return { sqlType: simple, unsupported: false }

  const decimalMatch = upper.match(DECIMAL_RE)
  if (decimalMatch) {
    const [, precision, scale] = decimalMatch
    return { sqlType: `DECIMAL(${precision},${scale})`, unsupported: false }
  }

  // VARCHAR(n) rarely appears directly from Unity Catalog's information_schema (STRING
  // is the norm) but pass it through faithfully if it does.
  const varcharMatch = upper.match(VARCHAR_RE)
  if (varcharMatch) {
    return {
      sqlType: dialect === 'sqlserver' ? `VARCHAR(${varcharMatch[1]})` : `VARCHAR2(${varcharMatch[1]})`,
      unsupported: false,
    }
  }

  const fallback = dialect === 'sqlserver' ? 'NVARCHAR(MAX)' : 'VARCHAR2(4000)'
  return { sqlType: fallback, unsupported: true, originalType: trimmed }
}
