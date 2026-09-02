// The Home chatbot's engine: conversations, streaming, and persistence.
//
// Separate from `store/workspace.ts` on purpose. That store is the object graph
// and its selection bus — every view subscribes to it, so putting a
// token-by-token stream in it would re-render the Graph canvas, the Inspector
// and the rail on every delta. This store is subscribed to by exactly one view.
//
// Streaming is read with `fetch` + a ReadableStream reader rather than
// `EventSource`, for two reasons that both matter:
//
//   * EventSource cannot POST. The endpoint takes a message array, an agent
//     override and an optional inline image; none of that fits in a query
//     string, and a base64 image certainly does not.
//   * EventSource cannot be aborted mid-response in a way the server sees.
//     "Stop generating" has to actually stop the generation, not just stop
//     rendering it — an abortable fetch closes the socket and the server's
//     generator terminates.
//
// Conversations are stored in localStorage. There is no server-side thread for
// this surface, deliberately: NOVA's thread (`/api/nova/thread`) is scoped to a
// Space and is a record of work done ON that Space. This is a general
// assistant, and filing every "explain closures to me" into the geopolitical
// intelligence workspace's audit trail would poison the one record that is
// supposed to mean something.

import { useSyncExternalStore } from 'react'

export type Role = 'user' | 'assistant'

/** The agents `/api/chat` will route to. `auto` is the absence of an override,
 *  which lets the server's own classifier pick — it is right most of the time
 *  and being able to see WHICH it picked (the `agent` on the reply) is more
 *  useful than choosing up front. */
export const AGENTS = [
  { id: 'auto', label: 'Auto', hint: 'REVORA picks the right model for the question' },
  { id: 'chat', label: 'General', hint: 'Conversation, writing, explanation' },
  { id: 'reasoning', label: 'Reasoning', hint: 'Step-by-step analysis and maths' },
  { id: 'coding', label: 'Code', hint: 'Writing, reviewing and debugging code' },
  { id: 'research', label: 'Research', hint: 'Searches the web and cites what it used' },
] as const

export type AgentId = (typeof AGENTS)[number]['id']

/** The model override for a conversation.
 *
 *  `auto` — the default and the recommended setting — sends no model at all and
 *  lets the server pick from the roster by role. Anything else is a catalogue
 *  id from `/api/roster`.
 *
 *  Model choice and agent choice are INDEPENDENT: the agent decides which
 *  prompt and tools run (Research still searches the web), the model decides
 *  what generates the text. Collapsing them into one control would make
 *  "research with the fast model" unexpressible. */
export const AUTO_MODEL = 'auto'
export type ModelChoice = string

export interface Source { n: number; title: string; url: string }

export interface Message {
  id: string
  role: Role
  text: string
  /** Which agent handled it, and on which model. Assistant turns only, and
   *  only once the server has said so — never guessed. */
  agent?: string
  model?: string
  sources?: Source[]
  error?: string
  /** Data URL of an image the user attached. */
  image?: string
  createdAt: number
  /** Set while this turn is being streamed into. */
  streaming?: boolean
  /** Set when the user pressed Stop. The text kept is what arrived. */
  stopped?: boolean
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
  agent: AgentId
  /** Model override. Absent on conversations saved before the picker existed,
   *  which is why every read of it defaults to `AUTO_MODEL` rather than
   *  assuming the field is there. */
  model?: ModelChoice
  createdAt: number
  updatedAt: number
}

interface ChatState {
  conversations: Conversation[]
  activeId: string
  /** True while a response is streaming. One at a time, by design: a second
   *  concurrent stream would land in whichever conversation happened to be
   *  open when it finished. */
  busy: boolean
}

const KEY = 'omx.chat.v1'
const MAX_CONVERSATIONS = 60

