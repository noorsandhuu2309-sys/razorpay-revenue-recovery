// The account session: who is signed in, and the calls that change that.
//
// Kept out of the workspace store on purpose. The store's `init()` fetches
// workspaces, ontology, graph and thread — every one of which is behind the
// gate — so it cannot run until a session exists. Auth has to resolve first and
// independently, which means it needs its own tiny store rather than a slice of
// one that depends on it.
//
// Every request here sends cookies. `fetch` already does for same-origin, but
// it is stated explicitly because the SPA is also served from `file://` in one
// packaging path, where the default would silently drop the session.

import { useSyncExternalStore } from 'react'

export interface AuthUser {
  name: string
  email: string
  initials: string
  created: number | null
  last_login: number | null
}

export interface AuthState {
  authenticated: boolean
  user: AuthUser | null
  /** False on a machine that has never had an account — the screen opens on
   *  signup rather than asking for credentials that cannot exist yet. */
  hasAccounts: boolean
  /** Whether the server enforces the gate at all (OMNIX_AUTH). */
  required: boolean
  inviteRequired: boolean
  /** The gate is on and accepting any password (OMNIX_AUTH=demo). The screen
   *  says so in small type — a lock that opens for any key is worth showing,
   *  but not worth letting anyone mistake for a real one. */
  demo: boolean
  /** Null until the first `/api/auth/me` lands. The shell renders nothing while
   *  it is null: flashing the login screen at a signed-in user for one frame is
   *  worse than a blank one. */
  ready: boolean
}

const EMPTY: AuthState = {
  authenticated: false, user: null, hasAccounts: false,
  required: true, inviteRequired: false, demo: false, ready: false,
}

let state: AuthState = EMPTY
const listeners = new Set<() => void>()

function set(next: Partial<AuthState>) {
  state = { ...state, ...next }
  for (const fn of listeners) fn()
}

/** Thrown with the server's own message, which `omnix/auth.py` has already
 *  vetted as safe to show. `field` lets the form highlight the right input. */
export class AuthFailure extends Error {
  field: string | null
  status: number
  constructor(message: string, field: string | null, status: number) {
    super(message)
    this.field = field
    this.status = status
  }
}

async function post<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({})) as Record<string, unknown>
  if (!res.ok) {
    throw new AuthFailure(
      String(data.error || `Something went wrong (${res.status}).`),
      (data.field as string) ?? null, res.status)
  }
  return data as T
}

/** Ask the server who we are. Safe to call repeatedly. */
export async function refresh(): Promise<AuthState> {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'include' })
    const data = await res.json() as Omit<AuthState, 'ready'>
    set({ ...data, ready: true })
  } catch {
    // The server being unreachable is not "signed out" — but the app cannot
    // work either way, and showing the gate is the honest failure: it is the
    // one screen that tells the user to establish a connection.
    set({ ...EMPTY, ready: true })
  }
  return state
}

export async function signIn(email: string, password: string, keep: boolean) {
  const r = await post<{ user: AuthUser }>('/api/auth/login', { email, password, keep })
  set({ authenticated: true, user: r.user, hasAccounts: true })
}

export async function signUp(
  name: string, email: string, password: string, keep: boolean, invite = '',
) {
  const r = await post<{ user: AuthUser }>(
    '/api/auth/signup', { name, email, password, keep, invite })
  set({ authenticated: true, user: r.user, hasAccounts: true })
}

export async function signOut() {
  try { await post('/api/auth/logout') } catch { /* already gone */ }
  set({ authenticated: false, user: null })
}

export async function forgot(email: string) {
  return post<{ sent: boolean; note: string; devToken: string | null }>(
    '/api/auth/forgot', { email })
}

export async function resetPassword(token: string, password: string) {
  const r = await post<{ user: AuthUser }>('/api/auth/reset', { token, password })
  set({ authenticated: true, user: r.user, hasAccounts: true })
}

export async function rename(name: string) {
  const r = await post<{ user: AuthUser }>('/api/auth/profile', { name }, 'PATCH')
  set({ user: r.user })
}

export async function changePassword(current: string, next: string) {
  await post('/api/auth/password', { current, new: next })
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

const snapshot = () => state

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Imperative read, for code outside a component. */
export const authState = () => state
