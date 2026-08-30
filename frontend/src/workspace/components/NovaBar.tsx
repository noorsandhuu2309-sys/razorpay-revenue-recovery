// The Context Lens + NOVA's global input.
//
// This pair is the interaction the whole product rests on: the user selects
// objects anywhere, they appear here as chips, and "explain the relationship
// between these" works without the user naming anything. The selection IS the
// context — there is no separate "attach" step to forget.

import { useRef, useState } from 'react'
import { useWorkspace } from '../store/workspace'
import { ProvDot } from './Provenance'
import { VoiceControls } from './VoiceControls'

export function NovaBar() {
  const ws = useWorkspace((s) => s.workspaceId)
  const selected = useWorkspace((s) => s.selected)
  const pinned = useWorkspace((s) => s.pinned)
  const ctxObjects = useWorkspace((s) => s.contextObjects)
  const deselect = useWorkspace((s) => s.deselect)
  const unpin = useWorkspace((s) => s.unpin)
  const clearSelection = useWorkspace((s) => s.clearSelection)
  const pin = useWorkspace((s) => s.pin)

  // The conversation itself lives in the store and is rendered by the Ask
  // view. This bar is an input into that one thread, not a second one — which
  // is why it no longer keeps a local `reply` that vanished on the next send.
  const ask = useWorkspace((s) => s.ask)
  const asking = useWorkspace((s) => s.asking)
  const turns = useWorkspace((s) => s.turns)
  const setView = useWorkspace((s) => s.setView)
  const view = useWorkspace((s) => s.view)

  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const lastAnswer = [...turns].reverse().find((t) => t.role === 'assistant')

  const submit = async () => {
    const text = input.trim()
    if (!text || asking || !ws) return
    setInput('')
    await ask(text)
  }

  const chips = [
    ...pinned.map((id) => ({ id, pinned: true })),
    ...selected.filter((id) => !pinned.includes(id)).map((id) => ({ id, pinned: false })),
  ]

  return (
    <div className="omx-nova">
      {chips.length > 0 && (
        <div className="omx-lens">
          <span className="omx-label" style={{ marginRight: 2 }}>Context</span>
          {chips.map(({ id, pinned: isPinned }) => {
            const o = ctxObjects[id]
            return (
              <span className={`omx-chip ${isPinned ? 'pinned' : ''}`} key={id}>
                {/* Two marks, and they answer different questions: the dot is
                    provenance (can I trust this?), the glyph is family (what
                    is it?). Carrying both here means the context the AI is
                    about to be given is legible without opening anything. */}
                {o && <ProvDot p={o.provenance} />}
                <span className="gl" style={{ color: o?.color ?? 'var(--omx-gold)' }}>
                  {o?.glyph ?? '○'}
                </span>
                <span className="nm">{o?.name ?? id.slice(0, 8)}</span>
                {!isPinned && (
                  <span
                    className="x"
                    title="Pin — survives selection changes"
                    onClick={() => pin(id)}
                    style={{ fontSize: 11 }}
                  >⌾</span>
                )}
                <span
                  className="x"
                  title="Remove from context"
                  onClick={() => (isPinned ? unpin(id) : deselect(id))}
                >×</span>
              </span>
            )
          })}
          <button
            className="omx-btn"
            style={{ padding: '2px 7px' }}
            onClick={() => { clearSelection(); pinned.forEach(unpin) }}
          >Clear</button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
        <input
          ref={inputRef}
          className="omx-input"
          placeholder={
            chips.length
              ? `Ask about the ${chips.length} selected object${chips.length > 1 ? 's' : ''}…`
              : 'Ask NOVA, or research a topic…   (⌘K for commands)'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
          disabled={asking}
        />
        <VoiceControls
          asking={asking}
          lastAnswer={lastAnswer?.text ?? ''}
          // Dictation fills the box rather than sending: speech recognition is
          // wrong often enough that firing an unseen transcript is a bad trade.
          onTranscript={(t) => { setInput(t); inputRef.current?.focus() }}
          onAsk={async (t) => {
            await ask(t)
            const turns = useWorkspace.getState().turns
            const reply = [...turns].reverse().find((x) => x.role === 'assistant')
            return reply?.text ?? ''
          }}
        />
        <button className="omx-btn primary" onClick={() => void submit()}
                disabled={asking || !input.trim()}>
          {asking ? <span className="omx-spin" /> : 'Send'}
        </button>
      </div>

      {/* A one-line trailer, not the answer. The answer belongs in the Ask
          view where there is room to read it and the rest of the conversation
          is visible; showing it twice invites the two to disagree. */}
      {view !== 'nova' && (asking || lastAnswer) && (
        <button className="omx-nova-trailer" onClick={() => setView('nova')}>
          {asking
            ? <><span className="omx-spin" /> <span className="omx-label">NOVA is thinking…</span></>
            : (
              <>
                <span className="tx">{lastAnswer!.text.slice(0, 150)}
                  {lastAnswer!.text.length > 150 ? '…' : ''}</span>
                <span className="omx-label go">Open conversation →</span>
              </>
            )}
        </button>
      )}
    </div>
  )
}