const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`

export function blankConversation(): Conversation {
  const now = Date.now()
  return {
    id: uid(), title: 'New chat', messages: [], agent: 'auto',
    model: AUTO_MODEL, createdAt: now, updatedAt: now,
  }
}

function load(): ChatState {
  const fresh = blankConversation()
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { conversations: [fresh], activeId: fresh.id, busy: false }
    const parsed = JSON.parse(raw) as Partial<ChatState>
    const list = Array.isArray(parsed.conversations) ? parsed.conversations : []
    // A conversation whose messages field is not an array would throw on the
    // first render. Storage is user-writable and survives across builds, so it
    // is validated rather than trusted.
    const clean = list.filter((c): c is Conversation =>
      !!c && typeof c.id === 'string' && Array.isArray(c.messages))
    if (!clean.length) return { conversations: [fresh], activeId: fresh.id, busy: false }
    // `busy` is never restored. A stream cannot survive a reload, and a
    // restored `true` leaves the composer disabled with nothing to finish it.
    const activeId = clean.some((c) => c.id === parsed.activeId)
      ? parsed.activeId! : clean[0].id
    return { conversations: clean, activeId, busy: false }
  } catch {
    return { conversations: [fresh], activeId: fresh.id, busy: false }
  }
}

let state: ChatState = load()
const listeners = new Set<() => void>()

function commit(next: Partial<ChatState>, persist = true) {
  state = { ...state, ...next }
  if (persist) {
    try {
      // `streaming` is a live flag, not content: persisting it would restore a
      // message that renders a caret forever.
      localStorage.setItem(KEY, JSON.stringify({
        activeId: state.activeId,
        conversations: state.conversations.slice(0, MAX_CONVERSATIONS).map((c) => ({
          ...c,
          messages: c.messages.map(({ streaming: _s, ...m }) => m),
        })),
      }))
    } catch { /* private mode or quota — the session still works in memory */ }
  }
  for (const fn of listeners) fn()
}

const subscribe = (fn: () => void) => {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

const snapshot = () => state

export function useChat(): ChatState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

export const chatState = () => state

export function activeConversation(): Conversation {
  return state.conversations.find((c) => c.id === state.activeId)
    ?? state.conversations[0]
}

function patchActive(fn: (c: Conversation) => Conversation, persist = true) {
  commit({
    conversations: state.conversations.map(
      (c) => (c.id === state.activeId ? fn(c) : c)),
  }, persist)
}

/** A title from the first thing the user said.
 *
 *  Taken from the user's own words rather than asked of a model: a title is
 *  navigation, it has to appear the instant the message is sent, and spending
 *  a model call plus a round trip on six words is the wrong trade. */
function titleFrom(text: string): string {
  const one = text.replace(/\s+/g, ' ').trim()
  if (one.length <= 42) return one || 'New chat'
  return `${one.slice(0, 41).replace(/\s\S*$/, '')}…`
}

// ---------------------------------------------------------------------------
// Conversation management
// ---------------------------------------------------------------------------
export function newConversation(): string {
  const c = blankConversation()
  commit({ conversations: [c, ...state.conversations], activeId: c.id })
  return c.id
}

export function openConversation(id: string) {
  if (state.conversations.some((c) => c.id === id)) commit({ activeId: id })
}

export function renameConversation(id: string, title: string) {
  const t = title.trim()
  if (!t) return
  commit({
    conversations: state.conversations.map(
      (c) => (c.id === id ? { ...c, title: t } : c)),
  })
}

export function deleteConversation(id: string) {
  const rest = state.conversations.filter((c) => c.id !== id)
  // Never leave zero conversations: the composer needs somewhere to write, and
  // an empty list would render a view with no target for the next message.
  const conversations = rest.length ? rest : [blankConversation()]
  const activeId = state.activeId === id ? conversations[0].id : state.activeId
  commit({ conversations, activeId })
}

export function setAgent(agent: AgentId) {
  patchActive((c) => ({ ...c, agent }))
}

export function setModel(model: ModelChoice) {
  patchActive((c) => ({ ...c, model }))
}

/** Drop every message after (and including) `id`, so the turn can be replayed.
 *  Returns the removed user text when `id` was a user message. */
function truncateFrom(id: string): string | null {
  const c = activeConversation()
  const at = c.messages.findIndex((m) => m.id === id)
  if (at === -1) return null
  const removed = c.messages[at]
  patchActive((conv) => ({ ...conv, messages: conv.messages.slice(0, at) }))
  return removed.role === 'user' ? removed.text : null
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------
let controller: AbortController | null = null

export function stop() {
  controller?.abort()
  controller = null
}

export const canStop = () => controller !== null

/** Send a turn and stream the reply into the active conversation.
 *
 *  `text` is optional when `resend` is set — regenerating replays the history
 *  that is already there rather than appending the question a second time. */
export async function send(text: string, image?: string): Promise<void> {
  const body = text.trim()
  if ((!body && !image) || state.busy) return

  const user: Message = {
    id: uid(), role: 'user', text: body, image, createdAt: Date.now(),
  }
  patchActive((c) => ({
    ...c,
    title: c.messages.length ? c.title : titleFrom(body),
    messages: [...c.messages, user],
    updatedAt: Date.now(),
  }))
  await stream(image)
}

/** Send a turn and resolve with the finished answer text.
 *
 *  Voice mode needs the answer as a VALUE — it has to read it aloud — where the
 *  screen only needs it to appear in the store. Rather than have the caller
 *  poll the store or subscribe and guess when it settled, the reply is read
 *  back here after the stream closes.
 *
 *  Returns '' if the turn produced nothing, and throws if it errored, so a
 *  hands-free loop can tell "said nothing" from "broke". */
export async function sendAndWait(text: string, image?: string): Promise<string> {
  const before = activeConversation().id
  await send(text, image)

  // The conversation can be switched, deleted or regenerated while a spoken
  // turn is in flight; answering into a conversation the user has left would be
  // wrong, so the result is only read back when we are still where we started.
  const conv = state.conversations.find((c) => c.id === before)
  const last = conv?.messages[conv.messages.length - 1]
  if (!last || last.role !== 'assistant') return ''
  if (last.error) throw new Error(last.error)
  return last.text || ''
}

/** Regenerate the assistant reply to a turn: drop it and everything after,
 *  then re-stream from the history that remains. */
export async function regenerate(assistantId: string): Promise<void> {
  if (state.busy) return
  truncateFrom(assistantId)
  await stream()
}

/** Replace a user message and re-run from there. */
export async function editAndResend(userId: string, next: string): Promise<void> {
  if (state.busy) return
  const body = next.trim()
  if (!body) return
  truncateFrom(userId)
  await send(body)
}

/** The actual SSE read. Everything above funnels here so retry, regenerate and
 *  a plain send cannot drift apart in how they handle errors or aborts. */
async function stream(image?: string): Promise<void> {
  const conv = activeConversation()
  const reply: Message = {
    id: uid(), role: 'assistant', text: '', createdAt: Date.now(),
    streaming: true,
  }
  // Not persisted while streaming: a write per delta would serialise the whole
  // conversation on every token.
  patchActive((c) => ({ ...c, messages: [...c.messages, reply] }), false)
  commit({ busy: true }, false)

  const patchReply = (fn: (m: Message) => Message, persist = false) => {
    patchActive((c) => ({
      ...c,
      messages: c.messages.map((m) => (m.id === reply.id ? fn(m) : m)),
    }), persist)
  }

  controller = new AbortController()
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        messages: conv.messages.map((m) => ({ role: m.role, content: m.text })),
        forced_agent: conv.agent === 'auto' ? null : conv.agent,
        // Auto is the ABSENCE of an override, not a value the server has to
        // know about — same contract as `forced_agent` above.
        forced_model: !conv.model || conv.model === AUTO_MODEL ? null : conv.model,
        image: image ?? null,
      }),
    })

    if (!res.ok || !res.body) {
      const detail = res.status === 401
        ? 'Your session expired. Sign in again.'
        : `The server answered ${res.status}.`
      patchReply((m) => ({ ...m, streaming: false, error: detail }), true)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let acc = ''

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line. A chunk can split one in
      // half, so only complete frames are consumed and the remainder stays in
      // the buffer — parsing per-chunk instead is how a stream loses every
      // token that happened to land on a packet boundary.
      let cut: number
      while ((cut = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, cut)
        buffer = buffer.slice(cut + 2)

        let event = 'message'
        const dataLines: string[] = []
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (!dataLines.length) continue

        let data: Record<string, unknown>
        try { data = JSON.parse(dataLines.join('\n')) } catch { continue }

        if (event === 'meta') {
          // Two meta events arrive: the intended model, then the one that
          // actually answered once a ladder rung wins. The second overwrites
          // the first, which is the whole reason the server sends it.
          patchReply((m) => ({
            ...m,
            agent: String(data.agent ?? m.agent ?? ''),
            model: String(data.model ?? m.model ?? ''),
          }))
        } else if (event === 'delta') {
          acc += String(data.text ?? '')
          patchReply((m) => ({ ...m, text: acc }))
        } else if (event === 'sources') {
          patchReply((m) => ({ ...m, sources: (data.sources as Source[]) ?? [] }))
        } else if (event === 'error') {
          patchReply((m) => ({ ...m, error: String(data.message ?? 'failed') }))
        }
      }
    }
    patchReply((m) => ({ ...m, streaming: false }), true)
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === 'AbortError'
    patchReply((m) => ({
      ...m,
      streaming: false,
      stopped: aborted,
      // A stop is not an error. Labelling it as one tells the user something
      // went wrong when they are the thing that went wrong, on purpose.
      error: aborted ? undefined
        : e instanceof Error ? e.message : 'The connection dropped.',
    }), true)
  } finally {
    controller = null
    commit({ busy: false })
  }
}

/** The conversation as markdown, for the export button. */
export function asMarkdown(c: Conversation): string {
  const lines = [`# ${c.title}`, '', `_${new Date(c.createdAt).toLocaleString()}_`, '']
  for (const m of c.messages) {
    lines.push(m.role === 'user' ? '## You' : `## OMNIX${m.model ? ` · ${m.model}` : ''}`)
    lines.push('')
    lines.push(m.text || (m.error ? `> ${m.error}` : '_(no reply)_'))
    lines.push('')
    if (m.sources?.length) {
      lines.push('Sources:')
      for (const s of m.sources) lines.push(`${s.n}. [${s.title || s.url}](${s.url})`)
      lines.push('')
    }
  }
  return lines.join('\n')
}
