import { describe, expect, it } from 'vitest'
import {
  anchorForCos, Occupancy, overlaps, ray, textBox, type Box,
} from './labels'

const b = (x0: number, y0: number, x1: number, y1: number): Box => ({ x0, y0, x1, y1 })

describe('overlaps', () => {
  it('is true when the rectangles share area', () => {
    expect(overlaps(b(0, 0, 10, 10), b(5, 5, 15, 15))).toBe(true)
  })
  it('is false when they only touch', () => {
    // Touching edges must not count, or a label sitting exactly against a mark
    // is rejected and the densest part of the graph loses the most names.
    expect(overlaps(b(0, 0, 10, 10), b(10, 0, 20, 10))).toBe(false)
    expect(overlaps(b(0, 0, 10, 10), b(0, 10, 10, 20))).toBe(false)
  })
  it('is false when they are apart on either axis', () => {
    expect(overlaps(b(0, 0, 10, 10), b(11, 0, 20, 10))).toBe(false)
    expect(overlaps(b(0, 0, 10, 10), b(0, 11, 10, 20))).toBe(false)
  })
})

describe('textBox', () => {
  it('treats y as the vertical centre, not the baseline', () => {
    const box = textBox(100, 50, 40, 14, 'middle', 0)
    expect(box.y0).toBe(43)
    expect(box.y1).toBe(57)
  })
  it('grows the way the anchor points', () => {
    expect(textBox(100, 0, 40, 14, 'start', 0).x0).toBe(100)
    expect(textBox(100, 0, 40, 14, 'start', 0).x1).toBe(140)
    expect(textBox(100, 0, 40, 14, 'end', 0).x0).toBe(60)
    expect(textBox(100, 0, 40, 14, 'end', 0).x1).toBe(100)
    expect(textBox(100, 0, 40, 14, 'middle', 0).x0).toBe(80)
  })
})

describe('Occupancy', () => {
  it('rejects a label that lands on a mark', () => {
    const occ = new Occupancy()
    occ.mark(0, 0, 10)
    expect(occ.fits(textBox(0, 0, 20, 12))).toBe(false)
    expect(occ.fits(textBox(0, 40, 20, 12))).toBe(true)
  })

  it('reserves marks as squares, so a caller must not pre-pad them', () => {
    // The square around a round mark already over-reserves by 41% along the
    // diagonal. Padding r further is what starved Orbit's diagonal spokes.
    const occ = new Occupancy()
    occ.mark(0, 0, 10)
    // A point at 45° at distance 13 is outside the circle (r=10) but inside
    // the square, which is the whole hazard the comment warns about.
    const diag = 13 / Math.SQRT2
    expect(occ.fits(textBox(diag, diag, 1, 1, 'middle', 0))).toBe(false)
  })

  it('takes the first candidate that fits and reserves it', () => {
    const occ = new Occupancy()
    occ.mark(0, 0, 10)
    const taken = occ.firstFit([
      textBox(0, 0, 20, 12),    // on the mark
      textBox(0, 30, 20, 12),   // clear
      textBox(0, 60, 20, 12),
    ])
    expect(taken).toBe(1)
    // Reserved, so the next label cannot take the same slot.
    expect(occ.fits(textBox(0, 30, 20, 12))).toBe(false)
  })

  it('reports -1 when nothing fits and reserves nothing', () => {
    const occ = new Occupancy()
    occ.mark(0, 0, 100)
    const before = occ.boxes.length
    expect(occ.firstFit([textBox(0, 0, 10, 10), textBox(5, 5, 10, 10)])).toBe(-1)
    expect(occ.boxes.length).toBe(before)
  })

  it('places a full ring of names without a single overlap', () => {
    // The regression this whole module exists for. 26 names on a ring, the
    // shape Orbit actually draws, with the pole crowding that used to drop
    // half of them.
    const occ = new Occupancy()
    const CX = 500, CY = 330, R = 185
    const angles = Array.from({ length: 26 }, (_, i) => (i / 26) * Math.PI * 2)
    for (const a of angles) occ.mark(CX + Math.cos(a) * R, CY + Math.sin(a) * R, 16)

    const placed: Box[] = []
    for (const a of angles) {
      const anchor = anchorForCos(Math.cos(a))
      const pts = ray(CX, CY, a, R + 31, [0, 21, 42, 63])
      const cands = pts.map((p) => textBox(p.x, p.y, 96, 15.5, anchor))
      const i = occ.firstFit(cands)
      if (i >= 0) placed.push(cands[i])
    }

    // Every name found a slot...
    expect(placed).toHaveLength(26)
    // ...and none of them touch.
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        expect(overlaps(placed[i], placed[j])).toBe(false)
      }
    }
  })
})

describe('ray', () => {
  it('walks outward along the angle', () => {
    const [a, c] = ray(0, 0, 0, 10, [0, 5])
    expect(a.x).toBeCloseTo(10)
    expect(a.y).toBeCloseTo(0)
    expect(c.x).toBeCloseTo(15)
  })
})

describe('anchorForCos', () => {
  it('anchors by side, and centres near the poles', () => {
    expect(anchorForCos(1)).toBe('start')
    expect(anchorForCos(-1)).toBe('end')
    expect(anchorForCos(0)).toBe('middle')
    expect(anchorForCos(0.1)).toBe('middle')
    expect(anchorForCos(0.5)).toBe('start')
  })
})
