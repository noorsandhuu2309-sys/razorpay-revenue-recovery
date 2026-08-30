// CHALLENGE — stress-test an idea before spending a research run on it.
//
// The design problem this view has to solve is one of restraint. Several
// models attacking an idea produces something that LOOKS like a verdict, and
// a reader will take it as one unless the interface actively refuses. So:
// no score, no gauge, no percentage, no green tick anywhere. Agreement is
// rendered as "raised by 3 of 4" — an attribution, which is what it is — and
// never as a bar or a rating, which is what it is not.
//
// The unanimous case gets the loudest warning rather than the quietest, since
// four models agreeing is exactly when a reader is most likely to relax, and
// models share training data. That copy comes from the backend so the warning
// cannot drift out of sync with the rule that produced it.
//
// Everything here funnels to one action: turning the questions into a research
// run, where claims get sources and a verdict that means something.
//
// -- Layout ------------------------------------------------------------------
//
// The view has two states and moves between them.
//
//   COMPOSING  one column, centred in the window. The explanation sits ABOVE
//              the box, because it is the thing you read before you type, and
//              a paragraph parked in a column beside the input is a paragraph
//              nobody reads. (That is what this was: a two-column head with
//              prose on the left and the composer on the right, which also
//              overflowed the canvas on a narrow window.)
//
//   RESULT     the same composer, collapsed to a single line at the top, with
//              the run beneath it. Nothing unmounts and nothing jumps: the
//              stage is one grid whose rows re-proportion, so the box the user
//              typed into visibly BECOMES the header of the answer.
//
// Enter runs it. Shift+Enter is a newline. The old binding was ⌘↵ only, which
// on a text box that looks exactly like a chat composer is a control nobody
// finds — and the button beside it was the only way in.

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { IconChallenge, IconRuns, IconSend } from '../components/Icons'
import { PanelFlow, usePanelFlow } from '../components/PanelFlow'
import type { ChallengeMeta } from '../lib/types'

const POLL_MS = 2000
const TIMEOUT_MS = 180_000

type Phase = 'idle' | 'running' | 'done' | 'error'

interface Result {
  meta: ChallengeMeta
}

/** Ideas worth challenging share a shape: a claim with a subject, a mechanism
 *  and a horizon. These are examples of that shape, not suggestions — a user
 *  who clicks one is meant to edit it. */
const EXAMPLES = [
  'Solid-state batteries will be in mass-market EVs by 2028.',
  'Small modular reactors will be cheaper per MWh than gas by 2032.',
  'Most enterprise software will be priced per outcome, not per seat, within five years.',
]

/** Vendor attribution, never a score. */
function Raised({ by, of }: { by: string[]; of: number }) {
  if (by.length <= 1) {
    return <span className="omx-raised one">{by[0] ?? 'one model'}</span>
  }
  return (
    <span className="omx-raised many" title={by.join(', ')}>
      raised by {by.length} of {of}
    </span>
  )
}

