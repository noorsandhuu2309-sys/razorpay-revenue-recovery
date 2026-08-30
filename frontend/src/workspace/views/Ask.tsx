// Ask OMNIX — the conversation with NOVA.
//
// This exists because the answer used to render in a 132-pixel strip above the
// input that cleared on the next send. There was nowhere to read a reply and no
// way to see what had already been said, which is what made NOVA feel broken
// even when it was working.
//
// The thread is stored per Space on the backend, so this view and the bar at
// the bottom of every other view are the SAME conversation — asking from the
// Graph and then opening this view shows the exchange that just happened, and
// reloading the page does not lose it.

import { useEffect, useRef, useState } from 'react'
import { useWorkspace } from '../store/workspace'
import { Markdown } from '../components/Markdown'
import { VoiceControls } from '../components/VoiceControls'
import { IconAsk } from '../components/Icons'
import type { NovaTurn } from '../lib/types'

const TIME = (iso: string): string => {
  const d = new Date(iso)
  return Number.isFinite(d.getTime())
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : ''
}

const SUGGESTIONS = [
  'What is in this Space?',
  'Which objects have the most relationships?',
  'What changed recently?',
  'Research the current state of the Strait of Hormuz',
]

function Turn({ turn }: { turn: NovaTurn }) {
  const objects = useWorkspace((s) => s.contextObjects)
  const isUser = turn.role === 'user'
  const named = turn.context.map((id) => objects[id]?.name).filter(Boolean)

  return (
    <div className={`omx-turn ${isUser ? 'user' : 'nova'}`}>
      <div className="omx-turn-head">
        <span className="who">{isUser ? 'You' : 'NOVA'}</span>
        {!isUser && turn.model && (
          <span className="omx-label mdl" title="Model chosen by the router">
            {turn.model}
          </span>
        )}
        {!isUser && turn.intent && turn.intent !== 'direct' && (
          <span className="omx-label it">{turn.intent}</span>
        )}
        <span className="omx-label ts">{TIME(turn.createdAt)}</span>
      </div>

      {named.length > 0 && (
        <div className="omx-turn-ctx">
          <span className="omx-label">about</span>
          {named.map((n) => <span className="omx-pill" key={n}>{n}</span>)}
        </div>
      )}

      {/* The user's own message is shown verbatim — they typed it, and
          reformatting someone's own words back at them is wrong. NOVA's reply
          is markdown and gets rendered as such. */}
      {isUser || !turn.text ? (
        <div className={`omx-turn-body ${turn.intent === 'error' ? 'err' : ''}`}>
          {turn.text || <span className="omx-null">(no reply)</span>}
        </div>
      ) : (
        <Markdown
          className={`omx-turn-body ${turn.intent === 'error' ? 'err' : ''}`}
          text={turn.text}
        />
      )}
    </div>
  )
}

export function AskNovaView() {
  const turns = useWorkspace((s) => s.turns)
  const asking = useWorkspace((s) => s.asking)
  const ask = useWorkspace((s) => s.ask)
  /** Most recent reply, for the read-answers-aloud toggle. */
  const lastAnswer = [...turns].reverse().find((t) => t.role === 'assistant')
  const loadThread = useWorkspace((s) => s.loadThread)
  const clearThread = useWorkspace((s) => s.clearThread)
  const workspaceId = useWorkspace((s) => s.workspaceId)
  const workspaces = useWorkspace((s) => s.workspaces)

  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const space = workspaces.find((w) => w.id === workspaceId)

  useEffect(() => { void loadThread() }, [loadThread, workspaceId])

  // Keep the newest turn in view. Only on length change, so re-renders from
  // unrelated store updates do not yank the reader back down mid-scroll.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length])

  const send = async () => {
    const t = draft.trim()
    if (!t || asking) return
    setDraft('')
    await ask(t)
  }

  return (
    <div className="omx-ask-view">
      <div className="omx-ask-head">
        <div>
          <h2>Ask OMNIX</h2>
          <span className="omx-label">
            {space ? `conversation for ${space.name}` : 'conversation'}
            {turns.length > 0 && ` · ${turns.length} turns · remembered`}
          </span>
        </div>
        <div style={{ flex: 1 }} />
        {turns.length > 0 && (
          <button className="omx-btn" onClick={() => void clearThread()}>
            Clear thread
          </button>
        )}
      </div>

      <div className="omx-ask-scroll">
        {turns.length === 0 ? (
          <div className="omx-ask-empty">
            <div className="glyph"><IconAsk size={34} /></div>
            <h3>Ask anything about this Space</h3>
            <p>
              NOVA remembers this conversation, so follow-ups like “yes” or
              “go on” work. Questions about your objects are answered from the
              graph; asking it to research something sends an agent and writes
              the findings back into the Space.
            </p>
            <div className="omx-ask-suggest">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="omx-pill click"
                        onClick={() => { setDraft(s) }}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((t) => <Turn key={t.id} turn={t} />)
        )}
        {asking && (
          <div className="omx-turn nova">
            <div className="omx-turn-head"><span className="who">NOVA</span></div>
            <div className="omx-turn-body">
              <span className="omx-spin" /> <span className="omx-label">thinking…</span>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="omx-ask-compose">
        <textarea
          className="omx-textarea"
          placeholder="Ask about this Space, or say “research …” to send an agent."
          value={draft}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline. A multi-line prompt is
            // common enough here that the textarea has to allow it.
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
          }}
          disabled={asking}
        />
        {/* Voice belongs here most of all — this is the chat surface. The
            global bar stands down on this view, so without these the one
            screen built for conversation would be the one you cannot talk to. */}
        <VoiceControls
          asking={asking}
          lastAnswer={lastAnswer?.text ?? ''}
          onTranscript={(t) => setDraft(t)}
          onAsk={async (t) => {
            await ask(t)
            const next = useWorkspace.getState().turns
            return [...next].reverse().find((x) => x.role === 'assistant')?.text ?? ''
          }}
        />
        <button className="omx-btn primary" onClick={() => void send()}
                disabled={asking || !draft.trim()}>
          {asking ? <span className="omx-spin" /> : 'Send'}
        </button>
      </div>
    </div>
  )
}
