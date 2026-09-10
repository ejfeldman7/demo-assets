import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { classifyTables, roleCounts, suggestStarCenter } from './classify.ts'
import type { ColumnMeta, GraphEdge, TableNodeData } from './types'

function c(name: string, opts: { pk?: boolean; fk?: boolean; type?: string } = {}): ColumnMeta {
  return { name, type: opts.type ?? 'string', is_pk: !!opts.pk, is_fk: !!opts.fk, comment: null, tags: [] }
}

function table(id: string, columns: ColumnMeta[] = [], comment: string | null = null): TableNodeData {
  const [catalog, schema, tbl] = id.split('.')
  return { id, catalog, schema, table: tbl, comment, tags: [], columns }
}

function edge(source: string, target: string): GraphEdge {
  return { id: `${source}->${target}`, source, target, fk_columns: ['x'], pk_columns: ['x'], constraint_name: null, inferred: false }
}

describe('classifyTables — naming short-circuit (explicit dimensional schemas)', () => {
  it('fact_ / fct_ names classify as fact even with zero FKs', () => {
    const nodes = [table('c.gold.fct_sales'), table('c.gold.fact_moq_exception')]
    const r = classifyTables(nodes, [])
    assert.equal(r.get('c.gold.fct_sales')!.role, 'fact')
    assert.equal(r.get('c.gold.fact_moq_exception')!.role, 'fact')
  })

  it('dim_ name classifies as dimension even with many outgoing FKs (naming overrides structure)', () => {
    const nodes = [table('c.gold.dim_date', [c('date_key', { pk: true })])]
    // 5 outgoing FKs would otherwise scream "fact" structurally.
    const edges = ['a', 'b', 'e', 'f', 'g'].map((t) => edge('c.gold.dim_date', `c.gold.${t}`))
    assert.equal(classifyTables(nodes, edges).get('c.gold.dim_date')!.role, 'dimension')
  })

  it('bridge_/junction_/xref_ names classify as junction', () => {
    const nodes = [table('c.s.bridge_account_contact'), table('c.s.customer_xref')]
    const r = classifyTables(nodes, [])
    assert.equal(r.get('c.s.bridge_account_contact')!.role, 'junction')
    assert.equal(r.get('c.s.customer_xref')!.role, 'junction')
  })

  it('does not false-positive on words that merely contain a keyword', () => {
    // "dimensional_model" / "factory" are not whole-segment dim/fact matches.
    const nodes = [table('c.s.factory', [c('id', { pk: true })]), table('c.s.dimensional_model', [c('id', { pk: true })])]
    const r = classifyTables(nodes, [])
    assert.notEqual(r.get('c.s.factory')!.role, 'fact')
    assert.notEqual(r.get('c.s.dimensional_model')!.role, 'dimension')
  })
})

describe('classifyTables — comment keywords', () => {
  it('classifies by comment when naming is neutral', () => {
    const nodes = [
      table('c.s.orders', [c('id', { pk: true })], 'Fact table of placed orders'),
      table('c.s.geography', [c('id', { pk: true })], 'Geography dimension used across marts'),
      table('c.s.link', [c('id', { pk: true })], 'Bridge / many-to-many between A and B'),
    ]
    const r = classifyTables(nodes, [])
    assert.equal(r.get('c.s.orders')!.role, 'fact')
    assert.equal(r.get('c.s.geography')!.role, 'dimension')
    assert.equal(r.get('c.s.link')!.role, 'junction')
  })
})

describe('classifyTables — composite PK', () => {
  it('a >=3-column PK is a junction (before structural scoring)', () => {
    const nodes = [
      table('c.s.work_order_operators', [
        c('work_order_id', { pk: true }),
        c('operator_id', { pk: true }),
        c('shift_id', { pk: true }),
      ]),
    ]
    assert.equal(classifyTables(nodes, []).get('c.s.work_order_operators')!.role, 'junction')
  })
})

