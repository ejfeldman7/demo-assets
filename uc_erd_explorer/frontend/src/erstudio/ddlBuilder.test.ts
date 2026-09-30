import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { buildDdl } from './ddlBuilder.ts'
import { buildMetadataCsv } from './metadataCsv.ts'
import { scopeGraph } from '../graphScope.ts'
import type { GraphResponse, TableNodeData, GraphEdge } from '../types.ts'

// ---------------------------------------------------------------------------
// Minimal fixture helpers
// ---------------------------------------------------------------------------

function node(catalog: string, schema: string, table: string, opts: {
  pk?: string; fk?: string; fkRef?: string; boundary?: boolean
} = {}): TableNodeData {
  const columns = []
  if (opts.pk) columns.push({ name: opts.pk, type: 'bigint', is_pk: true, is_fk: false, comment: null, tags: [] })
  if (opts.fk) columns.push({
    name: opts.fk, type: 'bigint', is_pk: false, is_fk: true, comment: null, tags: [],
    references: opts.fkRef ? { table: opts.fkRef, column: 'id', in_view: !opts.boundary } : null,
  })
  return {
    id: `${catalog}.${schema}.${table}`,
    catalog, schema, table,
    comment: null, tags: [], columns,
    ...(opts.boundary ? { is_boundary: true } : {}),
  }
}

function edge(
  fkCatalog: string, fkSchema: string, fkTable: string,
  pkCatalog: string, pkSchema: string, pkTable: string,
  opts: { cross_scope?: boolean; inferred?: boolean; name?: string } = {},
): GraphEdge {
  return {
    id: opts.name ?? `${fkTable}_${pkTable}_fk`,
    source: `${fkCatalog}.${fkSchema}.${fkTable}`,
    target: `${pkCatalog}.${pkSchema}.${pkTable}`,
    fk_columns: ['parent_id'],
    pk_columns: ['id'],
    constraint_name: opts.name ?? null,
    inferred: opts.inferred ?? false,
    cross_scope: opts.cross_scope ?? false,
  }
}

function graph(nodes: TableNodeData[], edges: GraphEdge[]): GraphResponse {
  const catalogs = [...new Set(nodes.filter(n => !n.is_boundary).map(n => n.catalog))]
  return { catalogs, unscoped: false, pairs: null, view: 'detail', nodes, edges }
}

// ---------------------------------------------------------------------------
// ddlBuilder -- cross-catalog FK
// ---------------------------------------------------------------------------

describe('buildDdl cross-catalog FK', () => {
  it('emits FOREIGN KEY for a declared FK whose target is in nodeById (same-catalog baseline)', () => {
    const src = node('cat', 's', 'orders', { pk: 'id', fk: 'customer_id', fkRef: 'cat.s.customers' })
    const tgt = node('cat', 's', 'customers', { pk: 'id' })
    const e1 = edge('cat', 's', 'orders', 'cat', 's', 'customers', { name: 'orders_customers_fk' })
    const { sql } = buildDdl(graph([src, tgt], [e1]), 'sqlserver')
    assert.ok(sql.includes('ALTER TABLE [cat__s].[orders] ADD CONSTRAINT [orders_customers_fk] FOREIGN KEY'))
    assert.ok(sql.includes('REFERENCES [cat__s].[customers]'))
  })

  it('emits FOREIGN KEY for a declared FK whose target is a cross-catalog boundary stub', () => {
    const src = node('cat_a', 's', 'fact_table', { pk: 'id', fk: 'dim_id', fkRef: 'cat_b.s.dim_table', boundary: false })
    // boundary stub: is_boundary=true, no columns, not in nodeById after scopeGraph strips it
    const stub = node('cat_b', 's', 'dim_table', { boundary: true })
    const e1 = edge('cat_a', 's', 'fact_table', 'cat_b', 's', 'dim_table', { cross_scope: true, name: 'fact_dim_fk' })
    // scopeGraph(keepDeclaredCrossScope: true) strips boundary nodes but keeps declared cross-scope edges
    const scoped = scopeGraph(graph([src, stub], [e1]), null, { keepDeclaredCrossScope: true })
    const { sql } = buildDdl(scoped, 'sqlserver')
    assert.ok(sql.includes('ALTER TABLE [cat_a__s].[fact_table] ADD CONSTRAINT [fact_dim_fk] FOREIGN KEY'))
    assert.ok(sql.includes('REFERENCES [cat_b__s].[dim_table]'), `Expected REFERENCES [cat_b__s].[dim_table] in:\n${sql}`)
  })

  it('does NOT emit FOREIGN KEY for an inferred cross-scope edge', () => {
    const src = node('cat_a', 's', 'orders', { pk: 'id', fk: 'dim_id' })
    const stub = node('cat_b', 's', 'dims', { boundary: true })
    const e1 = edge('cat_a', 's', 'orders', 'cat_b', 's', 'dims', { cross_scope: true, inferred: true })
    const scoped = scopeGraph(graph([src, stub], [e1]), null, { keepDeclaredCrossScope: true })
    const { sql } = buildDdl(scoped, 'sqlserver')
    assert.ok(!sql.includes('FOREIGN KEY'), `Expected no FOREIGN KEY for inferred edge, got:\n${sql}`)
  })

  it('emits FK for both in-scope and cross-scope declared edges in the same export', () => {
    const src = node('cat_a', 's', 'orders', { pk: 'id' })
    const inScope = node('cat_a', 's', 'customers', { pk: 'id' })
    const stub = node('cat_b', 's', 'regions', { boundary: true })
    const e1 = edge('cat_a', 's', 'orders', 'cat_a', 's', 'customers', { name: 'orders_customers_fk' })
    const e2 = edge('cat_a', 's', 'orders', 'cat_b', 's', 'regions', { cross_scope: true, name: 'orders_regions_fk' })
    const scoped = scopeGraph(graph([src, inScope, stub], [e1, e2]), null, { keepDeclaredCrossScope: true })
    const { sql } = buildDdl(scoped, 'sqlserver')
    assert.ok(sql.includes('orders_customers_fk'))
    assert.ok(sql.includes('orders_regions_fk'))
    assert.ok(sql.includes('[cat_b__s].[regions]'))
  })
})

