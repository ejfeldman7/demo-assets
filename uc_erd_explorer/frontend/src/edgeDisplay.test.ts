import { describe, expect, it } from 'vitest'
import {
  activeEdgeIds,
  computeEdgeVisual,
  edgeInSelection,
  edgesForKey,
  formatJoinLabel,
  highlightedColumnsByNode,
  LABEL_STACK_THRESHOLD,
  shouldShowLabel,
} from './edgeDisplay'
import type { GraphEdge } from './types'

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
    expect([...edgesForKey({ nodeId: 'c.s.orders', column: 'customer_id' }, allEdges)]).toEqual(['e1'])
  })

  it('lights the edge when hovering the PK-side column', () => {
    expect([...edgesForKey({ nodeId: 'c.s.customers', column: 'id' }, allEdges)]).toEqual(['e1', 'eInf'])
  })

  it('lights a composite relationship from any of its columns', () => {
    expect([...edgesForKey({ nodeId: 'c.s.line_items', column: 'order_line' }, allEdges)]).toEqual(['e3'])
  })

  it('returns nothing for no hovered key, or a column in no relationship', () => {
    expect(edgesForKey(null, allEdges).size).toBe(0)
    expect(edgesForKey({ nodeId: 'c.s.orders', column: 'notes' }, allEdges).size).toBe(0)
  })
})

describe('activeEdgeIds -- hover, clearing, and moving between targets', () => {
  it('is empty when nothing is hovered (details cleared)', () => {
    expect(activeEdgeIds(null, null, allEdges).size).toBe(0)
  })

  it('activates the hovered edge', () => {
    expect([...activeEdgeIds('e2', null, allEdges)]).toEqual(['e2'])
  })

  it('moving to a new edge leaves NO stale detail from the previous one', () => {
    const first = activeEdgeIds('e1', null, allEdges)
    const then = activeEdgeIds('e2', null, allEdges) // pointer moved e1 -> e2
    expect([...first]).toEqual(['e1'])
    expect([...then]).toEqual(['e2'])
    expect(then.has('e1')).toBe(false)
  })

  it('a hovered key activates its relationship', () => {
    expect([...activeEdgeIds(null, { nodeId: 'c.s.orders', column: 'product_id' }, allEdges)]).toEqual(['e2'])
  })
})

describe('shouldShowLabel -- hover-only, never on selection', () => {
  it('shows the label only for an active edge', () => {
    const active = activeEdgeIds('e1', null, allEdges)
    expect(shouldShowLabel('e1', active)).toBe(true)
    expect(shouldShowLabel('e2', active)).toBe(false)
  })

  it('shows no labels when nothing is hovered, regardless of selection', () => {
    const active = activeEdgeIds(null, null, allEdges) // hover cleared
    expect(allEdges.every((e) => shouldShowLabel(e.id, active) === false)).toBe(true)
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
    expect(inSetEdge.animated).toBe(true)
    expect(inSetEdge.opacity).toBe(1)
    expect(outOfSetEdge.dimmed).toBe(true)
    expect(outOfSetEdge.opacity).toBeCloseTo(0.1)
    // ...but the click never reveals join-key labels. This is the core fix.
    expect(inSetEdge.showLabel).toBe(false)
    expect(outOfSetEdge.showLabel).toBe(false)
  })

  it('hovering an edge while a table is selected reveals just that edge label', () => {
    const visibleSet = new Set(['c.s.orders', 'c.s.customers', 'c.s.products'])
    const active = activeEdgeIds('e1', null, allEdges)
    expect(computeEdgeVisual({ edge: e1, active, visibleSet, hasSelection: true }).showLabel).toBe(true)
    expect(computeEdgeVisual({ edge: e2, active, visibleSet, hasSelection: true }).showLabel).toBe(false)
  })

  it('clearing the selection (background click) un-dims everything', () => {
    const active = activeEdgeIds(null, null, allEdges)
    const v = computeEdgeVisual({ edge: e3, active, visibleSet: null, hasSelection: false })
    expect(v.dimmed).toBe(false)
    expect(v.opacity).toBe(1)
    expect(v.animated).toBe(false)
  })
})

describe('edgeInSelection', () => {
  it('treats a null selection as "everything visible"', () => {
    expect(edgeInSelection(e1, null)).toBe(true)
  })
  it('requires both endpoints visible', () => {
    const set = new Set(['c.s.orders', 'c.s.customers'])
    expect(edgeInSelection(e1, set)).toBe(true) // orders + customers
    expect(edgeInSelection(e2, set)).toBe(false) // products not in set
  })
})

describe('highlightedColumnsByNode -- matching-column highlight (extra B)', () => {
  it('highlights both endpoint columns of an active edge', () => {
    const active = activeEdgeIds('e1', null, allEdges)
    const map = highlightedColumnsByNode(active, allEdges)
    expect([...(map.get('c.s.orders') ?? [])]).toEqual(['customer_id'])
    expect([...(map.get('c.s.customers') ?? [])]).toEqual(['id'])
  })

  it('highlights every column of a composite relationship on both sides', () => {
    const active = activeEdgeIds('e3', null, allEdges)
    const map = highlightedColumnsByNode(active, allEdges)
    expect([...(map.get('c.s.line_items') ?? [])].sort()).toEqual(['order_id', 'order_line'])
    expect([...(map.get('c.s.orders') ?? [])].sort()).toEqual(['id', 'line_no'])
  })

  it('nothing highlighted when no edge is active', () => {
    expect(highlightedColumnsByNode(new Set(), allEdges).size).toBe(0)
  })
})

describe('multiple key-level relationship lines', () => {
  it('two FKs out of the same table are independent edges, hovered one at a time', () => {
    // orders has two outgoing FKs (e1, e2). Hovering one must not activate the other.
    const a1 = activeEdgeIds('e1', null, allEdges)
    const a2 = activeEdgeIds('e2', null, allEdges)
    expect(shouldShowLabel('e1', a1) && !shouldShowLabel('e2', a1)).toBe(true)
    expect(shouldShowLabel('e2', a2) && !shouldShowLabel('e1', a2)).toBe(true)
  })
})

describe('formatJoinLabel -- inline vs stacked reflow', () => {
  it('keeps a short single-column mapping inline', () => {
    const l = formatJoinLabel(['id'], ['id'], false)
    expect(l.stacked).toBe(false)
    expect(l.fk).toBe('id')
    expect(l.pk).toBe('id')
  })

  it('keeps an ordinary same-name single-column join inline (no over-stacking)', () => {
    // "customer_id → customer_id" is ~25 chars -- common and fine on one line.
    expect(formatJoinLabel(['customer_id'], ['customer_id'], false).stacked).toBe(false)
  })

  it('stacks a composite key even if short', () => {
    expect(formatJoinLabel(['a', 'b'], ['x', 'y'], false).stacked).toBe(true)
  })

  it('stacks a long single-column mapping past the threshold', () => {
    const long = 'a'.repeat(LABEL_STACK_THRESHOLD)
    expect(formatJoinLabel([long], ['id'], false).stacked).toBe(true)
  })

  it('carries the inferred flag through', () => {
    expect(formatJoinLabel(['x'], ['id'], true).inferred).toBe(true)
  })
})