// A less-explicit, normalized schema (megacorp-style) with no dim_/fact_ names -- the
// structural fallback must still find the obvious facts and dimensions.
function megacorpish(): { nodes: TableNodeData[]; edges: GraphEdge[] } {
  const nodes = [
    table('m.factory.plants', [c('plant_id', { pk: true }), c('name')]),
    table('m.factory.materials', [c('material_id', { pk: true }), c('name')]),
    table('m.factory.production_lines', [c('production_line_id', { pk: true }), c('plant_id', { fk: true })]),
    table('m.factory.work_orders', [
      c('work_order_id', { pk: true }),
      c('production_line_id', { fk: true }),
      c('material_id', { fk: true }),
      c('status'),
      c('quantity', { type: 'int' }),
      c('cost', { type: 'decimal(18,2)' }),
    ]),
    table('m.factory.quality_inspections', [c('inspection_id', { pk: true }), c('work_order_id', { fk: true })]),
    table('m.factory.work_order_operators', [
      c('work_order_id', { pk: true }),
      c('operator_id', { pk: true }),
      c('shift_id', { pk: true }),
    ]),
    table('m.erp.sales_orders', [c('sales_order_id', { pk: true }), c('customer_id', { fk: true })]),
    table('m.erp.sales_order_lines', [
      c('line_id', { pk: true }),
      c('sales_order_id', { fk: true }),
      c('material_id', { fk: true }),
      c('quantity', { type: 'int' }),
      c('unit_price', { type: 'decimal(18,2)' }),
      c('line_total', { type: 'decimal(18,2)' }),
    ]),
    table('m.erp.customers', [c('customer_id', { pk: true }), c('name')]),
    table('m.erp.invoices', [
      c('invoice_id', { pk: true }),
      c('sales_order_id', { fk: true }),
      c('customer_id', { fk: true }),
      c('amount', { type: 'decimal(18,2)' }),
      c('tax', { type: 'decimal(18,2)' }),
    ]),
    table('m.erp.vendors', [c('vendor_id', { pk: true }), c('name')]),
    table('m.erp.purchase_orders', [c('po_id', { pk: true }), c('vendor_id', { fk: true })]),
  ]
  const edges = [
    edge('m.factory.production_lines', 'm.factory.plants'),
    edge('m.factory.work_orders', 'm.factory.production_lines'),
    edge('m.factory.work_orders', 'm.factory.materials'),
    edge('m.factory.quality_inspections', 'm.factory.work_orders'),
    edge('m.factory.work_order_operators', 'm.factory.work_orders'),
    edge('m.erp.sales_order_lines', 'm.erp.sales_orders'),
    edge('m.erp.sales_order_lines', 'm.factory.materials'),
    edge('m.erp.sales_orders', 'm.erp.customers'),
    edge('m.erp.invoices', 'm.erp.sales_orders'),
    edge('m.erp.invoices', 'm.erp.customers'),
    edge('m.erp.purchase_orders', 'm.erp.vendors'),
  ]
  return { nodes, edges }
}

describe('classifyTables — structural fallback (less-explicit schema)', () => {
  const { nodes, edges } = megacorpish()
  const r = classifyTables(nodes, edges)

  it('tables referenced but referencing nothing are dimensions', () => {
    for (const id of ['m.factory.plants', 'm.factory.materials', 'm.erp.customers', 'm.erp.vendors']) {
      assert.equal(r.get(id)!.role, 'dimension', `${id} should be a dimension`)
    }
  })

  it('tables with >=2 outgoing FKs and measure columns are facts', () => {
    for (const id of ['m.factory.work_orders', 'm.erp.sales_order_lines', 'm.erp.invoices']) {
      assert.equal(r.get(id)!.role, 'fact', `${id} should be a fact`)
    }
  })

  it('composite-PK associative table is a junction', () => {
    assert.equal(r.get('m.factory.work_order_operators')!.role, 'junction')
  })

  it('every table gets human-readable signals', () => {
    for (const cls of r.values()) assert.ok(cls.signals.length > 0)
  })

  it('a table whose 2 FKs point at the SAME table is not mistaken for a fact', () => {
    // bill_of_materials-style: parent + component both -> materials. That's ONE related
    // table (a bridge), not a two-dimension fact, so it must not be classified 'fact'.
    const nodes = [
      table('m.f.materials', [c('material_id', { pk: true })]),
      table('m.f.bill_of_materials', [
        c('bom_id', { pk: true }),
        c('parent_material_id', { fk: true }),
        c('component_material_id', { fk: true }),
      ]),
    ]
    const edges = [edge('m.f.bill_of_materials', 'm.f.materials'), edge('m.f.bill_of_materials', 'm.f.materials')]
    assert.notEqual(classifyTables(nodes, edges).get('m.f.bill_of_materials')!.role, 'fact')
  })
})

describe('suggestStarCenter', () => {
  it('returns a fact as the center on a schema with facts', () => {
    const { nodes, edges } = megacorpish()
    const center = suggestStarCenter(nodes, edges)
    assert.ok(center)
    assert.equal(classifyTables(nodes, edges).get(center!)!.role, 'fact')
  })

  it('prefers the highest-scoring fact by name when explicit', () => {
    const nodes = [table('c.gold.fact_sales', [c('id', { pk: true })]), table('c.gold.dim_customer', [c('id', { pk: true })])]
    assert.equal(suggestStarCenter(nodes, []), 'c.gold.fact_sales')
  })

  it('falls back to the most-connected hub when no facts exist (non-star schema)', () => {
    // A simple chain a->b->c: no table has >=2 outgoing FKs, so none is a fact. Center must
    // still be non-null (the star degrades gracefully rather than breaking).
    const nodes = [table('c.s.a', [c('id', { pk: true })]), table('c.s.b', [c('id', { pk: true })]), table('c.s.c', [c('id', { pk: true })])]
    const edges = [edge('c.s.a', 'c.s.b'), edge('c.s.b', 'c.s.c')]
    const center = suggestStarCenter(nodes, edges)
    assert.equal(center, 'c.s.b') // b has degree 2 (highest)
  })

  it('returns null only when there are no tables', () => {
    assert.equal(suggestStarCenter([], []), null)
  })
})

describe('roleCounts', () => {
  it('sums to the number of tables', () => {
    const { nodes, edges } = megacorpish()
    const counts = roleCounts(classifyTables(nodes, edges))
    const total = counts.fact + counts.dimension + counts.junction + counts.unknown
    assert.equal(total, nodes.length)
  })
})
