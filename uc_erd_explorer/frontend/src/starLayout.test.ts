import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import type { Edge, Node } from 'reactflow'
import { layoutStar } from './starLayout.ts'
import type { ColumnMeta, TableNodeData } from './types'

function col(name: string): ColumnMeta {
  return { name, type: 'string', is_pk: false, is_fk: false, comment: null, tags: [] }
}

function node(id: string, ncols = 2): Node<TableNodeData> {
  return {
    id,
    type: 'table',
    position: { x: 0, y: 0 },
    data: {
      id,
      catalog: 'c',
      schema: 's',
      table: id,
      comment: null,
      tags: [],
      columns: Array.from({ length: ncols }, (_, i) => col(`col${i}`)),
    },
  }
}

function edge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target }
}

// The rendered box of a placed node (React Flow position is the top-left corner).
function box(n: Node<TableNodeData>) {
  const w = n.width ?? 0
  const h = n.height ?? 0
  return { left: n.position.x, top: n.position.y, right: n.position.x + w, bottom: n.position.y + h }
}

function center(n: Node<TableNodeData>) {
  const b = box(n)
  return { x: (b.left + b.right) / 2, y: (b.top + b.bottom) / 2 }
}

function overlaps(a: Node<TableNodeData>, b: Node<TableNodeData>) {
  const ba = box(a)
  const bb = box(b)
  return !(ba.right <= bb.left || bb.right <= ba.left || ba.bottom <= bb.top || bb.bottom <= ba.top)
}

describe('layoutStar', () => {
  it('places the center node with its geometric center at the origin', () => {
    const nodes = [node('center'), node('n1'), node('n2')]
    const edges = [edge('center', 'n1'), edge('center', 'n2')]
    const { nodes: laid, centerNodeId } = layoutStar('center', nodes, edges)
    assert.equal(centerNodeId, 'center')
    const c = center(laid.find((n) => n.id === 'center')!)
    assert.ok(Math.abs(c.x) < 1e-6 && Math.abs(c.y) < 1e-6)
  })

  it('positions neighbors at distinct angles, all on the same radius', () => {
    const nodes = ['center', 'a', 'b', 'd', 'e', 'f', 'g'].map((id) => node(id))
    const edges = ['a', 'b', 'd', 'e', 'f', 'g'].map((t) => edge('center', t))
    const { nodes: laid } = layoutStar('center', nodes, edges)
    const neighbors = laid.filter((n) => n.id !== 'center')
    assert.equal(neighbors.length, 6)

    const radii = neighbors.map((n) => {
      const c = center(n)
      return Math.hypot(c.x, c.y)
    })
    const r0 = radii[0]
    for (const r of radii) assert.ok(Math.abs(r - r0) < 1e-6, 'all neighbors share one radius')

    const angles = neighbors.map((n) => {
      const c = center(n)
      return Math.atan2(c.y, c.x).toFixed(4)
    })
    assert.equal(new Set(angles).size, neighbors.length, 'angles are distinct')
  })

  it('does not overlap any two neighbor cards', () => {
    const nodes = ['center', 'a', 'b', 'd', 'e', 'f'].map((id) => node(id))
    const edges = ['a', 'b', 'd', 'e', 'f'].map((t) => edge('center', t))
    const { nodes: laid } = layoutStar('center', nodes, edges)
    const neighbors = laid.filter((n) => n.id !== 'center')
    for (let i = 0; i < neighbors.length; i++) {
      for (let j = i + 1; j < neighbors.length; j++) {
        assert.ok(!overlaps(neighbors[i], neighbors[j]), `${neighbors[i].id} overlaps ${neighbors[j].id}`)
      }
    }
  })

  it('a center with no neighbors returns just the center', () => {
    const nodes = [node('lonely'), node('unrelated1'), node('unrelated2')]
    const edges = [edge('unrelated1', 'unrelated2')]
    const { nodes: laid } = layoutStar('lonely', nodes, edges)
    assert.equal(laid.length, 1)
    assert.equal(laid[0].id, 'lonely')
  })

  it('excludes nodes/edges not touching the center or its neighbors', () => {
    const nodes = ['center', 'n1', 'x', 'y'].map((id) => node(id))
    const edges = [edge('center', 'n1'), edge('x', 'y')]
    const { nodes: laid } = layoutStar('center', nodes, edges)
    const ids = new Set(laid.map((n) => n.id))
    assert.deepEqual([...ids].sort(), ['center', 'n1'])
  })

  it('returns an empty star for an unknown center id', () => {
    const { nodes: laid } = layoutStar('nope', [node('a')], [])
    assert.equal(laid.length, 0)
  })

  it('sets width/height on every placed node (for layout + overlap math)', () => {
    const nodes = [node('center'), node('n1')]
    const { nodes: laid } = layoutStar('center', nodes, [edge('center', 'n1')])
    for (const n of laid) {
      assert.ok((n.width ?? 0) > 0 && (n.height ?? 0) > 0)
    }
  })
})
