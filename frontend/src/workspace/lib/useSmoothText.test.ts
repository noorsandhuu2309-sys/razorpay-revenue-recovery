import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { safeMarkdownPrefix, useSmoothText } from './useSmoothText'

// These are the cases that caused visible flicker before the guard existed:
// each one renders as literal characters for a frame or two and then snaps into
// its real form once the closing delimiter arrives.
describe('safeMarkdownPrefix', () => {
  it('passes complete text through untouched', () => {
    const s = 'A **bold** word and `code` and [a link](https://x.com).'
    expect(safeMarkdownPrefix(s)).toBe(s)
  })

  it('withholds a half-typed bold marker', () => {
    expect(safeMarkdownPrefix('The answer is **')).toBe('The answer is ')
    expect(safeMarkdownPrefix('The answer is *')).toBe('The answer is ')
  })

  it('withholds an unclosed inline code span', () => {
    expect(safeMarkdownPrefix('Call `foo')).toBe('Call ')
  })

  it('keeps a closed inline code span', () => {
    expect(safeMarkdownPrefix('Call `foo()` now')).toBe('Call `foo()` now')
  })

  it('withholds an unclosed link', () => {
    expect(safeMarkdownPrefix('See [the docs')).toBe('See ')
  })

  it('streams the inside of an open code fence verbatim', () => {
    // The content of an unterminated fence is already plain text inside a
    // <pre>, so holding it back would freeze the most satisfying thing to
    // watch and buys nothing.
    const s = '```python\ndef f():\n    return **not bold'
    expect(safeMarkdownPrefix(s)).toBe(s)
  })

  it('passes text after a fence has closed', () => {
    const s = '```js\nlet a = 1\n```\nDone.'
    expect(safeMarkdownPrefix(s)).toBe(s)
  })

  it('handles empty input', () => {
    expect(safeMarkdownPrefix('')).toBe('')
  })

  it('never returns more than it was given', () => {
    for (const s of ['**', '`', '[', 'a **b', '```', 'x](']) {
      expect(safeMarkdownPrefix(s).length).toBeLessThanOrEqual(s.length)
    }
  })

  it('is a prefix of the input in every case', () => {
    const cases = ['hello **wor', 'a `b', 'see [x', '**done**', '```\nraw']
    for (const s of cases) {
      expect(s.startsWith(safeMarkdownPrefix(s))).toBe(true)
    }
  })

  it('withholds a table row until its newline lands', () => {
    // A row arriving cell by cell rebuilds the table — and recomputes every
    // column width — on each new pipe, so the block jumps sideways repeatedly.
    const done = '| a | b |\n| - | - |\n'
    expect(safeMarkdownPrefix(done + '| 1 | 2')).toBe(done)
    expect(safeMarkdownPrefix(done + '| 1 | 2 |\n')).toBe(done + '| 1 | 2 |\n')
  })
})

describe('useSmoothText continuity', () => {
  let now = 0
  let frames: FrameRequestCallback[] = []

  beforeEach(() => {
    now = 0
    frames = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(cb)
      return frames.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    vi.spyOn(performance, 'now').mockImplementation(() => now)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  /** Advance the clock and run every frame currently queued. */
  const advance = (ms: number) => {
    now += ms
    const due = frames
    frames = []
    act(() => { for (const f of due) f(now) })
  }

  it('keeps revealing while deltas arrive faster than the frame budget', () => {
    // THE BUG. The drain loop was a dependency of an effect keyed on the text,
    // so every delta tore it down and rebuilt it, resetting the frame clock.
    // Deltas land far closer together than the 30ms frame budget, so the
    // frame-skip test was true every time and nothing was ever painted while
    // text was flowing — the answer only advanced during gaps in the stream.
    let text = ''
    const { result, rerender } = renderHook(
      ({ full }) => useSmoothText(full, true), { initialProps: { full: '' } })

    // A delta every 10ms for 300ms, which is how a healthy stream behaves.
    for (let i = 0; i < 30; i++) {
      text += '0123456789'
      rerender({ full: text })
      advance(10)
    }

    expect(result.current.length).toBeGreaterThan(0)
  })

  it('never reveals more than has arrived', () => {
    const { result, rerender } = renderHook(
      ({ full }) => useSmoothText(full, true), { initialProps: { full: '' } })
    let text = ''
    for (let i = 0; i < 10; i++) {
      text += 'abcdefghij'
      rerender({ full: text })
      advance(50)
      expect(text.startsWith(result.current)).toBe(true)
    }
  })

  it('settles on the whole answer once the stream closes', () => {
    const full = 'x'.repeat(400)
    const { result, rerender } = renderHook(
      ({ full: f, streaming }) => useSmoothText(f, streaming),
      { initialProps: { full, streaming: true } })
    advance(50)
    rerender({ full, streaming: false })
    for (let i = 0; i < 40; i++) advance(50)
    expect(result.current).toBe(full)
  })
})
