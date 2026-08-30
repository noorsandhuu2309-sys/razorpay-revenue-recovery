// Label placement: the rule that names never cover marks, and never cover
// each other.
//
// Both graph modes need it and both had grown their own copy, which is how they
// ended up disagreeing about it: the network canvas tested labels against other
// labels only, so a name could clear every other name and then land squarely on
// an unrelated node; Orbit tested nothing at all and drew its names inside the
// node groups, so a node emitted later painted over an earlier node's name.
// Two different bugs, one missing primitive.
//
// The model is deliberately dumb — axis-aligned rectangles, linear scan, no
// spatial index. A frame places at most a few dozen labels against a few
// hundred marks; a quadtree would cost more to maintain than the scan costs to
// run, and this has to be re-derived from scratch every frame because the
// layout is still moving.

export interface Box { x0: number; y0: number; x1: number; y1: number }

export type Anchor = 'start' | 'middle' | 'end'

/** Two rectangles share at least one pixel. Touching edges do not count. */
export const overlaps = (a: Box, b: Box): boolean =>
  !(a.x1 <= b.x0 || a.x0 >= b.x1 || a.y1 <= b.y0 || a.y0 >= b.y1)

/** The rectangle a piece of text will occupy.
 *
 *  `y` is the text's VERTICAL CENTRE, not its baseline: callers are placing
 *  against round marks and think in centres, and every baseline conversion in
 *  the two call sites was a chance to be off by half a line. */
export function textBox(
  x: number, y: number, width: number, height: number,
  anchor: Anchor = 'middle', pad = 3,
): Box {
  const x0 = anchor === 'start' ? x : anchor === 'end' ? x - width : x - width / 2
  return { x0: x0 - pad, y0: y - height / 2, x1: x0 + width + pad, y1: y + height / 2 }
}

/** What is already taken.
 *
 *  Marks go in FIRST and labels are fitted into the gaps between them. That
 *  ordering is the whole point: seeding the marks is what stops a name landing
 *  on a dot that is not its own, and it is the single change that did most to
 *  make the graph readable. */
export class Occupancy {
  readonly boxes: Box[] = []

  /** Reserve a mark. `r` is a half-extent, and the reservation is the SQUARE
   *  around it — which already over-reserves a round mark by 41% along each
   *  diagonal, so callers should pass the true radius and resist padding it.
   *  Padding it is what starved Orbit's diagonal spokes of labels. */
  mark(x: number, y: number, r: number): void {
    this.boxes.push({ x0: x - r, y0: y - r, x1: x + r, y1: y + r })
  }

  add(box: Box): void { this.boxes.push(box) }

  fits(box: Box): boolean {
    for (const o of this.boxes) if (overlaps(box, o)) return false
    return true
  }

  /** Try each candidate in order and take the first that fits, reserving it.
   *  Returns the index taken, or -1 if none did. */
  firstFit(candidates: Box[]): number {
    for (let i = 0; i < candidates.length; i++) {
      if (this.fits(candidates[i])) { this.add(candidates[i]); return i }
    }
    return -1
  }
}

/** Points along a ray from (cx, cy), one per step.
 *
 *  A single candidate per name is not enough on a radial layout. Near the poles
 *  the spokes are vertical, so adjacent names sit side by side about 45px apart
 *  while being up to 110px wide: they always collide, and a one-shot pass
 *  silently drops the entire top and bottom of the ring. Sliding a name further
 *  out along its OWN spoke keeps it unambiguously attached to its node while
 *  letting neighbours interleave at different radii, which is how a radial
 *  layout is meant to breathe. */
export function ray(
  cx: number, cy: number, angle: number, from: number, steps: readonly number[],
): { x: number; y: number }[] {
  const cos = Math.cos(angle), sin = Math.sin(angle)
  return steps.map((s) => ({ x: cx + cos * (from + s), y: cy + sin * (from + s) }))
}

/** Anchor a radial label by which side of the dial its spoke points at.
 *
 *  Near the poles the spoke is vertical and a side anchor throws the name onto
 *  one shoulder for no reason, so those are centred over the node instead. */
export const anchorForCos = (cos: number, deadzone = 0.22): Anchor =>
  Math.abs(cos) < deadzone ? 'middle' : cos > 0 ? 'start' : 'end'
