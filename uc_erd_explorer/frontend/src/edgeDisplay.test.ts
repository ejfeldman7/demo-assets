import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  activeEdgeIds,
  computeEdgeVisual,
  edgeInSelection,
  edgesForKey,
  formatJoinLabel,
  highlightedColumnsByNode,
  LABEL_STACK_THRESHOLD,
  shouldShowLabel,
} from './edgeDisplay.ts'
import type { GraphEdge } from './types'

// Runs on Node's built-in test runner (`node --test`, type-stripped) -- deliberately NO
// test-runner dependency, so the CI dist-check's `npm ci` install tree stays lean.

// Minimal edge factory -- only the fields the display logic reads.
function edge(partial: Partial<GraphEdge> & Pick<GraphEdge, 'id' | 'source' | 'target'>): GraphEdge {
  return {
    fk_columns: [],
    pk_columns: [],
    constraint_name: null,
    inferred: false,
    ...partial,
  }
}

// orders.customer_id -> customers.id  (declared)
const e1 = edge({ id: 'e1', source: 'c.s.orders', target: 'c.s.customers', fk_columns: ['customer_id'], pk_columns: ['id'] })
// orders.product_id -> products.id  (declared)
const e2 = edge({ id: 'e2', source: 'c.s.orders', target: 'c.s.products', fk_columns: ['product_id'], pk_columns: ['id'] })
// line_items (composite FK) -> orders  (declared)
const e3 = edge({ id: 'e3', source: 'c.s.line_items', target: 'c.s.orders', fk_columns: ['order_id', 'order_line'], pk_columns: ['id', 'line_no'] })
// inferred dashed edge
const eInf = edge({ id: 'eInf', source: 'c.s.events', target: 'c.s.customers', fk_columns: ['cust'], pk_columns: ['id'], inferred: true })
const allEdges = [e1, e2, e3, eInf]

describe('edgesForKey', () => {
  it('lights the edge when hovering the FK-side column', () => {
    assert.deepEqual([...edgesForKey({ nodeId: 'c.s.orders', column: 'customer_id' }, allEdges)], ['e1'])
  })

  it('lights the edge when hovering the PK-side column', () => {
    assert.deepEqual([...edgesForKey({ nodeId: 'c.s.customers', column: 'id' }, allEdges)], ['e1', 'eInf'])
  })

  it('lights a composite relationship from any of its columns', () => {
    assert.deepEqual([...edgesForKey({ nodeId: 'c.s.line_items', column: 'order_line' }, allEdges)], ['e3'])
  })

  it('returns nothing for no hovered key, or a column in no relationship', () => {
    assert.equal(edgesForKey(null, allEdges).size, 0)
    assert.equal(edgesForKey({ nodeId: 'c.s.orders', column: 'notes' }, allEdges).size, 0)
  })
})

describe('activeEdgeIds -- hover, clearing, and moving between targets', () => {
  it('is empty when nothing is hovered (details cleared)', () => {
    assert.equal(activeEdgeIds(null, null, allEdges).size, 0)
  })

  it('activates the hovered edge', () => {
    assert.deepEqual([...activeEdgeIds('e2', null, allEdges)], ['e2'])
  })

  it('moving to a new edge leaves NO stale detail from the previous one', () => {
    const first = activeEdgeIds('e1', null, allEdges)
    const then = activeEdgeIds('e2', null, allEdges) // pointer moved e1 -> e2
    assert.deepEqual([...first], ['e1'])
    assert.deepEqual([...then], ['e2'])
    assert.equal(then.has('e1'), false)
  })

  it('a hovered key activates its relationship', () => {
    assert.deepEqual([...activeEdgeIds(null, { nodeId: 'c.s.orders', column: 'product_id' }, allEdges)], ['e2'])
  })
})

describe('shouldShowLabel -- hover-only, never on selection', () => {
  it('shows the label only for an active edge', () => {
    const active = activeEdgeIds('e1', null, allEdges)
    assert.equal(shouldShowLabel('e1', active), true)
    assert.equal(shouldShowLabel('e2', active), false)
  })

  it('shows no labels when nothing is hovered, regardless of selection', () => {
    const active = activeEdgeIds(null, null, allEdges) // hover cleared
    assert.equal(allEdges.every((e) => shouldShowLabel(e.id, active) === false), true)
  })
})

