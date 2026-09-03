import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { isSubsequence, rankTables, type TableEntry } from './search.ts'

function t(catalog: string, schema: string, table: string): TableEntry {
  return { id: `${catalog}.${schema}.${table}`, catalog, schema, table }
}

const tables = [
  t('mfg', 'sales', 'orders'),
  t('mfg', 'sales', 'order_items'),
  t('mfg', 'orders_archive', 'shipments'),
  t('mfg', 'factory', 'bill_of_materials'),
  t('mfg', 'factory', 'materials'),
]

describe('isSubsequence', () => {
  it('matches in-order non-contiguous chars', () => {
    assert.equal(isSubsequence('ordr', 'orders'), true)
    assert.equal(isSubsequence('oi', 'order_items'), true)
  })
  it('rejects out-of-order or missing chars', () => {
    assert.equal(isSubsequence('rdo', 'orders'), false)
    assert.equal(isSubsequence('xyz', 'orders'), false)
  })
  it('empty needle always matches', () => {
    assert.equal(isSubsequence('', 'anything'), true)
  })
})

describe('rankTables', () => {
  it('empty query returns all tables in input order', () => {
    assert.deepEqual(rankTables('', tables).map((x) => x.table), tables.map((x) => x.table))
  })

  it('ranks an exact table-name match first (over a schema that contains the word)', () => {
    // "orders" is an exact table (mfg.sales.orders) AND appears in schema orders_archive.
    assert.equal(rankTables('orders', tables)[0].id, 'mfg.sales.orders')
  })

  it('prefers a shorter table name on a prefix tie', () => {
    // "material" prefixes both "materials" and (word-boundary) "bill_of_materials".
    const r = rankTables('material', tables)
    assert.equal(r[0].table, 'materials')
  })

  it('matches on a word boundary inside a snake_case name', () => {
    // "items" is a word-boundary segment of order_items.
    const r = rankTables('items', tables)
    assert.equal(r[0].table, 'order_items')
  })

  it('falls back to subsequence when nothing better matches', () => {
    // "oi" is a subsequence of order_items but not a substring/prefix.
    const r = rankTables('oi', tables)
    assert.ok(r.some((x) => x.table === 'order_items'))
  })

  it('excludes non-matches entirely', () => {
    assert.equal(rankTables('zzzzz', tables).length, 0)
  })

  it('respects the limit', () => {
    assert.equal(rankTables('', tables, 2).length, 2)
  })

  it('matches against the qualified schema.table when the table name misses', () => {
    // "archive" only appears in the schema, not the table name (shipments).
    const r = rankTables('archive', tables)
    assert.ok(r.some((x) => x.id === 'mfg.orders_archive.shipments'))
  })
})
