import { describe, expect, it } from 'vitest'
import { countryCentroids } from './geodata'
import { greetingFor, severityLabel, severityVar, countryName } from './highlights'
import type { RawFeature } from './geodata'

// A square, as the compacted gazetteer stores one: polygon -> ring -> [lon, lat].
const square = (cx: number, cy: number, r: number): number[][][] => [[
  [cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r],
  [cx - r, cy - r],
]]

describe('countryCentroids', () => {
  it('places the point inside a simple country', () => {
    const f: RawFeature[] = [{ n: 'Squareland', i: 'sq', g: [square(10, 20, 5)] }]
    expect(countryCentroids(f)).toEqual({ SQ: [10, 20] })
  })

  it('uppercases the ISO code, because the feed asks for it in upper case', () => {
    const f: RawFeature[] = [{ i: 'us', g: [square(0, 0, 1)] }]
    expect(Object.keys(countryCentroids(f))).toEqual(['US'])
  })

  // The bug this guards is the reason the function does not use a bounding box:
  // a country whose territory includes a distant island would otherwise get a
  // centroid in the ocean between the two. The United States with Hawaii is the
  // real case; this is that shape, minimised.
  it('uses the largest landmass, not the bounding box of all of them', () => {
    const mainland = square(0, 40, 10)     // area 400
    const island = square(-150, 20, 1)     // area 4, far away
    const c = countryCentroids([{ i: 'US', g: [mainland, island] }])
    expect(c.US[0]).toBeCloseTo(0, 6)
    expect(c.US[1]).toBeCloseTo(40, 6)
  })

  // Coastlines are digitised far more densely than inland borders, so averaging
  // vertices drags the point towards the detailed edge. The shoelace centroid
  // does not care how many vertices a side is drawn with.
  it('is unmoved by how densely one edge is digitised', () => {
    const plain = square(0, 0, 10)
    const dense: number[][][] = [[
      [-10, -10],
      ...Array.from({ length: 50 }, (_, i) => [-10 + (i + 1) * (20 / 51), -10]),
      [10, -10], [10, 10], [-10, 10], [-10, -10],
    ]]
    const a = countryCentroids([{ i: 'AA', g: [plain] }]).AA
    const b = countryCentroids([{ i: 'BB', g: [dense] }]).BB
    expect(b[0]).toBeCloseTo(a[0], 6)
    expect(b[1]).toBeCloseTo(a[1], 6)
  })

  it('skips features with no geometry or no ISO code rather than emitting NaN', () => {
    const f: RawFeature[] = [
      { i: 'AA', g: [] },
      { i: '', g: [square(0, 0, 1)] },
      { i: 'BB', g: [[[[0, 0], [1, 1]]]] }, // two points: not a ring
      { i: 'CC', g: [square(3, 4, 1)] },
    ]
    expect(countryCentroids(f)).toEqual({ CC: [3, 4] })
  })
})

describe('severity', () => {
  it('bands the score, with the top band reserved for the worst', () => {
    expect(severityLabel(0.9)).toBe('critical')
    expect(severityLabel(0.6)).toBe('elevated')
    expect(severityLabel(0.4)).toBe('notable')
    expect(severityLabel(0.1)).toBe('routine')
  })

  it('treats a missing severity as routine rather than critical', () => {
    // TERRA omits `severity` on clusters it could not score. Defaulting the
    // other way would paint the feed red on a bad ingest.
    expect(severityLabel(undefined)).toBe('routine')
    expect(severityVar(undefined)).toBe('var(--omx-text-faint)')
  })

  // These are consumed only by map.css, never read back through
  // getComputedStyle by a canvas renderer, which is what makes a var()
  // reference safe here — and what keeps the pins correct in all ten
  // theme x accent combinations.
  it('returns token references, not frozen hex', () => {
    for (const s of [0, 0.4, 0.6, 0.9]) {
      expect(severityVar(s)).toMatch(/^var\(--omx-[a-z-]+\)$/)
    }
  })
})

describe('greetingFor', () => {
  const at = (h: number) => greetingFor(new Date(2026, 7, 13, h, 0, 0))

  it('follows the clock', () => {
    expect(at(2)).toBe('Still up')
    expect(at(9)).toBe('Good morning')
    expect(at(14)).toBe('Good afternoon')
    expect(at(19)).toBe('Good evening')
    expect(at(23)).toBe('Good evening')
  })

  it('greets at every hour of the day', () => {
    for (let h = 0; h < 24; h++) expect(at(h)).toBeTruthy()
  })
})

describe('countryName', () => {
  it('resolves an ISO code to a display name', () => {
    expect(countryName('CO')).toBe('Colombia')
    expect(countryName('co')).toBe('Colombia')
  })

  it('falls back to the code rather than throwing on nonsense', () => {
    expect(countryName('ZZZZ')).toBe('ZZZZ')
    expect(countryName('')).toBe('an unknown country')
  })
})
