import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { COLUMN_CAP, visibleColumns } from './graphUtils.ts'
import type { ColumnMeta } from './types'

function col(name: string, opts: { pk?: boolean; fk?: boolean } = {}): ColumnMeta {
  return { name, type: 'string', is_pk: !!opts.pk, is_fk: !!opts.fk, comment: null, tags: [] }
}

describe('visibleColumns', () => {
  it('orders PK first, then FK, then the rest (stable within each group)', () => {
    const cols = [col('a'), col('fk1', { fk: true }), col('pk1', { pk: true }), col('b'), col('fk2', { fk: true })]
    const { visible } = visibleColumns(cols, false)
    assert.deepEqual(visible.map((c) => c.name), ['pk1', 'fk1', 'fk2', 'a', 'b'])
  })

  it('does not cap when at or under the cap', () => {
    const cols = Array.from({ length: COLUMN_CAP }, (_, i) => col(`c${i}`))
    const { visible, hidden } = visibleColumns(cols, false)
    assert.equal(visible.length, COLUMN_CAP)
    assert.equal(hidden, 0)
  })

  it('caps to COLUMN_CAP and reports the hidden count', () => {
    const cols = Array.from({ length: COLUMN_CAP + 30 }, (_, i) => col(`c${i}`))
    const { visible, hidden } = visibleColumns(cols, false)
    assert.equal(visible.length, COLUMN_CAP)
    assert.equal(hidden, 30)
  })

  it('keeps PK/FK columns within the cap so edge anchors survive (100-column table)', () => {
    // 3 keys buried among 100 columns -- after ordering they must be in the visible slice.
    const cols = [
      ...Array.from({ length: 50 }, (_, i) => col(`x${i}`)),
      col('id', { pk: true }),
      col('owner_id', { fk: true }),
      col('parent_id', { fk: true }),
      ...Array.from({ length: 47 }, (_, i) => col(`y${i}`)),
    ]
    const { visible, hidden } = visibleColumns(cols, false)
    assert.equal(visible.length, COLUMN_CAP)
    assert.equal(hidden, 100 - COLUMN_CAP)
    const names = visible.map((c) => c.name)
    assert.ok(names.includes('id') && names.includes('owner_id') && names.includes('parent_id'))
    assert.equal(names[0], 'id') // PK first
  })

  it('expanded shows everything with nothing hidden', () => {
    const cols = Array.from({ length: 100 }, (_, i) => col(`c${i}`))
    const { visible, hidden } = visibleColumns(cols, true)
    assert.equal(visible.length, 100)
    assert.equal(hidden, 0)
  })
})