describe('computeEdgeVisual -- selection focus without persistent labels', () => {
  it('selecting a table dims out-of-set edges but shows NO labels (regression guard)', () => {
    // orders is focused: visibleSet = orders + its neighbors, but pointer hovers nothing.
    const visibleSet = new Set(['c.s.orders', 'c.s.customers', 'c.s.products'])
    const active = activeEdgeIds(null, null, allEdges)
    const inSetEdge = computeEdgeVisual({ edge: e1, active, visibleSet, hasSelection: true })
    const outOfSetEdge = computeEdgeVisual({ edge: e3, active, visibleSet, hasSelection: true })

    // Focus is applied...
    assert.equal(inSetEdge.animated, true)
    assert.equal(inSetEdge.opacity, 1)
    assert.equal(outOfSetEdge.dimmed, true)
    assert.equal(outOfSetEdge.opacity, 0.1)
    // ...but the click never reveals join-key labels. This is the core fix.
    assert.equal(inSetEdge.showLabel, false)
    assert.equal(outOfSetEdge.showLabel, false)
  })

  it('hovering an edge while a table is selected reveals just that edge label', () => {
    const visibleSet = new Set(['c.s.orders', 'c.s.customers', 'c.s.products'])
    const active = activeEdgeIds('e1', null, allEdges)
    assert.equal(computeEdgeVisual({ edge: e1, active, visibleSet, hasSelection: true }).showLabel, true)
    assert.equal(computeEdgeVisual({ edge: e2, active, visibleSet, hasSelection: true }).showLabel, false)
  })

  it('clearing the selection (background click) un-dims everything', () => {
    const active = activeEdgeIds(null, null, allEdges)
    const v = computeEdgeVisual({ edge: e3, active, visibleSet: null, hasSelection: false })
    assert.equal(v.dimmed, false)
    assert.equal(v.opacity, 1)
    assert.equal(v.animated, false)
  })
})

describe('edgeInSelection', () => {
  it('treats a null selection as "everything visible"', () => {
    assert.equal(edgeInSelection(e1, null), true)
  })
  it('requires both endpoints visible', () => {
    const set = new Set(['c.s.orders', 'c.s.customers'])
    assert.equal(edgeInSelection(e1, set), true) // orders + customers
    assert.equal(edgeInSelection(e2, set), false) // products not in set
  })
})

describe('highlightedColumnsByNode -- matching-column highlight (extra B)', () => {
  it('highlights both endpoint columns of an active edge', () => {
    const active = activeEdgeIds('e1', null, allEdges)
    const map = highlightedColumnsByNode(active, allEdges)
    assert.deepEqual([...(map.get('c.s.orders') ?? [])], ['customer_id'])
    assert.deepEqual([...(map.get('c.s.customers') ?? [])], ['id'])
  })

  it('highlights every column of a composite relationship on both sides', () => {
    const active = activeEdgeIds('e3', null, allEdges)
    const map = highlightedColumnsByNode(active, allEdges)
    assert.deepEqual([...(map.get('c.s.line_items') ?? [])].sort(), ['order_id', 'order_line'])
    assert.deepEqual([...(map.get('c.s.orders') ?? [])].sort(), ['id', 'line_no'])
  })

  it('nothing highlighted when no edge is active', () => {
    assert.equal(highlightedColumnsByNode(new Set(), allEdges).size, 0)
  })
})

describe('multiple key-level relationship lines', () => {
  it('two FKs out of the same table are independent edges, hovered one at a time', () => {
    // orders has two outgoing FKs (e1, e2). Hovering one must not activate the other.
    const a1 = activeEdgeIds('e1', null, allEdges)
    const a2 = activeEdgeIds('e2', null, allEdges)
    assert.equal(shouldShowLabel('e1', a1) && !shouldShowLabel('e2', a1), true)
    assert.equal(shouldShowLabel('e2', a2) && !shouldShowLabel('e1', a2), true)
  })
})

describe('formatJoinLabel -- inline vs stacked reflow', () => {
  it('keeps a short single-column mapping inline', () => {
    const l = formatJoinLabel(['id'], ['id'], false)
    assert.equal(l.stacked, false)
    assert.equal(l.fk, 'id')
    assert.equal(l.pk, 'id')
  })

  it('keeps an ordinary same-name single-column join inline (no over-stacking)', () => {
    // "customer_id -> customer_id" is ~25 chars -- common and fine on one line.
    assert.equal(formatJoinLabel(['customer_id'], ['customer_id'], false).stacked, false)
  })

  it('stacks a composite key even if short', () => {
    assert.equal(formatJoinLabel(['a', 'b'], ['x', 'y'], false).stacked, true)
  })

  it('stacks a long single-column mapping past the threshold', () => {
    const long = 'a'.repeat(LABEL_STACK_THRESHOLD)
    assert.equal(formatJoinLabel([long], ['id'], false).stacked, true)
  })

  it('carries the inferred flag through', () => {
    assert.equal(formatJoinLabel(['x'], ['id'], true).inferred, true)
  })
})