export function ChallengeView() {
  const workspaceId = useWorkspace((s) => s.workspaceId)
  const setView = useWorkspace((s) => s.setView)

  const [idea, setIdea] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [note, setNote] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [sent, setSent] = useState<string[]>([])
  const [runId, setRunId] = useState<string | null>(null)
  /** The text the run was actually launched with. `idea` stays editable while
   *  a run is in flight, so rendering the header from it would relabel a
   *  finished result with something the panel never saw. */
  const [asked, setAsked] = useState('')

  const boxRef = useRef<HTMLTextAreaElement>(null)
  /** Set when a run is abandoned, so a poll loop that is still in flight
   *  cannot write its result over a newer one. Two runs overlapping is easy to
   *  reach: start, press New, start again. */
  const runToken = useRef(0)

  // Seat states come from the run's own progress events, so the panel shows
  // which vendors are actually working rather than a timer pretending to.
  const { seats, stage, setStage } = usePanelFlow(runId, phase === 'running')

  const composing = phase === 'idle'

  // Grow the box with its content, up to a ceiling. A fixed `rows={3}` hides
  // the end of a long idea behind an inner scrollbar exactly when the user is
  // trying to re-read what they wrote before committing to a run.
  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, composing ? 260 : 120)}px`
  }, [idea, composing])

  useEffect(() => { boxRef.current?.focus() }, [])

  async function run() {
    const text = idea.trim()
    if (!text || phase === 'running') return
    const token = ++runToken.current

    setPhase('running')
    setAsked(text)
    setNote('Asking independent models…')
    setResult(null)
    setSent([])
    setRunId(null)

    try {
      const { executionId } = await api.runAgent('challenge', text, workspaceId)
      if (runToken.current !== token) return
      setRunId(executionId)
      const deadline = Date.now() + TIMEOUT_MS

      for (;;) {
        if (Date.now() > deadline) {
          if (runToken.current !== token) return
          setPhase('error')
          setNote('The panel took too long to answer. Try again.')
          return
        }
        await new Promise((r) => setTimeout(r, POLL_MS))
        if (runToken.current !== token) return
        const ex = await api.execution(executionId)
        if (runToken.current !== token) return

        if (ex.status === 'running' || ex.status === 'queued') continue

        if (ex.status !== 'completed') {
          setPhase('error')
          setNote(ex.error || `The run ${ex.status}.`)
          return
        }

        const out = ex.steps?.[0]?.output
        const meta = out?.meta ?? {}
        // A panel that never answered is a failure with a completed status —
        // showing its empty output would read as "no objections found".
        if (meta.error) {
          setPhase('error')
          setNote(out?.summary || 'No model on the panel answered.')
          return
        }
        // Everything rendered below comes from meta. The step output's
        // `blocks` field is a COUNT, not a list — the prose is filed in the
        // artifact — and treating it as an array threw inside render, which
        // takes down the entire workspace, not just this view.
        setResult({ meta })
        setStage('done')
        setPhase('done')
        return
      }
    } catch (e) {
      if (runToken.current !== token) return
      setPhase('error')
      setNote(e instanceof Error ? e.message : 'Could not reach the server.')
    }
  }

  /** Abandon whatever is in flight and go back to an empty composer. */
  function reset(keepText: boolean) {
    runToken.current++
    setPhase('idle')
    setResult(null)
    setRunId(null)
    setStage('idle')
    setNote('')
    setSent([])
    if (!keepText) setIdea('')
    requestAnimationFrame(() => boxRef.current?.focus())
  }

  async function research(questions: string[]) {
    const fresh = questions.filter((q) => !sent.includes(q))
    if (!fresh.length) return
    setSent((s) => [...s, ...fresh])
    const results = await Promise.allSettled(
      fresh.map((q) => api.research(workspaceId, q, [])),
    )
    // A question whose run never started must not keep showing "Researching"
    // — that is a button lying about work that is not happening.
    const failed = fresh.filter((_, i) => results[i].status === 'rejected')
    if (failed.length) {
      setSent((s) => s.filter((q) => !failed.includes(q)))
      setNote(`${failed.length} run${failed.length > 1 ? 's' : ''} could not `
        + 'be started. The rest are running.')
    }
    // The runs are long; the Agents view is where they are watchable.
    if (failed.length < fresh.length) setView('agents')
  }

  // Every list is guarded with Array.isArray rather than `?? []`, which
  // only substitutes for null/undefined and would happily pass a number
  // through to .map().
  const meta = result?.meta
  const arr = <T,>(v: T[] | undefined): T[] => (Array.isArray(v) ? v : [])
  const questions = arr(meta?.researchQuestions)
  const answered = meta?.answered ?? 0
  const assumptions = arr(meta?.assumptions)
  const counters = arr(meta?.counterarguments)
  const split = arr(meta?.split)
  const allSent = questions.length > 0 && questions.every((q) => sent.includes(q))

  return (
    <div className={`omx-ch ${composing ? 'composing' : 'engaged'}`} data-phase={phase}>
      <div className="omx-ch-stage">
        {/* The explanation. Above the box while composing — it is what you read
            before you type — and gone once a run exists, because by then the
            answer explains the feature better than the paragraph did. */}
        {/* The inner div is load-bearing. `.omx-ch-intro` collapses by
            animating `grid-template-rows` from `1fr` to `0fr`, and that
            declares exactly ONE explicit row — three direct children would put
            the other two in implicit `auto` rows that the collapse never
            touches, leaving a block of empty space above the result. One child
            means one row means a complete collapse. */}
        <div className="omx-ch-intro" aria-hidden={!composing}>
          <div>
            <div className="omx-label">Before you research</div>
            <h2>Stress-test an idea</h2>
            <p>
              Independent models from different vendors attack your idea
              separately, then hand you the questions worth researching. They
              have no sources — this finds blind spots, it does not establish
              facts.
            </p>
          </div>
        </div>

        {/* Once a run has been launched the composer keeps the text but stops
            being the focus, so the idea stays readable above its own critique
            without a second copy of it on screen. */}
        {!composing && (
          <div className="omx-ch-asked">
            <span className="omx-label">Challenging</span>
            <p>{asked}</p>
            <button className="omx-btn" onClick={() => reset(false)}>
              New challenge
            </button>
            <button className="omx-btn" onClick={() => { setIdea(asked); reset(true) }}>
              Edit
            </button>
          </div>
        )}

        {composing && (
          <div className="omx-ch-box">
            <textarea
              ref={boxRef}
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="State the idea as a claim. e.g. Solid-state batteries will be in mass-market EVs by 2028."
              rows={2}
              onKeyDown={(e) => {
                // Enter runs, Shift+Enter is a newline. ⌘/Ctrl+Enter also runs,
                // because that is what the old binding was and muscle memory
                // outlives a redesign.
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void run()
                }
              }}
            />
            <div className="omx-ch-boxfoot">
              <span className="omx-null">
                <kbd>↵</kbd> to run · <kbd>⇧↵</kbd> for a new line
              </span>
              <button
                className="omx-btn primary"
                onClick={() => void run()}
                disabled={!idea.trim()}
              >
                Challenge it <IconSend size={14} />
              </button>
            </div>
          </div>
        )}

        {composing && !idea.trim() && (
          <div className="omx-ch-examples">
            <span className="omx-label">Ideas shaped like this work best</span>
            <div className="row">
              {EXAMPLES.map((e) => (
                <button key={e} className="omx-pill click"
                        onClick={() => { setIdea(e); boxRef.current?.focus() }}>
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="omx-ch-out">
        {phase === 'running' && (
          <PanelFlow seats={seats} stage={stage} note={note} />
        )}

        {phase === 'error' && (
          <div className="omx-ch-status error">
            <span>{note}</span>
            <button className="omx-btn" onClick={() => { setIdea(asked); reset(true) }}>
              Try again
            </button>
          </div>
        )}

        {phase === 'done' && result && (
          <div className="omx-ch-result">
            <div className="omx-ch-verdictless">
              <IconChallenge />
              <div>
                <strong>{meta?.headline}</strong>
                <div className="omx-null">
                  {answered} of {meta?.panelSize ?? answered} models answered
                  {meta?.vendors?.length ? ` · ${meta.vendors.join(', ')}` : ''}
                </div>
              </div>
            </div>

            {/* A completed run with nothing in it is not a clean bill of
                health, and an empty page here would read as one. */}
            {!assumptions.length && !counters.length && !split.length
              && !questions.length && (
              <div className="omx-ch-status">
                The panel answered but produced nothing structured enough to
                show. That is a failure of the run, not a verdict on the idea —
                try rephrasing it as a single specific claim.
              </div>
            )}

            {assumptions.length > 0 && (
              <section>
                <h3>Assumptions your idea rests on</h3>
                <ol className="omx-ch-list">
                  {assumptions.map((a) => (
                    <li key={a.text}>
                      <span>{a.text}</span>
                      <Raised by={a.raisedBy} of={answered} />
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {counters.length > 0 && (
              <section>
                <h3>Strongest counterargument</h3>
                {counters.map((c, i) => (
                  <div
                    key={c.text}
                    className={`omx-ch-counter ${i === 0 ? 'lead' : ''}`}
                  >
                    <p>{c.text}</p>
                    <Raised by={c.raisedBy} of={answered} />
                  </div>
                ))}
              </section>
            )}

            {split.length > 0 && (
              <section>
                <h3>
                  Where the panel split
                  <span className="omx-null"> — signal, not consensus</span>
                </h3>
                <ul className="omx-ch-split">
                  {split.map((s) => <li key={s}>{s}</li>)}
                </ul>
              </section>
            )}

            {questions.length > 0 && (
              <section className="omx-ch-questions">
                <h3>Questions that would settle it</h3>
                <ul>
                  {questions.map((q) => (
                    <li key={q}>
                      <span>{q}</span>
                      <button
                        className="omx-btn tiny"
                        disabled={sent.includes(q)}
                        onClick={() => void research([q])}
                      >
                        {sent.includes(q) ? 'Researching' : 'Research'}
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  className="omx-btn primary"
                  disabled={allSent}
                  onClick={() => void research(questions)}
                >
                  <IconRuns />{' '}
                  {allSent ? 'All sent to research'
                    : `Research all ${questions.length}`}
                </button>
              </section>
            )}

            {/* Keyed off meta.evidence, not the agent name, so any unsourced
                unit inherits this warning automatically. */}
            {meta?.evidence === 'model_opinion' && (
              <div className="omx-ch-disclaimer">{meta.disclaimer}</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
