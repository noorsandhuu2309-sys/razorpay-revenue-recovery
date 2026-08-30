// Smooth text reveal: decouples how fast tokens ARRIVE from how fast they are
// PAINTED.
//
// A raw SSE stream does not arrive smoothly. Tokens land in bursts — a dozen in
// one 16ms frame, then nothing for 300ms while the provider batches, then
// another burst. Rendering each delta the moment it arrives faithfully
// reproduces that jitter, and the result reads as a stuttering, cheap-feeling
// stream even when the underlying model is fast. It is the single biggest gap
// between "an open model on a free tier" and "a frontier product", and none of
// it is the model's fault.
//
// So the arriving text goes into a buffer and a requestAnimationFrame loop
// drains it at a controlled rate. The reader sees a steady flow of characters.
//
// The rate is ADAPTIVE, which is the part that makes it feel right rather than
// merely even:
//
//   * Rate is derived from how much is waiting. A deep buffer drains faster, so
//     the display never falls far behind the truth; a shallow one drains slower,
//     so it does not catch up and then sit frozen waiting for the next burst.
//   * There is a floor and a ceiling. The floor stops it stalling visibly
//     mid-sentence; the ceiling stops a big burst from flashing in all at once,
//     which is the jitter we are removing.
//   * Once the stream is closed, the remaining buffer is drained hard. Nobody
//     wants to watch an animation replay text the machine already has, and a
//     finished answer must never be gated behind an animation — that would turn
//     a cosmetic effect into a real delay.
//
// Reduced-motion users get the text immediately, with no animation at all.

import { useCallback, useEffect, useRef, useState } from 'react'

/** Characters per second at the slowest. Below roughly this, the eye reads it
 *  as the machine having stopped rather than as typing. */
const MIN_CPS = 220
/** ...and at the fastest, while the stream is still open. Fast enough to keep
 *  up with anything the tier produces, slow enough to stay legible. */
const MAX_CPS = 1400
/** Aim to clear whatever is buffered in about this long. Short enough that the
 *  display tracks the truth closely; long enough to absorb a burst. */
const DRAIN_SECONDS = 0.35
/** After the stream closes, finish within about this long regardless. */
const FLUSH_SECONDS = 0.28
/** Minimum gap between repaints, in ms — i.e. cap the reveal at ~33fps.
 *
 *  Every repaint re-parses the whole answer and reconciles it, which on a
 *  3,000-word reply is a few milliseconds. Doing that on all 60 frames spends
 *  a fifth of the main thread on an animation the eye cannot resolve past
 *  about 30fps anyway. Halving the rate is free smoothness: the CHARACTERS
 *  still advance at the same speed because each tick simply reveals twice as
 *  many, and the text is flowing continuously either way. */
const MIN_FRAME_MS = 30

const prefersReducedMotion = () =>
  typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/**
 * Progressively reveal `full`.
 *
 * @param full      the complete text received so far (grows as the stream runs)
 * @param streaming whether more text is still expected
 * @returns the substring to paint right now
 */
