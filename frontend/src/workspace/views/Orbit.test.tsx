// Orbit bounds its own rings, and it says when it has.
//
// The trimming exists because density can now return 160+ objects and a centre
// like the United States has ninety direct relationships: drawing all of them
// puts ninety overlapping shapes on one circle with their labels stacked.
//
// The first version of that trim aliased its own array — `let kept = ring1`,
// then `ring1.length = 0` — so on the common path, where nothing needed
// trimming, it emptied the ring it was meant to leave alone. Every centre with
// fewer than 26 neighbours rendered "No relationships at this depth", which is
// a confident and completely false statement about the data. These tests are
// here so that cannot come back quietly.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

vi.mock('../lib/api', () => ({
  api: {
    graph: vi.fn(async () => ({ nodes: [], edges: [] })),
    object: vi.fn(async (_w: string, id: string) => ({ id, name: id })),
    summary: vi.fn(async () => null),
    timeline: vi.fn(async () => ({ events: [] })),
    thread: vi.fn(async () => ({ turns: [] })),
  },
}))

import { Orbit, type OrbitHandle } from './Orbit'
import { useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import type { GraphPayload, OmxEdge, OmxObject } from '../lib/types'

const obj = (id: string): OmxObject => ({
  id, type: 'country', typeLabel: 'Country', family: 'country',
  familyLabel: 'Countries', domain: 'external', name: id.toUpperCase(),
  description: '', externalId: '', properties: {}, tags: [],
  provenance: 'verified', provenanceLabel: 'Verified', confidence: null,
  salience: 0.5, tracked: false, lat: null, lon: null, geo: false,
  glyph: '◆', color: '#c9a45c', shape: 'square', vweight: 1, ring: false,
  degree: null, executionId: null, firstSeen: '', lastSeen: '',
})

const edge = (source: string, target: string, weight = 2): OmxEdge => ({
  id: `${source}-${target}`, source, target, relation: 'sanctions',
  label: 'sanctions', weight, count: 3, sentiment: 0,
  provenance: 'source_backed',
})

/** A centre with `n` neighbours, weights descending so trimming is testable. */
function star(n: number): GraphPayload {
  const nodes = [obj('centre'), ...Array.from({ length: n }, (_, i) => obj(`n${i}`))]
  const edges = Array.from({ length: n }, (_, i) => edge('centre', `n${i}`, n - i))
  return {
    workspace: 'ws', roots: ['centre'], nodes, edges, communities: [],
    stats: { nodes: nodes.length, edges: edges.length, byType: {} },
  }
}

const renderOrbit = (g: GraphPayload) =>
  render(<Orbit graph={g} centreId="centre" onRecentre={() => {}} />)

beforeEach(() => {
  useWorkspace.setState({
    workspaceId: 'ws1', selected: [], pinned: [], primary: null,
    contextObjects: {}, provenanceFloor: 'ai_inferred', ontology: null,
  })
  useGraphUi.setState({ activeEdge: null })
})

describe('Orbit ring budget', () => {
  it('draws every neighbour when the ring is under budget', () => {
    renderOrbit(star(8))
    // 8 on the ring plus the centre. The regression this guards emptied the
    // ring entirely and left only the centre.
    expect(document.querySelectorAll('.omx-orbit-node')).toHaveLength(9)
    expect(screen.queryByText(/No relationships at this depth/i)).toBeNull()
  })

  it('does not claim a populated centre is unconnected', () => {
    renderOrbit(star(3))
    expect(screen.queryByText(/No relationships at this depth/i)).toBeNull()
    expect(screen.getByText('N0')).toBeTruthy()
  })

  it('trims an over-budget ring and says how much it left out', () => {
    renderOrbit(star(46))
    // The count in the notice is the honest part: a ring that silently showed
    // 26 of 46 would read as a complete answer.
    expect(screen.getByText(/Strongest 26 of 46 direct links/i)).toBeTruthy()
  })

  it('keeps the strongest links when it trims', () => {
    // Weights descend with index, so n0 is the strongest and n45 the weakest.
    renderOrbit(star(46))
    expect(screen.getByText('N0')).toBeTruthy()
    expect(screen.queryByText('N45')).toBeNull()
  })

  it('states the empty case rather than drawing a bare centre', () => {
    const g: GraphPayload = {
      workspace: 'ws', roots: ['centre'], nodes: [obj('centre')], edges: [],
      communities: [], stats: { nodes: 1, edges: 0, byType: {} },
    }
    renderOrbit(g)
    expect(screen.getByText(/No relationships at this depth/i)).toBeTruthy()
  })
})

// Orbit shipped as a fixed viewBox with no wheel or pointer handling at all —
// it read as frozen next to Network, and the toolbar's zoom buttons were
// hidden in this mode rather than wired to anything. The camera is a real one
// now, and these assert it moves.
describe('Orbit camera', () => {
  const viewBox = () =>
    document.querySelector('.omx-orbit-svg')!.getAttribute('viewBox')!

  const handle = () => {
    let h: OrbitHandle | null = null
    render(
      <Orbit graph={star(6)} centreId="centre" onRecentre={() => {}}
             onReady={(x) => { h = x ?? h }} />)
    return h!
  }

  it('starts framed on the whole dial', () => {
    renderOrbit(star(6))
    expect(viewBox()).toBe('0 0 1000 660')
  })

  it('zooms in and back out through the toolbar contract', () => {
    const h = handle()
    const width = () => Number(viewBox().split(' ')[2])
    const base = width()

    act(() => h.zoomBy(1.35))
    expect(width()).toBeLessThan(base)

    act(() => h.zoomBy(1 / 1.35))
    expect(width()).toBeCloseTo(base, 3)
  })

  it('clamps zoom so the dial can be neither a speck nor a single node', () => {
    const h = handle()
    const width = () => Number(viewBox().split(' ')[2])

    act(() => { for (let i = 0; i < 40; i++) h.zoomBy(2) })
    expect(width()).toBeGreaterThanOrEqual(1000 / 8)

    act(() => { for (let i = 0; i < 60; i++) h.zoomBy(0.5) })
    expect(width()).toBeLessThanOrEqual(1000 / 0.35)
  })

  it('fit returns to the base framing after a zoom', () => {
    const h = handle()
    act(() => h.zoomBy(3))
    expect(viewBox()).not.toBe('0 0 1000 660')
    act(() => h.fit())
    expect(viewBox()).toBe('0 0 1000 660')
  })

  it('reframes the whole dial rather than maximum-zooming on a lone point', () => {
    // One point has no extent; framing it drove the network canvas to maximum
    // zoom on an empty field, and the same trap is here.
    const h = handle()
    act(() => h.zoomBy(4))
    act(() => h.fitTo(['n0']))
    expect(viewBox()).toBe('0 0 1000 660')
  })

  it('pans on drag', () => {
    renderOrbit(star(6))
    const svg = document.querySelector('.omx-orbit-svg')!
    const before = viewBox()
    const send = (type: string, x: number, y: number) =>
      act(() => {
        svg.dispatchEvent(new PointerEvent(type, {
          clientX: x, clientY: y, pointerId: 1, button: 0, buttons: 1,
          bubbles: true, cancelable: true,
        }))
      })
    send('pointerdown', 400, 300)
    send('pointermove', 260, 220)
    send('pointerup', 260, 220)
    expect(viewBox()).not.toBe(before)
  })

  it('ignores a drag under the threshold, so a click is still a click', () => {
    renderOrbit(star(6))
    const svg = document.querySelector('.omx-orbit-svg')!
    const before = viewBox()
    const send = (type: string, x: number, y: number) =>
      act(() => {
        svg.dispatchEvent(new PointerEvent(type, {
          clientX: x, clientY: y, pointerId: 1, button: 0, buttons: 1,
          bubbles: true, cancelable: true,
        }))
      })
    send('pointerdown', 400, 300)
    send('pointermove', 402, 301)
    send('pointerup', 402, 301)
    expect(viewBox()).toBe(before)
  })
})
