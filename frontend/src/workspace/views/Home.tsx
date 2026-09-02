// Home — the assistant.
//
// This used to be a landing page: Spaces, what changed, activity, Intents,
// agent runs and a provenance bar. Every one of those was a summary of a view
// that already exists and is reachable from the rail, so the screen you arrived
// on was a table of contents for the screens you actually wanted. Meanwhile the
// thing people open a tool like this to do — ask it something — was a one-line
// input at the top that handed off to a different view to answer.
//
// So Home is now the chatbot, with the full set of behaviours a modern
// assistant has: streaming replies you can stop mid-flight, regenerate, copy,
// edit-and-resend, markdown with per-block code copy, image attachments, an
// agent picker, cited sources, multiple saved conversations, export, and
// keyboard shortcuts. What was lost from the old landing page is one strip on
// the empty state naming what this Space holds — the rest was links, and the
// rail is the links.
//
// Three rules this view is written to:
//
//   * Nothing is invented. The model name on a reply is the one the SERVER
//     said answered, arriving in a second meta event after the ladder resolves.
//     A turn with no model shows none rather than the one we hoped for.
//   * Stop actually stops. The button aborts the fetch, which closes the
//     socket, which ends the server's generator. It is not a render-level
//     pause with the tokens still being paid for.
//   * Every destructive control is reversible or confirmed. Deleting a
//     conversation is the only irreversible one, so it asks twice — inline,
//     not through a modal, because a dialog for this is heavier than the act.

import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
} from 'react'
import { Markdown } from '../components/Markdown'
import {
  IconAgents, IconAsk, IconAttach, IconCheck, IconClose, IconCopy, IconEdit,
  IconPlus, IconRegen, IconSend, IconStop, IconTrash,
} from '../components/Icons'
import {
  AGENTS, AUTO_MODEL, asMarkdown, canStop, deleteConversation,
  editAndResend, newConversation, openConversation, regenerate,
  renameConversation, send, sendAndWait, setAgent, setModel, stop, useChat,
  type AgentId, type Conversation, type Message,
} from '../lib/chat'
import { ShareMenu } from '../components/ShareMenu'
import { VoiceMode } from '../components/VoiceMode'
import {
  checkVoice, isSpeaking, speak, speakable, stopSpeaking,
} from '../lib/voice'
import { IconConverse, IconSpeaker, IconSpeakerOff } from '../components/Icons'
import { ModelPicker } from '../components/ModelPicker'
import { ArtifactPanel } from '../components/Artifact'
import type { Artifact } from '../lib/artifacts'
import { safeMarkdownPrefix, useSmoothText } from '../lib/useSmoothText'
import { useWorkspace } from '../store/workspace'
import '../assistant.css'

