// The CHALLENGE panel, drawn as what it actually is: one idea fanned out to
// several independent vendors and folded back into a consolidation step.
//
// Two rules govern everything here, and they pull against each other.
//
//   1. It must be honest. Every seat state comes from an `execution.progress`
//      event the backend emitted at the moment it happened. Nothing here runs
//      on a timer, and there is no simulated progress: a client-side animation
//      that advances on its own looks identical whether four models are
//      answering or the cloud backend is unreachable, and this is the one
//      product that does not get to ship that.
//
//   2. It must stay legible. Which means the wires have to actually land on the
//      boxes.
//
// Rule 2 is why this file was rewritten. The wires used to be an SVG with
// `viewBox="0 0 1 N"` and `preserveAspectRatio="none"`, laid out in "seat
// units" on the assumption that seat i occupies exactly the i-th of N equal
// horizontal bands. That assumption is false the moment any seat is a
// different height from its neighbours — which is the normal case here, since
// a retrying seat carries an extra line and a long model id wraps. The result
// was curves that pointed at the gaps between the boxes and drifted further
// with every state change, plus a visible snap on each re-render as the
// stretch factor changed.
//
// So geometry is now MEASURED, not assumed. Each seat registers its own DOM
// node; a ResizeObserver over the container and every seat recomputes anchor
// points from real `getBoundingClientRect()` values, in the container's own
// coordinate space, and the SVG is a plain 1:1 overlay with no aspect-ratio
// distortion. A wire cannot point at the wrong place because it is derived
// from where the box actually is.
//
// It also, deliberately, never renders a score, a percentage or a tick. A seat
// that answered is drawn as answered; that is a fact about the vendor, not a
// judgement about the idea. See challenge.py for why that distinction is the
// whole design.

import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
} from 'react'
import { api } from '../lib/api'

export type SeatState =
  | 'pending' | 'thinking' | 'answered' | 'silent' | 'unusable' | 'retry'

export interface Seat {
  vendor: string
  model: string
  state: SeatState
}

/** Stage of the run as a whole, which drives which wires are live. */
export type FlowStage = 'idle' | 'asking' | 'consolidating' | 'done'

const STATE_LABEL: Record<SeatState, string> = {
  pending: 'waiting',
  thinking: 'reviewing the idea',
  answered: 'answered',
  silent: 'no answer',
  unusable: 'unreadable reply',
  retry: 'retrying',
}

/** Seats that have finished, either way. Used to decide when the outbound
 *  wires stop pulsing without waiting for the whole run. */
const SETTLED: ReadonlySet<SeatState> = new Set(['answered', 'silent', 'unusable'])

/** Poll the run's trail and fold it into seat states.
 *
 *  Incremental by `seq`: each poll asks only for what it has not seen, so a
 *  long run does not re-download its whole history every second. The reducer is
 *  order-independent for everything except retry, which is why the events carry
 *  their own meaning rather than the view inferring one. */
export function usePanelFlow(executionId: string | null, active: boolean) {
  const [seats, setSeats] = useState<Seat[]>([])
  const [stage, setStage] = useState<FlowStage>('idle')
  const seq = useRef(0)

  useEffect(() => {
    if (!executionId || !active) return
    let alive = true
    seq.current = 0
    setSeats([])
    setStage('asking')

    const tick = async () => {
      try {
        const { events } = await api.agentEvents(executionId, seq.current)
        if (!alive || !events.length) return
        seq.current = events[events.length - 1].seq

        setSeats((prev) => {
          let next = [...prev]
          for (const ev of events) {
            if (ev.type !== 'execution.progress') continue
            const st = String(ev.payload.stage ?? '')
            const detail = String(ev.payload.detail ?? '')

            if (st === 'panel.seated') {
              // The roster arrives before any vendor has been called, so all
              // the seats exist from the first frame instead of popping in.
              try {
                const roster = JSON.parse(detail) as
                  { vendor: string; model: string }[]
                next = roster.map((r) => ({ ...r, state: 'pending' as const }))
              } catch { /* a malformed roster is not worth a broken view */ }
              continue
            }
            if (!st.startsWith('seat.')) continue

            const state = st.slice(5) as SeatState
            const i = next.findIndex((s) => s.vendor === detail)
            if (i >= 0) next[i] = { ...next[i], state }
          }
          return next
        })

        for (const ev of events) {
          if (ev.type !== 'execution.progress') continue
          if (String(ev.payload.stage ?? '') === 'consolidate') {
            setStage('consolidating')
          }
        }
      } catch { /* the caller's own poll surfaces a dead run */ }
    }

    void tick()
    const id = window.setInterval(tick, 1200)
    return () => { alive = false; window.clearInterval(id) }
  }, [executionId, active])

  return { seats, stage, setStage }
}

