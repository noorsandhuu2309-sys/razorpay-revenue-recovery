// The graph's visual rules, asserted.
//
// Each of these encodes a claim the canvas makes to the reader, and each one
// was wrong at some point in this codebase:
//
//   * direction read off `source`/`target` gave arrows that contradicted their
//     own labels, because those fields are traversal order and the stored
//     direction lives in the label's "(inbound)" suffix.
//   * every node was sized identically, because the API reports `degree: null`
//     on subgraph nodes and nothing recomputed it.
//   * the ontology's palette read as neon against #060606.

import { describe, expect, it } from 'vitest'
import {
  buildModel, certaintyOf, describeEdge, focusTiers, readLabel, relClass,
  restrain, shapeOf,
} from './graphModel'
import type { GraphPayload, OmxEdge, OmxObject } from './types'

const obj = (id: string, over: Partial<OmxObject> = {}): OmxObject => ({
  id, type: 'country', typeLabel: 'Country', family: 'country',
  familyLabel: 'Countries', domain: 'external', name: id.toUpperCase(),
  description: '', externalId: '', properties: {}, tags: [],
  provenance: 'verified', provenanceLabel: 'Verified', confidence: null,
  salience: 0.5, tracked: false, lat: null, lon: null, geo: false,
  glyph: '◆', color: '#c9a45c', shape: 'square', vweight: 1, ring: false,
  degree: null, executionId: null, firstSeen: '', lastSeen: '', ...over,
})

const edge = (source: string, target: string, over: Partial<OmxEdge> = {}): OmxEdge => ({
  id: `${source}-${target}`, source, target, relation: 'sanctions',
  label: 'sanctions', weight: 2, count: 3, sentiment: 0,
  provenance: 'source_backed', ...over,
})

const payload = (nodes: OmxObject[], edges: OmxEdge[]): GraphPayload => ({
  workspace: 'ws', roots: [], nodes, edges, communities: [],
  stats: { nodes: nodes.length, edges: edges.length, byType: {} },
})

describe('edge direction', () => {
  // `source`/`target` are the BFS walk order — the engine emits `source: nid`
  // for whichever node it walked FROM. The stored direction is in the label.
  it('reads direction from the label, not from source/target', () => {
    const out = describeEdge(edge('a', 'b', { label: 'sanctions' }), new Set())
    expect(out.inbound).toBe(false)

    const inbound = describeEdge(
      edge('a', 'b', { label: 'sanctions (inbound)' }), new Set())
    expect(inbound.inbound).toBe(true)
    // and the bookkeeping never reaches the reader
    expect(inbound.label).toBe('sanctions')
  })

  it('points the arrow the other way when the edge is inbound', () => {
    const g = payload(
      [obj('a'), obj('b')],
      [edge('a', 'b', { label: 'sanctions (inbound)' })])
    const [e] = buildModel(g, new Set()).edges
    // a is index 0, b is index 1. Inbound means b sanctions a.
    expect([e.from, e.to]).toEqual([1, 0])
  })

  it('marks symmetric relations so no arrow is drawn', () => {
    const g = payload(
      [obj('a'), obj('b')],
      [edge('a', 'b', { relation: 'allied_with', label: 'allied with' })])
    const [e] = buildModel(g, new Set(['allied_with'])).edges
    expect(e.symmetric).toBe(true)
  })

  it('strips the suffix from a bare relation key with no label', () => {
    expect(readLabel({ label: '', relation: 'in_conflict' }).label)
      .toBe('in conflict')
  })
})

describe('certainty', () => {
  it('treats co-mention and related-to as incidental however heavy', () => {
    expect(certaintyOf({
      relation: 'co_mentioned', provenance: 'verified', count: 40, weight: 9,
    })).toBe('weak')
  })

  it('calls a single source-backed observation inferred, two corroborated', () => {
    const base = { relation: 'sanctions', provenance: 'source_backed' as const, weight: 2 }
    expect(certaintyOf({ ...base, count: 1 })).toBe('inferred')
    expect(certaintyOf({ ...base, count: 2 })).toBe('confirmed')
  })

  it('never calls a model extraction confirmed', () => {
    expect(certaintyOf({
      relation: 'sanctions', provenance: 'ai_inferred', count: 9, weight: 8,
    })).toBe('inferred')
  })
})

describe('semantic class', () => {
  // Sanctions is coercive. Reading it as neutral alignment is the single most
  // misleading thing this map could do.
  it('classes sanctions as adversarial, not strategic', () => {
    expect(relClass('sanctions')).toBe('adversarial')
    expect(relClass('allied_with')).toBe('strategic')
    expect(relClass('trades_with')).toBe('economic')
  })

  it('falls back to informational for an unknown relation', () => {
    expect(relClass('some_future_relation')).toBe('informational')
  })
})

