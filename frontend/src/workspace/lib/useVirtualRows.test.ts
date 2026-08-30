// Windowing maths. This hook replaced a `view.slice(0, 400)` that silently
// dropped rows, so the property that actually matters is that EVERY row stays
// reachable — the window plus its two spacers must always account for the
// whole list. A regression here does not throw; it quietly hides rows again,
// which is exactly the failure that motivated the hook.

import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useVirtualRows } from './useVirtualRows'

const ROW = 36

/** A stand-in for `.omx-scroll` and the `<tbody>` inside it. jsdom gives every
 *  element a zero rect, so the geometry both elements report is faked: the
 *  container is a fixed viewport at y=0, and the body's top moves up as the
 *  list scrolls. */
function harness({ viewport = 720, total = 1000 } = {}) {
  let scrollTop = 0
  const listeners: Array<() => void> = []

  const scroll = {
    getBoundingClientRect: () => ({ top: 0, height: viewport }),
    addEventListener: (_: string, fn: () => void) => { listeners.push(fn) },
    removeEventListener: () => {},
    scrollTo: ({ top }: { top: number }) => { scrollTop = top; listeners.forEach((f) => f()) },
  } as unknown as HTMLDivElement

  const body = {
    getBoundingClientRect: () => ({ top: -scrollTop, height: total * ROW }),
  } as unknown as HTMLElement

  return {
    scroll,
    body,
    scrollTo(px: number) { scrollTop = px; listeners.forEach((f) => f()) },
    get scrollTop() { return scrollTop },
  }
}

beforeEach(() => {
  // The hook observes its scroll container; jsdom has no ResizeObserver.
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
})

/** Mount the hook and attach it to a fake container. */
function mount(total: number, h = harness({ total })) {
  const view = renderHook(() => useVirtualRows(total, ROW))
  act(() => {
    view.result.current.scrollRef(h.scroll)
    view.result.current.bodyRef(h.body)
  })
  return { view, h }
}

describe('useVirtualRows', () => {
  it('renders a window instead of the whole list', () => {
    const { view } = mount(1000)
    const w = view.result.current.window
    expect(w.start).toBe(0)
    // A 720px viewport of 36px rows is 20, plus overscan on the trailing edge.
    expect(w.end).toBeGreaterThan(19)
    expect(w.end).toBeLessThan(60)
  })

  it('accounts for every row, at every scroll position', () => {
    const { view, h } = mount(1000)
    for (const px of [0, 360, 5_000, 17_999, 36_000 - 720]) {
      act(() => { h.scrollTo(px) })
      const w = view.result.current.window
      const rendered = w.end - w.start
      // padTop + rendered rows + padBottom === the full list. This is the
      // invariant the old `.slice(0, 400)` broke.
      expect(w.padTop / ROW + rendered + w.padBottom / ROW).toBe(1000)
      expect(w.start).toBeGreaterThanOrEqual(0)
      expect(w.end).toBeLessThanOrEqual(1000)
    }
  })

  it('keeps the rows under the viewport inside the window', () => {
    const { view, h } = mount(1000)
    act(() => { h.scrollTo(9_000) })  // row 250 is at the top edge
    const w = view.result.current.window
    expect(w.start).toBeLessThanOrEqual(250)
    expect(w.end).toBeGreaterThanOrEqual(250 + Math.ceil(720 / ROW))
  })

  it('never windows past the end of a short list', () => {
    const { view } = mount(5)
    const w = view.result.current.window
    expect(w.start).toBe(0)
    expect(w.end).toBe(5)
    expect(w.padTop).toBe(0)
    expect(w.padBottom).toBe(0)
  })

  it('scrollToTop returns to the first row', () => {
    const { view, h } = mount(1000)
    act(() => { h.scrollTo(9_000) })
    expect(view.result.current.window.start).toBeGreaterThan(0)
    act(() => { view.result.current.scrollToTop() })
    expect(h.scrollTop).toBe(0)
    expect(view.result.current.window.start).toBe(0)
  })

  it('attaches when the element appears after the first render', () => {
    // Callers render a skeleton first, so the scroller does not exist on the
    // mount pass. A `useRef` here left the listener unattached and the window
    // frozen at row 0 forever; the callback ref is what fixes it.
    const h = harness({ total: 1000 })
    const view = renderHook(() => useVirtualRows(1000, ROW))
    act(() => { h.scrollTo(9_000) })            // scrolled while still detached
    act(() => {
      view.result.current.scrollRef(h.scroll)
      view.result.current.bodyRef(h.body)
    })
    expect(view.result.current.window.start).toBeGreaterThan(200)
  })
})