interface Point { x: number; y: number }

interface Geometry {
  width: number
  height: number
  /** Where the outbound wires leave the idea node. */
  from: Point
  /** Where the inbound wires arrive at the consolidation node. */
  to: Point
  /** Left and right edge midpoints of each seat, in seat order. */
  seats: { inp: Point; out: Point }[]
}

/** Measure the flow's real geometry from the DOM.
 *
 *  Everything is expressed relative to the container's own top-left, so the
 *  numbers survive the page being scrolled, the rail being collapsed, or the
 *  panel being inside a transformed ancestor — all of which move
 *  `getBoundingClientRect()` in viewport space and none of which should move a
 *  wire relative to the box it points at. */
function measure(
  root: HTMLElement | null,
  start: HTMLElement | null,
  finish: HTMLElement | null,
  seats: (HTMLElement | null)[],
): Geometry | null {
  if (!root || !start || !finish) return null
  const base = root.getBoundingClientRect()
  if (!base.width || !base.height) return null

  const rel = (el: HTMLElement, side: 'left' | 'right'): Point => {
    const r = el.getBoundingClientRect()
    return {
      x: (side === 'left' ? r.left : r.right) - base.left,
      y: r.top + r.height / 2 - base.top,
    }
  }

  const rows = seats
    .filter((el): el is HTMLElement => el !== null)
    .map((el) => ({ inp: rel(el, 'left'), out: rel(el, 'right') }))

  if (!rows.length) return null
  return {
    width: base.width,
    height: base.height,
    from: rel(start, 'right'),
    to: rel(finish, 'left'),
    seats: rows,
  }
}

/** A horizontal cubic between two points.
 *
 *  Control points sit at a fixed FRACTION of the horizontal gap rather than at
 *  a fixed pixel offset, so the curve keeps its shape at every panel width
 *  instead of flattening on a narrow window and ballooning on a wide one. The
 *  0.55 lead-out against 0.45 lead-in gives the fan a slight forward lean,
 *  which reads as direction without needing an arrowhead on every wire. */
function curve(a: Point, b: Point): string {
  const dx = Math.max(24, b.x - a.x)
  return `M${a.x},${a.y} C${a.x + dx * 0.55},${a.y} ${b.x - dx * 0.45},${b.y} ${b.x},${b.y}`
}