// ---------------------------------------------------------------------------
// scopeGraph -- keepDeclaredCrossScope option
// ---------------------------------------------------------------------------

describe('scopeGraph', () => {
  it('drops cross_scope edges by default (MD/YAML/JSON path)', () => {
    const src = node('a', 's', 't1', { pk: 'id' })
    const stub = node('b', 's', 't2', { boundary: true })
    const e1 = edge('a', 's', 't1', 'b', 's', 't2', { cross_scope: true })
    const scoped = scopeGraph(graph([src, stub], [e1]), null)
    assert.equal(scoped.edges.length, 0)
    assert.equal(scoped.nodes.length, 1) // stub stripped too
  })

  it('keeps declared cross_scope edges when keepDeclaredCrossScope is true', () => {
    const src = node('a', 's', 't1', { pk: 'id' })
    const stub = node('b', 's', 't2', { boundary: true })
    const e1 = edge('a', 's', 't1', 'b', 's', 't2', { cross_scope: true })
    const scoped = scopeGraph(graph([src, stub], [e1]), null, { keepDeclaredCrossScope: true })
    assert.equal(scoped.edges.length, 1)
    assert.equal(scoped.nodes.length, 1) // stub still stripped (no CREATE TABLE for it)
  })

  it('still drops inferred cross_scope edges even with keepDeclaredCrossScope', () => {
    const src = node('a', 's', 't1', { pk: 'id' })
    const stub = node('b', 's', 't2', { boundary: true })
    const e1 = edge('a', 's', 't1', 'b', 's', 't2', { cross_scope: true, inferred: true })
    const scoped = scopeGraph(graph([src, stub], [e1]), null, { keepDeclaredCrossScope: true })
    assert.equal(scoped.edges.length, 0)
  })

  it('still drops boundary nodes even with keepDeclaredCrossScope', () => {
    const src = node('a', 's', 't1', { pk: 'id' })
    const stub = node('b', 's', 't2', { boundary: true })
    const e1 = edge('a', 's', 't1', 'b', 's', 't2', { cross_scope: true })
    const scoped = scopeGraph(graph([src, stub], [e1]), null, { keepDeclaredCrossScope: true })
    assert.ok(!(scoped.nodes as TableNodeData[]).some((n) => n.is_boundary))
  })
})

// ---------------------------------------------------------------------------
// buildMetadataCsv -- declared_fk_target column
// ---------------------------------------------------------------------------

describe('buildMetadataCsv declared_fk_target', () => {
  it('populates declared_fk_target for a same-catalog FK whose target is in scope', () => {
    const src = node('cat', 's', 'orders', { pk: 'id', fk: 'customer_id', fkRef: 'cat.s.customers' })
    const tgt = node('cat', 's', 'customers', { pk: 'id' })
    const csv = buildMetadataCsv(graph([src, tgt], []))
    const lines = csv.split('\n')
    const fkLine = lines.find((l) => l.includes('customer_id'))!
    assert.ok(fkLine, 'customer_id row not found')
    const cols = fkLine.split(',')
    const headerLine = lines[0].split(',')
    const fkTargetIdx = headerLine.indexOf('declared_fk_target')
    assert.notEqual(fkTargetIdx, -1, 'declared_fk_target column missing from header')
    assert.equal(cols[fkTargetIdx], 'cat.s.customers')
  })

  it('populates declared_fk_target for a cross-catalog FK (boundary stub target)', () => {
    const src = node('cat_a', 's', 'fact', { fk: 'dim_id', fkRef: 'cat_b.s.dim', boundary: false })
    // After scopeGraph the stub is stripped, but the FK column still has references set
    const stub = node('cat_b', 's', 'dim', { boundary: true })
    const csv = buildMetadataCsv(graph([src, stub], []))
    const lines = csv.split('\n')
    const fkLine = lines.find((l) => l.includes('dim_id'))!
    assert.ok(fkLine, 'dim_id row not found')
    const cols = fkLine.split(',')
    const headerLine = lines[0].split(',')
    const fkTargetIdx = headerLine.indexOf('declared_fk_target')
    assert.equal(cols[fkTargetIdx], 'cat_b.s.dim')
  })

  it('leaves declared_fk_target empty for non-FK columns', () => {
    const src = node('cat', 's', 't', { pk: 'id' })
    const csv = buildMetadataCsv(graph([src], []))
    const lines = csv.split('\n')
    const pkLine = lines.find((l) => l.includes(',id,'))!
    assert.ok(pkLine, 'id row not found')
    const cols = pkLine.split(',')
    const headerLine = lines[0].split(',')
    const fkTargetIdx = headerLine.indexOf('declared_fk_target')
    assert.equal(cols[fkTargetIdx], '')
  })
})