describe('shape', () => {
  it('distinguishes company from organization, claim from source', () => {
    expect(shapeOf({ type: 'country', family: 'country' })).toBe('diamond')
    expect(shapeOf({ type: 'company', family: 'company' })).toBe('square')
    expect(shapeOf({ type: 'organization', family: 'org' })).toBe('circle')
    expect(shapeOf({ type: 'claim', family: 'evidence' })).toBe('diamond')
    expect(shapeOf({ type: 'source', family: 'evidence' })).toBe('square')
    expect(shapeOf({ type: 'location', family: 'place' })).toBe('hexagon')
  })

  it('falls back to the family, then to a circle', () => {
    expect(shapeOf({ type: 'unknown_type', family: 'conflict' })).toBe('triangle')
    expect(shapeOf({ type: 'unknown_type', family: 'unknown_family' })).toBe('circle')
  })
})

describe('restrained palette', () => {
  const hsl = (hex: string) => {
    const n = parseInt(hex.slice(1), 16)
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255
    const max = Math.max(r, g, b), min = Math.min(r, g, b)
    const l = (max + min) / 2
    const s = max === min ? 0
      : l > 0.5 ? (max - min) / (2 - max - min) : (max - min) / (max + min)
    return { s, l }
  }

  it('pulls the ontology neon into range', () => {
    // The four that read as highlighter against the dark ground.
    for (const c of ['#4ade80', '#c084fc', '#f0abfc', '#ff3355']) {
      const { s, l } = hsl(restrain(c))
      expect(s).toBeLessThanOrEqual(0.42)
      expect(l).toBeGreaterThanOrEqual(0.4)
      expect(l).toBeLessThanOrEqual(0.64)
    }
  })

  it('keeps hues apart, so colour still identifies type', () => {
    expect(restrain('#57d7ff')).not.toBe(restrain('#c9a45c'))
    expect(restrain('#4ade80')).not.toBe(restrain('#ff9a62'))
  })
})

describe('node hierarchy', () => {
  // The API reports `degree: null` on every subgraph node, so nothing derived
  // from it sized anything. This is where hierarchy actually comes from.
  it('computes degree from the loaded subgraph', () => {
    const g = payload(
      [obj('hub'), obj('a'), obj('b'), obj('c')],
      [edge('hub', 'a'), edge('hub', 'b'), edge('hub', 'c')])
    const m = buildModel(g, new Set())
    expect(m.byId.get('hub')!.degree).toBe(3)
    expect(m.byId.get('a')!.degree).toBe(1)
  })

  it('sizes a hub above a leaf, but within a bounded range', () => {
    const g = payload(
      [obj('hub'), ...Array.from({ length: 30 }, (_, i) => obj(`n${i}`))],
      Array.from({ length: 30 }, (_, i) => edge('hub', `n${i}`, { id: `e${i}` })))
    const m = buildModel(g, new Set())
    const hub = m.byId.get('hub')!
    const leaf = m.byId.get('n0')!
    expect(hub.size).toBeGreaterThan(leaf.size)
    // Bounded on BOTH ends: hierarchy, not one hub that swallows the map.
    expect(leaf.size).toBeGreaterThanOrEqual(4)
    expect(hub.size).toBeLessThanOrEqual(14.5)
    expect(hub.size / leaf.size).toBeLessThan(3)
  })

  it('drops edges whose endpoints the node cap excluded', () => {
    // cosmos.gl indexes its buffers by position; an edge pointing past the end
    // of the node array is a GPU read out of bounds, not a missing line.
    const g = payload([obj('a')], [edge('a', 'ghost')])
    expect(buildModel(g, new Set()).edges).toHaveLength(0)
  })

  it('ranks labels by importance so the survivors of a collision are the ones worth keeping', () => {
    const g = payload(
      [obj('quiet'), obj('hub'), obj('mid')],
      [edge('hub', 'quiet'), edge('hub', 'mid', { id: 'e2' })])
    const m = buildModel(g, new Set())
    expect(m.labelOrder[0].id).toBe('hub')
  })
})

describe('focus tiers', () => {
  //   0  the anchor and everything it touches
  //   1  second degree
  //   2  the rest, which drops to near-invisible
  it('tiers by distance from the anchor', () => {
    const g = payload(
      [obj('a'), obj('b'), obj('c'), obj('far')],
      [edge('a', 'b'), edge('b', 'c', { id: 'e2' })])
    const tiers = focusTiers(buildModel(g, new Set()), ['a'])
    expect(tiers.get('a')).toBe(0)
    expect(tiers.get('b')).toBe(0)   // directly connected reads as fully present
    expect(tiers.get('c')).toBe(1)   // second degree
    expect(tiers.get('far')).toBe(2) // unrelated
  })

  it('returns an empty tiering with no anchors, so nothing is dimmed', () => {
    const g = payload([obj('a')], [])
    expect(focusTiers(buildModel(g, new Set()), []).size).toBe(0)
  })
})