const RELATIVE = (ms: number): string => {
  const mins = Math.round((Date.now() - ms) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

const STARTERS = [
  'Explain the difference between a claim and a source in OMNIX.',
  'Write a Python function that de-duplicates a list of dicts by one key.',
  // Two that exercise the surfaces most people never discover on their own:
  // a chart figure and an interactive artifact. Both are things the answer
  // cannot show as text, so the starter is how they get found.
  'Chart the top 5 countries by lithium reserves as a bar chart.',
  'Build a single-page HTML unit converter with a dark theme.',
  'What are the main risks in relying on a single supplier for lithium?',
]

/** A copy button that acknowledges. Shared by the message toolbar. */
function CopyButton({ text, label }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button
      className="omx-chat-act"
      title="Copy"
      onClick={() => {
        navigator.clipboard.writeText(text)
          .then(() => setOk(true))
          .catch(() => setOk(false))
        setTimeout(() => setOk(false), 1600)
      }}
    >
      {ok ? <IconCheck size={13} /> : <IconCopy size={13} />}
      {label && <span>{ok ? 'Copied' : label}</span>}
    </button>
  )
}

/** Read one answer aloud. Its own component because it holds the playing state,
 *  and because only one thing speaks at a time app-wide — pressing play on a
 *  second answer replaces the first rather than talking over it. */
function ReadAloud({ text }: { text: string }) {
  const [playing, setPlaying] = useState(false)

  useEffect(() => () => { if (playing) stopSpeaking() }, [playing])

  const toggle = () => {
    if (playing || isSpeaking()) {
      stopSpeaking()
      setPlaying(false)
      return
    }
    setPlaying(true)
    speak(speakable(text)).done.finally(() => setPlaying(false))
  }

  return (
    <button className={`omx-chat-act ${playing ? 'on' : ''}`} onClick={toggle}
            title={playing ? 'Stop reading' : 'Read this answer aloud'}>
      {playing ? <IconSpeaker size={13} /> : <IconSpeakerOff size={13} />}
      <span>{playing ? 'Stop' : 'Listen'}</span>
    </button>
  )
}

function Turn({ m, isLast, busy, openArtifactId, onArtifact, voiceReady }: {
  m: Message; isLast: boolean; busy: boolean
  openArtifactId: string | null
  onArtifact: (a: Artifact) => void
  voiceReady: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(m.text)

  // Called before the user-turn branch below, because hooks cannot sit after a
  // conditional return. It is free for a user turn: with `streaming` false it
  // settles on the full text immediately and never starts a frame loop.
  const revealed = useSmoothText(m.text, !!m.streaming)

  if (m.role === 'user') {
    return (
      <div className="omx-chat-turn user">
        {editing ? (
          <div className="omx-chat-edit">
            <textarea
              value={draft}
              autoFocus
              rows={Math.min(10, draft.split('\n').length + 1)}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') { setEditing(false); setDraft(m.text) }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  setEditing(false)
                  void editAndResend(m.id, draft)
                }
              }}
            />
            <div className="row">
              <button className="omx-btn tiny"
                      onClick={() => { setEditing(false); setDraft(m.text) }}>
                Cancel
              </button>
              <button className="omx-btn tiny primary" disabled={!draft.trim()}
                      onClick={() => { setEditing(false); void editAndResend(m.id, draft) }}>
                Send
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="omx-chat-bubble">
              {m.image && (
                <img className="omx-chat-img" src={m.image} alt="Attached" />
              )}
              {m.text}
            </div>
            <div className="omx-chat-tools">
              <CopyButton text={m.text} />
              {/* Editing re-runs everything after this turn, so it is offered
                  only when nothing is in flight — an edit landing mid-stream
                  would race the reply it is about to delete. */}
              <button className="omx-chat-act" title="Edit and resend"
                      disabled={busy}
                      onClick={() => { setDraft(m.text); setEditing(true) }}>
                <IconEdit size={13} />
              </button>
            </div>
          </>
        )}
      </div>
    )
  }

  // The reveal is presentational: `m.text` stays the source of truth, so a
  // reload, an export, a copy or read-aloud always gets the whole answer
  // regardless of how much of it has been painted.
  //
  // While streaming, cut back to a point that is safe to parse — otherwise a
  // half-typed `**` or table row renders as literal characters for a frame and
  // then snaps into place, which is exactly the flicker the reveal is for.
  const body = m.streaming ? safeMarkdownPrefix(revealed) : m.text

  const empty = !body && !m.error
  return (
    <div className="omx-chat-turn nova">
      <div className="omx-chat-head">
        <span className="who">OMNIX</span>
        {m.agent && <span className="omx-label ag">{m.agent}</span>}
        {/* Named only once the server says which model answered. */}
        {m.model && <span className="omx-label mdl" title="Model that answered">{m.model}</span>}
      </div>

      {empty && m.streaming ? (
        <div className="omx-chat-thinking">
          <span className="d" /><span className="d" /><span className="d" />
        </div>
      ) : (
        <Markdown
          className="omx-chat-body" text={body} streaming={m.streaming}
          // Only the chat transcript enriches: charts render as figures and
          // substantial blocks become artifact cards. Every other surface that
          // renders markdown (ARENA seats, ORACLE, the Inspector) deliberately
          // keeps plain code blocks.
          enrich={{ messageId: m.id, openId: openArtifactId, onOpen: onArtifact }}
        />
      )}

      {m.streaming && body && <span className="omx-chat-caret" aria-hidden="true" />}

      {m.sources && m.sources.length > 0 && (
        <div className="omx-chat-sources">
          <span className="omx-label">Sources it searched</span>
          <ol>
            {m.sources.map((s) => (
              <li key={s.n}>
                <a href={s.url} target="_blank" rel="noreferrer noopener">
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ol>
        </div>
      )}

      {m.stopped && <div className="omx-chat-note">Stopped.</div>}
      {m.error && <div className="omx-chat-error">{m.error}</div>}

      {!m.streaming && (
        <div className="omx-chat-tools">
          {m.text && <CopyButton text={m.text} label="Copy" />}
          {/* Copy in other shapes, and save as a document. Always the FULL
              text, never the partially-revealed `body`. */}
          {m.text && <ShareMenu markdown={m.text} subtitle={`Answered by ${m.model || 'OMNIX'}`} />}
          {m.text && voiceReady && <ReadAloud text={m.text} />}
          {/* Regenerate only on the newest reply: re-running an older one would
              silently discard every turn after it, and a destructive action
              hidden behind a refresh icon is not a fair trade. */}
          {isLast && (
            <button className="omx-chat-act" disabled={busy}
                    onClick={() => void regenerate(m.id)}
                    title="Regenerate this reply">
              <IconRegen size={13} /><span>Regenerate</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function ConversationRow({ c, active, onOpen }: {
  c: Conversation; active: boolean; onOpen: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(c.title)

  // Reset the confirm state when the row scrolls out of relevance, so a
  // half-pressed delete does not sit armed until the next visit.
  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => setConfirming(false), 4000)
    return () => clearTimeout(t)
  }, [confirming])

  if (renaming) {
    return (
      <div className="omx-chat-conv renaming">
        <input
          autoFocus value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => { renameConversation(c.id, draft); setRenaming(false) }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { renameConversation(c.id, draft); setRenaming(false) }
            if (e.key === 'Escape') { setDraft(c.title); setRenaming(false) }
          }}
        />
      </div>
    )
  }

  return (
    <div className={`omx-chat-conv ${active ? 'on' : ''}`}>
      <button className="open" onClick={onOpen} onDoubleClick={() => setRenaming(true)}
              title={`${c.title} — double-click to rename`}>
        <span className="t">{c.title}</span>
        <span className="s">
          {c.messages.length
            ? `${c.messages.length} messages · ${RELATIVE(c.updatedAt)}`
            : 'empty'}
        </span>
      </button>
      <button
        className={`del ${confirming ? 'armed' : ''}`}
        title={confirming ? 'Click again to delete' : 'Delete conversation'}
        onClick={() => {
          if (confirming) deleteConversation(c.id)
          else setConfirming(true)
        }}
      >{confirming ? <IconCheck size={12} /> : <IconTrash size={12} />}</button>
    </div>
  )
}

export function HomeView() {
  const { conversations, activeId, busy } = useChat()
  const conv = conversations.find((c) => c.id === activeId) ?? conversations[0]
  const summary = useWorkspace((s) => s.summary)
  const workspaces = useWorkspace((s) => s.workspaces)
  const workspaceId = useWorkspace((s) => s.workspaceId)
  const space = workspaces.find((w) => w.id === workspaceId)

  const [draft, setDraft] = useState('')
  const [image, setImage] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(false)
  const [pinned, setPinned] = useState(true)
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [voiceOpen, setVoiceOpen] = useState(false)
  // Whether the server actually has the voice stack. The read-aloud and voice
  // buttons are hidden rather than shown-and-broken on a build without it —
  // clicking one would produce a 500 the user cannot interpret.
  const [voiceReady, setVoiceReady] = useState(false)

  useEffect(() => { void checkVoice().then(setVoiceReady) }, [])

  const boxRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const messages = conv?.messages ?? []
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')

  // Auto-grow the composer, capped. A textarea that grows without limit pushes
  // the conversation off screen; one that never grows hides the end of a long
  // prompt behind an inner scrollbar.
  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [draft])

  // Follow the stream, but only while the reader is at the bottom. Yanking
  // someone back down while they are re-reading an earlier answer is the
  // single most irritating thing a chat UI does; `pinned` is set from the
  // scroll position and cleared the moment they scroll away.
  const atBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < 90
  }, [])

  // Mirrors `pinned` for the ResizeObserver below, which is registered once and
  // cannot see later state through its closure.
  const pinnedRef = useRef(pinned)
  pinnedRef.current = pinned

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => setPinned(atBottom())
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [atBottom])

  // Hold the bottom as the CONTENT grows, not only as messages arrive.
  //
  // The message effect below fires when `messages` changes, which on mount is
  // before the transcript has laid out — fonts are still settling and the
  // markdown has not been measured, so `scrollHeight` is barely more than one
  // viewport and "scroll to the bottom" lands at the top. Reopening OMNIX on a
  // long conversation therefore showed the FIRST message, and the newest reply
  // was thousands of pixels below the fold.
  //
  // A ResizeObserver fixes it at the source: whenever the content's height
  // changes and the reader is pinned, go to the new bottom. That covers the
  // mount case, a code block that reflows, an image finishing decode, and the
  // artifact panel opening and narrowing the column.
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !el.firstElementChild) return
    const toBottom = () => {
      // Read `pinned` from the ref rather than the closure: this observer is
      // set up once and would otherwise capture the value from mount forever.
      if (!pinnedRef.current) return
      // `behavior: 'instant'` is load-bearing. The sheet sets
      // `scroll-behavior: smooth` on this container, so a plain `scrollTop =`
      // becomes an ANIMATED scroll — which is driven by requestAnimationFrame,
      // and therefore does not progress at all while the tab is in the
      // background, and crawls across 4,000px even when it is. That is what
      // left a long transcript stuck at the very top: the jump was issued
      // correctly every time and then never arrived.
      el.scrollTo({ top: el.scrollHeight, behavior: 'instant' })
    }
    // Jump once immediately: if the transcript is already fully laid out when
    // this runs, no resize will follow and the observer alone would never fire.
    toBottom()
    const obs = new ResizeObserver(toBottom)
    obs.observe(el)
    for (const child of el.children) obs.observe(child)
    return () => obs.disconnect()
  }, [activeId])

  // Smooth follow while a reply streams in. The observer above is the one that
  // guarantees correctness; this one exists for the easing.
  useEffect(() => {
    if (!pinned) return
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: messages.length > 1 ? 'smooth' : 'auto',
    })
  }, [messages, pinned, lastAssistant?.text])

  // Shortcuts, scoped to this view. Ctrl/⌘+K would normally open the command
  // palette; it is deliberately NOT rebound here — a global shortcut that
  // means something different on one screen is worse than not having it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault(); newConversation(); boxRef.current?.focus()
      }
      if (e.key === 'Escape' && canStop()) { e.preventDefault(); stop() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => { boxRef.current?.focus() }, [activeId])

  // Switching conversations closes the panel. The artifact belonged to a
  // message in the conversation being left, so keeping it open would leave a
  // pane of content with no visible origin.
  useEffect(() => { setArtifact(null) }, [activeId])

  // Keep the open artifact in step with the message it came from: a regenerate
  // rewrites the reply, and the panel would otherwise keep showing the previous
  // version of a file that no longer exists in the transcript.
  useEffect(() => {
    if (!artifact) return
    const stillThere = messages.some((m) => artifact.id.startsWith(`${m.id}:`))
    if (!stillThere) setArtifact(null)
  }, [messages, artifact])

  const submit = () => {
    const text = draft.trim()
    if ((!text && !image) || busy) return
    setDraft('')
    const img = image ?? undefined
    setImage(null)
    setPinned(true)
    void send(text, img)
  }

  const attach = (file: File | undefined) => {
    if (!file || !file.type.startsWith('image/')) return
    // 6MB before base64, which is ~8MB as a data URL. Larger than that and the
    // POST body starts costing more than the answer is worth.
    if (file.size > 6 * 1024 * 1024) return
    const reader = new FileReader()
    reader.onload = () => setImage(String(reader.result))
    reader.readAsDataURL(file)
  }

  // The markdown-only download this replaced is now one entry in ShareMenu,
  // alongside Word, PDF, HTML and plain text.

  const agentDef = AGENTS.find((a) => a.id === conv?.agent) ?? AGENTS[0]

  return (
    <div className={`omx-chat ${listOpen ? 'with-list' : ''} ${artifact ? 'with-art' : ''}`}>
      {/* Conversation list. A drawer rather than a permanent column: the rail
          is already 224px and a second persistent list beside it leaves the
          conversation itself in half the window. */}
      <aside className="omx-chat-list">
        <div className="omx-chat-listhead">
          <span className="omx-label">Conversations</span>
          <button className="omx-btn tiny" onClick={() => { newConversation(); setListOpen(false) }}>
            <IconPlus size={12} /> New
          </button>
        </div>
        <div className="omx-chat-convs">
          {conversations.map((c) => (
            <ConversationRow
              key={c.id} c={c} active={c.id === activeId}
              onOpen={() => { openConversation(c.id); setListOpen(false) }}
            />
          ))}
        </div>
      </aside>

      <div className="omx-chat-main">
        <div className="omx-chat-bar">
          <button className={`omx-btn tiny ${listOpen ? 'on' : ''}`}
                  onClick={() => setListOpen((v) => !v)}>
            {conversations.length} chat{conversations.length === 1 ? '' : 's'}
          </button>
          <button className="omx-btn tiny" onClick={() => { newConversation(); boxRef.current?.focus() }}
                  title="New chat — Ctrl+Shift+O">
            <IconPlus size={12} /> New
          </button>

          <div style={{ flex: 1 }} />

          {/* Two independent controls: the agent decides which prompt and
              tools run, the model decides what generates the text. Both
              default to Auto. */}
          <label className="omx-chat-agent" title={agentDef.hint}>
            <select
              value={conv?.agent ?? 'auto'}
              onChange={(e) => setAgent(e.target.value as AgentId)}
            >
              {AGENTS.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </label>
          <ModelPicker
            value={conv?.model ?? AUTO_MODEL}
            onChange={setModel}
            disabled={busy}
          />
          {voiceReady && (
            <button className="omx-btn tiny" onClick={() => setVoiceOpen(true)}
                    title="Talk to OMNIX and be answered aloud">
              <IconConverse size={12} /> Voice
            </button>
          )}
          {messages.length > 0 && (
            // The whole conversation, in any format. The per-message menu
            // exports one answer; this one exports the transcript.
            <ShareMenu
              markdown={conv ? asMarkdown(conv) : ''}
              title={conv?.title || 'OMNIX conversation'}
              subtitle={`${messages.length} messages`}
            />
          )}
        </div>

        <div className="omx-chat-scroll" ref={scrollRef}>
          {messages.length === 0 ? (
            <div className="omx-chat-empty">
              <div className="glyph"><IconAsk size={30} /></div>
              <h2>What are you working on?</h2>
              <p>
                Ask anything. REVORA routes each question to the model that suits
                it — reasoning, code, or a research agent that searches the web
                and shows you what it read.
              </p>

              {/* The one thing kept from the old landing page: what this Space
                  actually holds. It is context for the conversation, not a
                  menu — every count here is a real number from /api/summary. */}
              {summary && summary.objects > 0 && (
                <div className="omx-chat-space">
                  <span className="omx-label">{space?.name}</span>
                  <span>
                    <b>{summary.objects}</b> objects · <b>{summary.relationships}</b> links
                    · <b>{summary.claims}</b> claims · <b>{summary.sources}</b> sources
                  </span>
                </div>
              )}

              <div className="omx-chat-starters">
                {STARTERS.map((s) => (
                  <button key={s} className="omx-pill click"
                          onClick={() => { setDraft(s); boxRef.current?.focus() }}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <Turn
                key={m.id} m={m} busy={busy}
                isLast={i === messages.length - 1}
                openArtifactId={artifact?.id ?? null}
                onArtifact={setArtifact}
                voiceReady={voiceReady}
              />
            ))
          )}
        </div>

        {!pinned && messages.length > 0 && (
          <button
            className="omx-chat-jump"
            onClick={() => { setPinned(true) }}
            title="Jump to the latest"
          >↓ Latest</button>
        )}

        <div className="omx-chat-compose">
          {image && (
            <div className="omx-chat-attached">
              <img src={image} alt="Attachment" />
              <button onClick={() => setImage(null)} title="Remove">
                <IconClose size={12} />
              </button>
              <span className="omx-null">Sent to the vision model</span>
            </div>
          )}

          <div
            className="omx-chat-box"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); attach(e.dataTransfer.files?.[0]) }}
          >
            <button
              className="omx-chat-attach"
              onClick={() => fileRef.current?.click()}
              title="Attach an image"
              disabled={busy}
            ><IconAttach size={16} /></button>
            <input
              ref={fileRef} type="file" accept="image/*" hidden
              onChange={(e) => { attach(e.target.files?.[0]); e.target.value = '' }}
            />

            <textarea
              ref={boxRef}
              value={draft}
              rows={1}
              placeholder={busy ? 'Answering…' : 'Ask anything. ↵ to send, ⇧↵ for a new line.'}
              onChange={(e) => setDraft(e.target.value)}
              onPaste={(e) => {
                const img = [...e.clipboardData.items]
                  .find((it) => it.type.startsWith('image/'))
                if (img) attach(img.getAsFile() ?? undefined)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
                // Up-arrow on an empty box recalls the last thing you said, the
                // way a shell does. Only when empty, so it never eats a
                // cursor movement inside real text.
                if (e.key === 'ArrowUp' && !draft) {
                  const last = [...messages].reverse().find((m) => m.role === 'user')
                  if (last) { e.preventDefault(); setDraft(last.text) }
                }
              }}
            />

            {busy ? (
              <button className="omx-chat-send stop" onClick={() => stop()}
                      title="Stop generating — Esc">
                <IconStop size={15} />
              </button>
            ) : (
              <button className="omx-chat-send" onClick={submit}
                      disabled={!draft.trim() && !image}
                      title="Send — ↵">
                <IconSend size={16} />
              </button>
            )}
          </div>

          <div className="omx-chat-foot">
            <span className="omx-null">
              <IconAgents size={11} /> {agentDef.label} · {agentDef.hint}
            </span>
            <span className="omx-null">
              Models can be wrong. Claims that need evidence belong in Research.
            </span>
          </div>
        </div>
      </div>

      {artifact && (
        <ArtifactPanel a={artifact} onClose={() => setArtifact(null)} />
      )}

      {voiceOpen && (
        <VoiceMode
          onClose={() => setVoiceOpen(false)}
          // Routed through the normal send path, so a spoken turn lands in the
          // same conversation with the same model and agent as a typed one and
          // is still there after voice mode closes.
          onAsk={(text) => sendAndWait(text)}
        />
      )}

      {listOpen && (
        <div className="omx-chat-scrim" onClick={() => setListOpen(false)} />
      )}
    </div>
  )
}