export function PanelFlow({ seats, stage, note }: {
  seats: Seat[]
  stage: FlowStage
  note?: string
}) {
  const rootRef = useRef<HTMLDivElement>(null)
  const startRef = useRef<HTMLDivElement>(null)
  const finishRef = useRef<HTMLDivElement>(null)
  const seatRefs = useRef<(HTMLLIElement | null)[]>([])
  const [geo, setGeo] = useState<Geometry | null>(null)

  const remeasure = useCallback(() => {
    setGeo(measure(rootRef.current, startRef.current, finishRef.current,
                   seatRefs.current.slice(0, seats.length)))
  }, [seats.length])

  // Layout effect, not effect: measuring after paint would draw one frame of
  // wires at the previous geometry every time the seat count changes.
  useLayoutEffect(() => { remeasure() }, [remeasure, stage, seats])

  // A ResizeObserver on the container AND every seat. The container alone is
  // not enough — a seat's status text changing from "waiting" to "reviewing
  // the idea" can rewrap and change that row's height without the container's
  // own box moving at all, which is exactly the case the old fixed-band
  // geometry got wrong.
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => remeasure())
    if (rootRef.current) ro.observe(rootRef.current)
    for (const el of seatRefs.current) if (el) ro.observe(el)
    return () => ro.disconnect()
  }, [remeasure, seats.length])

  const working = seats.some((s) => s.state === 'thinking' || s.state === 'retry')
  const anySettled = seats.some((s) => SETTLED.has(s.state))

  // Nothing is drawn once the run is over. The finished panel reports its own
  // roll-call from `meta.answered` and `meta.vendors`, which the backend
  // computed from the replies it actually used; a second count derived here
  // from progress events disagreed with it in practice, because polling stops
  // as soon as the execution reads `completed` and the last seat's
  // `seat.answered` event can still be in flight. Live progress and a final
  // tally are different questions, and only one of them is authoritative.
  if (stage === 'done') return null

  if (!seats.length) {
    return (
      <div className="omx-flow-summary">
        <span className="omx-spin" />
        <span>{note || 'Seating the panel…'}</span>
      </div>
    )
  }

  return (
    <div className="omx-flow" data-stage={stage} ref={rootRef}>
      {/* One overlay for both fans. Drawn first in DOM order so it sits behind
          the nodes, with `pointer-events: none` in CSS — a wire that swallowed
          a click on a seat would be a decoration breaking a control. */}
      {geo && (
        <svg
          className="omx-flow-wires"
          width={geo.width}
          height={geo.height}
          viewBox={`0 0 ${geo.width} ${geo.height}`}
          aria-hidden="true"
        >
          {geo.seats.map((s, i) => {
            const st = seats[i]?.state ?? 'pending'
            return (
              <path
                key={`out-${i}`}
                className="wire out"
                data-state={st}
                data-live={working && !SETTLED.has(st) ? 'yes' : 'no'}
                d={curve(geo.from, s.inp)}
              />
            )
          })}
          {geo.seats.map((s, i) => {
            const st = seats[i]?.state ?? 'pending'
            return (
              <path
                key={`in-${i}`}
                className="wire in"
                data-state={st}
                // An inbound wire is only real once that seat has something to
                // send. Drawing all of them live from the start implied every
                // vendor had already contributed.
                data-live={stage === 'consolidating' && SETTLED.has(st) ? 'yes' : 'no'}
                d={curve(s.out, geo.to)}
              />
            )
          })}
        </svg>
      )}

      <div className="omx-flow-end start" ref={startRef}>
        <span className="omx-flow-endlabel">Your idea</span>
      </div>

      <ol className="omx-flow-seats">
        {seats.map((s, i) => (
          <li
            key={s.vendor}
            ref={(el) => { seatRefs.current[i] = el }}
            className="omx-flow-seat"
            data-state={s.state}
          >
            <span className="omx-flow-dot" aria-hidden="true" />
            <span className="v">{s.vendor}</span>
            {/* Naming the checkpoint is the independence claim. "OpenAI" alone
                is not something a reader can check. */}
            <span className="m" title={s.model}>{s.model}</span>
            <span className="s">{STATE_LABEL[s.state] ?? s.state}</span>
          </li>
        ))}
      </ol>

      <div
        className={`omx-flow-end finish ${stage === 'consolidating' ? 'on' : ''}`}
        ref={finishRef}
      >
        <span className="omx-flow-endlabel">
          {stage === 'consolidating' ? 'Consolidating'
            : anySettled ? 'Waiting on the rest' : 'What they agree on'}
        </span>
      </div>
    </div>
  )
}