export function useSmoothText(full: string, streaming: boolean): string {
  const [shown, setShown] = useState(() => (streaming ? '' : full))

  // Held in refs, not state: the animation loop reads them every frame and must
  // not be a reason to re-render.
  const target = useRef(full)
  const count = useRef(streaming ? 0 : full.length)
  const raf = useRef(0)
  const last = useRef(0)
  // Whether a drain loop is already in flight. THIS is what makes the reveal
  // continuous.
  //
  // The loop used to be torn down and rebuilt by the effect on every `full`
  // change — i.e. on every delta — and each rebuild reset `last` to "now".
  // Deltas arrive far closer together than MIN_FRAME_MS, so the frame-skip test
  // (`now - last < 30ms`) was true every single time and the reveal advanced by
  // nothing at all while text was flowing. It only moved during a gap in the
  // stream, which is exactly backwards: the answer appeared to stop and start
  // in bursts, and the harder the model streamed the worse it looked.
  //
  // Now the loop is started once and left alone; it reads the refs below, so
  // new text simply extends what the running loop is already draining.
  const running = useRef(false)
  const streamingRef = useRef(streaming)

  target.current = full
  streamingRef.current = streaming

  useEffect(() => {
    // A shorter target means a different message is being rendered in this
    // slot — a regenerate, or the conversation switching underneath. Restart
    // rather than trying to reveal text that no longer exists.
    if (count.current > full.length) {
      count.current = 0
      setShown('')
    }
  }, [full])

  // A hidden tab does not run requestAnimationFrame — browsers throttle it to
  // roughly nothing in the background. Without this the reveal simply STOPS
  // when you switch away, and you come back to an answer frozen halfway
  // through with no indication that the rest already arrived. Nobody is
  // watching an animation on a tab they cannot see, so the honest behaviour is
  // to settle on the full text immediately and let the loop resume from there.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) {
        count.current = target.current.length
        setShown(target.current)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  // The drain loop. Defined once and stable for the life of the component — it
  // reads `target`, `count` and `streamingRef`, never props, so it never needs
  // rebuilding and can keep running across arriving deltas.
  const tick = useCallback((now: number) => {
    // Skip frames rather than paint on every one. `last` is not advanced on a
    // skipped frame, so the dt used when we do paint covers the whole elapsed
    // period and the reveal SPEED is unchanged — only the number of repaints
    // drops.
    if (now - last.current < MIN_FRAME_MS) {
      raf.current = requestAnimationFrame(tick)
      return
    }
    const dt = Math.min(0.1, (now - last.current) / 1000)
    last.current = now

    const streaming = streamingRef.current
    const behind = target.current.length - count.current
    if (behind <= 0) {
      if (!streaming) {
        // Caught up and the stream is closed — we are done.
        setShown(target.current)
        running.current = false
        return
      }
      // Caught up but more is coming. Keep the loop alive rather than
      // tearing it down and rebuilding on the next token.
      raf.current = requestAnimationFrame(tick)
      return
    }

    const cps = streaming
      ? Math.min(MAX_CPS, Math.max(MIN_CPS, behind / DRAIN_SECONDS))
      // Unbounded once closed: the flush is allowed to be as fast as it needs
      // to be to finish in FLUSH_SECONDS.
      : Math.max(MIN_CPS, behind / FLUSH_SECONDS)

    // At least one character per frame, or a slow rate on a fast display
    // rounds to zero and never advances.
    const step = Math.max(1, Math.round(cps * dt))
    count.current = Math.min(target.current.length, count.current + step)
    setShown(target.current.slice(0, count.current))

    raf.current = requestAnimationFrame(tick)
  }, [])

  // Stop the loop on unmount, and ONLY on unmount. Cancelling it whenever
  // `full` changed is what broke the reveal; the running loop picks new text up
  // through `target.current` by itself.
  useEffect(() => () => {
    cancelAnimationFrame(raf.current)
    running.current = false
  }, [])

  useEffect(() => {
    // Same reasoning for a tab that is ALREADY hidden when text arrives.
    if (prefersReducedMotion() || document.hidden) {
      count.current = target.current.length
      setShown(target.current)
      return
    }

    // Nothing left to reveal and nothing more coming: settle exactly on the
    // truth, so an idle transcript costs no frames.
    if (!streaming && count.current >= target.current.length) {
      if (shown !== target.current) setShown(target.current)
      return
    }

    // Already draining — leave it be. Restarting here would reset the frame
    // clock, which is the bug this guard exists to prevent.
    if (running.current) return

    running.current = true
    last.current = performance.now()
    raf.current = requestAnimationFrame(tick)
    // `shown` is deliberately not a dependency: it changes every frame and
    // including it would re-enter this effect on each one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, full, tick])

  return shown
}

/** Where to cut a partially-revealed answer so markdown does not flicker.
 *
 *  Revealing mid-syntax makes the renderer thrash: `**bo` is literal asterisks
 *  for two frames and then bold, a half-typed fence is a paragraph and then a
 *  code block, and a table redraws its column widths on every new cell. Each is
 *  a visible flash.
 *
 *  So the reveal is trimmed back to the last point that is safe to render, and
 *  the few withheld characters appear a frame or two later as a unit. The
 *  exception is an open code fence: its content is already plain text inside a
 *  `<pre>`, so it can stream character by character without any reflow, and
 *  holding it back would freeze the most satisfying thing to watch.
 */
export function safeMarkdownPrefix(text: string): string {
  if (!text) return text

  // Inside an unterminated fence, everything is literal — stream it all.
  const fences = (text.match(/^```/gm) || []).length
  if (fences % 2 === 1) return text

  let cut = text.length

  // Do not end inside an inline-code span, a link, or a partial emphasis run.
  const tail = text.slice(-90)
  const openInline = (tail.match(/`/g) || []).length % 2 === 1
  if (openInline) cut = Math.min(cut, text.length - tail.length + tail.lastIndexOf('`'))

  const openLink = tail.lastIndexOf('[')
  if (openLink !== -1 && !/\]\([^)]*\)\s*$/.test(tail.slice(openLink))
      && tail.indexOf(')', openLink) === -1) {
    cut = Math.min(cut, text.length - tail.length + openLink)
  }

  // A trailing run of emphasis markers is either the start of `**bold**` or the
  // end of it; either way it renders wrong until its partner arrives.
  const dangling = /(\*{1,2}|_{1,2})$/.exec(text.slice(0, cut))
  if (dangling) cut -= dangling[0].length

  // A fence marker being typed out (```, ``, `) at the very end.
  const partialFence = /\n`{1,3}$/.exec(text.slice(0, cut))
  if (partialFence) cut -= partialFence[0].length - 1

  // A half-typed TABLE ROW. This one is the most violent reflow of the lot: a
  // row arriving cell by cell makes the renderer rebuild the table — and
  // recompute every column width — on each new pipe, so the whole block jumps
  // sideways a dozen times per row. Withhold the trailing partial row until its
  // newline lands and it can appear as one finished line.
  const head = text.slice(0, cut)
  const lastBreak = head.lastIndexOf('\n')
  const lastLine = head.slice(lastBreak + 1)
  if (lastLine.trimStart().startsWith('|')) cut = lastBreak + 1

  return cut < text.length ? text.slice(0, Math.max(0, cut)) : text
}
