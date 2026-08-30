// The model roster, shared by the Home picker and the Settings toggles.
//
// Its own tiny store rather than a slice of `store/workspace.ts`, for the same
// reason `lib/auth.ts` is: that store's `init()` loads workspaces, ontology and
// the graph, and the roster is needed before and independently of all of it.
// It is also read by two unrelated surfaces, so it cannot live inside either.
//
// One fetch is shared across every subscriber — the picker and the settings
// screen mounting together must not produce two requests, and more importantly
// must not disagree about which models are on.

import { useSyncExternalStore } from 'react'

export interface ModelCard {
  id: string
  model: string
  label: string
  vendor: string
  role: string
  roleLabel: string
  license: string
  params: string
  blurb: string
  ttft: number
  locked: boolean
  enabled: boolean
  isDefault: boolean
}

export interface RoleGroup { id: string; label: string; models: ModelCard[] }

interface RosterState {
  models: ModelCard[]
  roles: RoleGroup[]
  auto: boolean
  /** Set by OMNIX_CHAT_MODEL — the picker is being overridden for this run. */
  pinned: string | null
  loaded: boolean
  error: string | null
}

const EMPTY: RosterState = {
  models: [], roles: [], auto: true, pinned: null, loaded: false, error: null,
}

let state: RosterState = EMPTY
const listeners = new Set<() => void>()

function commit(next: Partial<RosterState>) {
  state = { ...state, ...next }
  for (const fn of listeners) fn()
}

const subscribe = (fn: () => void) => {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function useRoster(): RosterState {
  return useSyncExternalStore(subscribe, () => state, () => state)
}

export const rosterState = () => state

let inflight: Promise<void> | null = null

/** Load the roster once. Concurrent callers share the same request. */
export function loadRoster(force = false): Promise<void> {
  if (!force && (state.loaded || inflight)) return inflight ?? Promise.resolve()
  inflight = fetch('/api/roster', { credentials: 'include' })
    .then(async (r) => {
      if (!r.ok) throw new Error(`roster ${r.status}`)
      return r.json() as Promise<Omit<RosterState, 'loaded' | 'error'>>
    })
    .then((d) => {
      commit({
        models: d.models ?? [], roles: d.roles ?? [],
        auto: d.auto ?? true, pinned: d.pinned ?? null,
        loaded: true, error: null,
      })
    })
    .catch((e: unknown) => {
      // A roster that will not load must not take the composer with it. The
      // picker falls back to "Auto", which is what it defaults to anyway.
      commit({ loaded: true, error: e instanceof Error ? e.message : 'failed' })
    })
    .finally(() => { inflight = null })
  return inflight
}

/** Flip a model on or off. Optimistic, with the server's answer as the truth.
 *
 *  Optimistic because the switch is the whole interaction — a toggle that waits
 *  ~200ms to move reads as broken and gets clicked twice. The response replaces
 *  the whole roster, so a rejected change (a locked model, say) snaps back
 *  rather than leaving the UI claiming something the server did not do. */
export async function setModelEnabled(id: string, on: boolean): Promise<void> {
  const before = state.models
  commit({ models: state.models.map((m) => (m.id === id ? { ...m, enabled: on } : m)) })
  try {
    const r = await fetch('/api/roster/prefs', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: { [id]: on } }),
    })
    if (!r.ok) throw new Error(String(r.status))
    const d = await r.json()
    commit({ models: d.models ?? [], roles: d.roles ?? [], auto: d.auto ?? true })
  } catch {
    commit({ models: before, error: 'Could not save that change.' })
  }
}

export async function setAuto(on: boolean): Promise<void> {
  const before = state.auto
  commit({ auto: on })
  try {
    const r = await fetch('/api/roster/prefs', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto: on }),
    })
    if (!r.ok) throw new Error(String(r.status))
    const d = await r.json()
    commit({ models: d.models ?? [], roles: d.roles ?? [], auto: d.auto ?? on })
  } catch {
    commit({ auto: before, error: 'Could not save that change.' })
  }
}

export interface ModelHealth { ok: boolean | null; ms?: number; error?: string }

/** Probe every model. Costs one inference call each, so it is never automatic. */
export async function probeHealth(): Promise<Record<string, ModelHealth>> {
  const r = await fetch('/api/roster/health', { credentials: 'include' })
  if (!r.ok) throw new Error(`health ${r.status}`)
  const d = await r.json()
  return (d.results ?? {}) as Record<string, ModelHealth>
}

/** The models a picker should offer: enabled ones only.
 *
 *  A disabled model is not shown rather than shown-and-greyed. The switch lives
 *  in Settings and its purpose is to shorten this list; leaving the entry
 *  visible would defeat the only thing turning it off does. */
export const selectable = (s: RosterState): ModelCard[] =>
  s.models.filter((m) => m.enabled)
