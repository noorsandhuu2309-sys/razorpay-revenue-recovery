// Focus Mode. Focus is navigation (where you are); selection is what you are
// holding. Conflating them is what makes graph products feel like they lose
// your place, so the trail has its own rules and they are asserted here.

import { beforeEach, describe, expect, it, vi } from 'vitest'

const graph = vi.fn(async () => ({ nodes: [], edges: [] }))

vi.mock('../lib/api', () => ({
  api: {
    graph: (...a: unknown[]) => graph(...(a as [])),
    object: vi.fn(async (_ws: string, id: string) => ({ id, name: id })),
    summary: vi.fn(async () => null),
    timeline: vi.fn(async () => ({ events: [] })),
    thread: vi.fn(async () => ({ turns: [] })),
  },
}))

import { useWorkspace } from './workspace'
import { useGraphUi } from './graphUi'

const obj = (id: string) => ({
  id, name: id.toUpperCase(), glyph: '◆',
} as never)

beforeEach(() => {
  graph.mockClear()
  // Density persists to localStorage, so it survives between tests unless it
  // is put back — one test choosing 'high' would otherwise decide the base
  // depth every later test measures against.
  useGraphUi.setState({ density: 'medium' })
  useWorkspace.setState({
    workspaceId: 'ws1', primary: null, selected: [], pinned: [],
    origin: 'system', contextObjects: {}, focus: [], graph: null,
    view: 'home',
  })
})

describe('focus trail', () => {
  it('pushes each drill-down onto the trail', async () => {
    await useWorkspace.getState().focusOn(obj('a'))
    await useWorkspace.getState().focusOn(obj('b'))
    expect(useWorkspace.getState().focus.map((f) => f.id)).toEqual(['a', 'b'])
  })

  // Drilling A > B > A must pop back to A, not grow a three-step trail that
  // lies about how deep the user actually is.
  it('treats re-focusing something already on the trail as a pop', async () => {
    await useWorkspace.getState().focusOn(obj('a'))
    await useWorkspace.getState().focusOn(obj('b'))
    await useWorkspace.getState().focusOn(obj('a'))

    expect(useWorkspace.getState().focus.map((f) => f.id)).toEqual(['a'])
    expect(useWorkspace.getState().primary).toBe('a')
  })

  it('focusTo(0) returns to the whole Space with an unrooted graph', async () => {
    await useWorkspace.getState().focusOn(obj('a'))
    await useWorkspace.getState().focusOn(obj('b'))
    graph.mockClear()

    await useWorkspace.getState().focusTo(0)

    expect(useWorkspace.getState().focus).toEqual([])
    expect(useWorkspace.getState().primary).toBeNull()
    // Depth 0 is the whole Space: the graph must be requested with no roots.
    const [, opts] = graph.mock.calls[0] as unknown as [string, { roots: string }]
    expect(opts.roots).toBe('')
  })

  it('focusTo(depth) truncates the trail and roots on the new head', async () => {
    await useWorkspace.getState().focusOn(obj('a'))
    await useWorkspace.getState().focusOn(obj('b'))
    await useWorkspace.getState().focusOn(obj('c'))

    await useWorkspace.getState().focusTo(1)

    expect(useWorkspace.getState().focus.map((f) => f.id)).toEqual(['a'])
    expect(useWorkspace.getState().primary).toBe('a')
  })

  // Focus earns an extra hop because a drill-down is a request to see the
  // neighbourhood, not just the node.
  //
  // Asserted as a RELATION rather than against literal hop counts. The base
  // depth is now the density control's, and pinning this test to "1" is what
  // let the Space-level view ship asking for one hop from a single seed — a
  // request that can never return more than `per_node + 1` objects, which is
  // how a 644-object Space came to open on nine nodes.
  it('requests deeper traversal once focused than at the Space level', async () => {
    await useWorkspace.getState().loadGraph()
    const [, unfocused] = graph.mock.calls[0] as unknown as [string, { hops: number }]

    graph.mockClear()
    await useWorkspace.getState().focusOn(obj('a'))
    const [, focused] = graph.mock.calls[0] as unknown as [string, { hops: number }]

    expect(focused.hops).toBeGreaterThan(unfocused.hops)
    // And the Space-level view must ask for more than one hop, or it cannot
    // show more than a single node's immediate neighbours however high
    // `max_nodes` is set.
    expect(unfocused.hops).toBeGreaterThan(1)
  })

  // The density control is a fetch parameter, so changing it has to reach the
  // server — a density that only changed what was drawn would be a lie.
  it('scales the requested subgraph with the density control', async () => {
    useGraphUi.getState().setDensity('low')
    await useWorkspace.getState().loadGraph()
    const [, low] = graph.mock.calls[0] as unknown as [string, { max_nodes: number }]

    graph.mockClear()
    useGraphUi.getState().setDensity('high')
    await useWorkspace.getState().loadGraph()
    const [, high] = graph.mock.calls[0] as unknown as [string, { max_nodes: number }]

    expect(high.max_nodes).toBeGreaterThan(low.max_nodes)
  })

  it('moves off Home when focusing, since Home has no canvas to rebuild', async () => {
    expect(useWorkspace.getState().view).toBe('home')
    await useWorkspace.getState().focusOn(obj('a'))
    expect(useWorkspace.getState().view).toBe('graph')
  })
})

describe('switching Space', () => {
  // Focus and selection are both scoped to a Space. Carrying either across
  // leaves the breadcrumb naming objects that no longer exist.
  it('clears focus, selection and thread when the Space changes', async () => {
    await useWorkspace.getState().focusOn(obj('a'))
    useWorkspace.getState().pin('p')

    await useWorkspace.getState().setWorkspace('ws2')

    const s = useWorkspace.getState()
    expect(s.workspaceId).toBe('ws2')
    expect(s.focus).toEqual([])
    expect(s.selected).toEqual([])
    expect(s.pinned).toEqual([])
    expect(s.primary).toBeNull()
  })
})
