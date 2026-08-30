// Fixed-height row windowing, for the lists that outgrew a `.slice()`.
//
// The Table view rendered `view.slice(0, 400)` under a counter that read
// "612 of 644". It said 612 and drew 400, silently, and the rows it dropped
// were whichever the current sort pushed past 400 — so sorting by Confidence
// to find the least-certain objects hid exactly the ones being looked for.
// A truncation the user cannot see is worse than a slow table.
//
// Windowing keeps every row addressable and pays only for the ones on screen.
//
// ROW HEIGHT IS FIXED and enforced in CSS (`.omx-table tbody tr` has an
// explicit height, and its cells do not wrap). Variable heights would need
// per-row measurement; nothing here needs that yet, and an estimated average
// is how virtual lists end up drifting under the scrollbar mid-scroll.

import { useCallback, useEffect, useState } from 'react'

export interface RowWindow {
  /** First row to render. */
  start: number
  /** One past the last row to render. */
  end: number
  /** Spacer height standing in for rows before `start`. */
  padTop: number
  /** Spacer height standing in for rows after `end`. */
  padBottom: number
}

/**
 * @param total      how many rows exist after filtering
 * @param rowHeight  the exact rendered height of one row, in px
 * @param overscan   rows drawn beyond each edge, so a fast flick does not
 *                   expose blank space before the next frame
 */
export function useVirtualRows(total: number, rowHeight: number, overscan = 8) {
  // Callback refs, not `useRef`, and this is not a style preference. Callers
  // render a skeleton while their fetch is in flight, so on the mount pass
  // there is no table and a `useRef` would still be null when the effect ran —
  // the listener was never attached, the window never moved, and the table
  // silently showed only its first screen no matter how far you scrolled.
  // A callback ref re-runs the effect at the moment the element appears.
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null)
  const [bodyEl, setBodyEl] = useState<HTMLElement | null>(null)
  const scrollRef = useCallback((el: HTMLDivElement | null) => { setScrollEl(el) }, [])
  const bodyRef = useCallback((el: HTMLElement | null) => { setBodyEl(el) }, [])

  const [metrics, setMetrics] = useState({ above: 0, viewport: 0 })

  const measure = useCallback(() => {
    const sc = scrollEl
    const body = bodyEl
    if (!sc || !body) return
    const s = sc.getBoundingClientRect()
    const b = body.getBoundingClientRect()
    // Rows already past the top edge. Measured from rects rather than
    // `offsetTop` so it does not depend on which ancestor is positioned.
    const above = Math.max(0, s.top - b.top)
    // Only commit a real change. Writing unconditionally here would let the
    // ResizeObserver below feed itself, which is the failure that once pegged
    // the main thread in the old TERRA bundle and looked like a hang.
    setMetrics((p) =>
      p.above === above && p.viewport === s.height ? p : { above, viewport: s.height })
  }, [scrollEl, bodyEl])

  useEffect(() => {
    if (!scrollEl) return
    measure()
    scrollEl.addEventListener('scroll', measure, { passive: true })
    // Observes the scroll container, whose height is set by the layout — not
    // the rows region, which this hook writes to.
    const ro = new ResizeObserver(measure)
    ro.observe(scrollEl)
    return () => {
      scrollEl.removeEventListener('scroll', measure)
      ro.disconnect()
    }
  }, [scrollEl, measure])

  // A viewport of 0 means "not measured yet" (first paint, or a hidden tab).
  // Rendering one row there would flash a nearly-empty table before the effect
  // runs, so fall back to a screenful.
  const viewport = metrics.viewport || 720
  const start = Math.max(0, Math.floor(metrics.above / rowHeight) - overscan)
  const end = Math.min(total, start + Math.ceil(viewport / rowHeight) + overscan * 2)

  const window_: RowWindow = {
    start,
    end,
    padTop: start * rowHeight,
    padBottom: Math.max(0, total - end) * rowHeight,
  }

  /** Jump back to the top — call when a sort or filter changes, so the reader
   *  is not left parked at row 300 of a list that is now about something else. */
  const scrollToTop = useCallback(() => {
    scrollEl?.scrollTo({ top: 0 })
  }, [scrollEl])

  return { scrollRef, bodyRef, window: window_, scrollToTop }
}
