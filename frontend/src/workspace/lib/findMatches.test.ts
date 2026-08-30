import { describe, expect, it } from 'vitest'
import { findMatches } from './findMatches'
import type { GraphModel } from './graphModel'

/** Just enough model for the matcher: it only reads `nodes[].id` and
 *  `nodes[].o.name`. */
const modelOf = (...names: string[]) => ({
  nodes: names.map((name, i) => ({ id: `n${i}`, o: { name } })),
} as unknown as GraphModel)

describe('findMatches', () => {
  it('matches on a case-insensitive substring', () => {
    const m = findMatches(modelOf('Gazprom', 'Gaza', 'Rosneft'), 'GAZ')
    expect(m.map((h) => h.name).sort()).toEqual(['Gaza', 'Gazprom'])
  })

  it('returns nothing for an empty query, rather than everything', () => {
    // The canvas dims non-matches, so "every node matches" and "no query" have
    // to be distinguishable or an empty box would grey out the whole graph.
    expect(findMatches(modelOf('Iran', 'Iraq'), '')).toEqual([])
    expect(findMatches(modelOf('Iran', 'Iraq'), '   ')).toEqual([])
  })

  it('ranks a prefix above a mid-word hit', () => {
    // Typing "iran" should land on Iran, not on "Sanctions on Iran".
    const m = findMatches(modelOf('Sanctions on Iran', 'Iran'), 'iran')
    expect(m[0].name).toBe('Iran')
  })

  it('ranks the shorter name first when both are prefixes', () => {
    const m = findMatches(modelOf('Iran-Israel escalation', 'Iran'), 'iran')
    expect(m[0].name).toBe('Iran')
  })

  it('caps the list so the panel cannot become the graph', () => {
    const many = Array.from({ length: 40 }, (_, i) => `Bank ${i}`)
    expect(findMatches(modelOf(...many), 'bank')).toHaveLength(12)
    expect(findMatches(modelOf(...many), 'bank', 5)).toHaveLength(5)
  })

  it('finds nothing when nothing matches', () => {
    expect(findMatches(modelOf('Iran', 'Iraq'), 'zzz')).toEqual([])
  })
})
