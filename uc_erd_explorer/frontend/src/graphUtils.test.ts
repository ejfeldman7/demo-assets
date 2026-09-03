import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { COLUMN_CAP, shortestPath, visibleColumns } from './graphUtils.ts'
import type { ColumnMeta, GraphEdge } from './types'

function col(name: string, opts: { pk?: boolean; fk?: boolean } = {}): ColumnMeta {
  return { name, type: 'string', is_pk: !!opts.pk, is_fk: !!opts.fk, comment: null, tags: [] }
}

function e(id: string, source: string, target: string): GraphEdge {
  return { id, source, target, fk_columns: [], pk_columns: [], constraint_name: null, inferred: false }
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

describe('shortestPath', () => {
  // a -e1- b -e2- c -e3- d ;  b -e4- e (branch) ;  x isolated
  const edges = [e('e1', 'a', 'b'), e('e2', 'b', 'c'), e('e3', 'c', 'd'), e('e4', 'b', 'e')]

  it('finds the fewest-hops path and the edges along it', () => {
    const p = shortestPath('a', 'd', edges)
    assert.deepEqual(p?.nodeIds, ['a', 'b', 'c', 'd'])
    assert.deepEqual(p?.edgeIds, ['e1', 'e2', 'e3'])
  })

  it('works regardless of edge direction (undirected)', () => {
    const p = shortestPath('d', 'a', edges)
    assert.deepEqual(p?.nodeIds, ['d', 'c', 'b', 'a'])
    assert.deepEqual(p?.edgeIds, ['e3', 'e2', 'e1'])
  })

  it('returns the node itself with no edges when source === target', () => {
    assert.deepEqual(shortestPath('a', 'a', edges), { nodeIds: ['a'], edgeIds: [] })
  })

  it('returns null when the tables are in different components', () => {
    assert.equal(shortestPath('a', 'x', edges), null)
  })

  it('takes the shorter of two routes', () => {
    // a-b direct (e1) vs a-...-b longer; add a long way a-f-g-b.
    const withLoop = [...edges, e('f1', 'a', 'f'), e('f2', 'f', 'g'), e('f3', 'g', 'b')]
    assert.deepEqual(shortestPath('a', 'b', withLoop)?.edgeIds, ['e1'])
  })
})
