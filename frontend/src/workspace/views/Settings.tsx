// Settings — account, appearance, Spaces, and what this build actually is.
//
// The rule this view is written to: every control here has to DO something. A
// settings screen is where a product's honesty is easiest to check, because a
// toggle that stores a preference nothing reads is invisible until someone
// relies on it. So there is no notifications section (nothing sends any), no
// "data export" button (Outputs is the export surface and it works), and no
// telemetry switch (there is no telemetry). What is here is what exists.
//
// Sign out is the one control with a consequence outside this screen: it drops
// the server session and the shell falls back to the gate on the next auth
// snapshot. That fallback lives in Workspace.tsx rather than here — a view that
// unmounted itself by navigating would race its own state update.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import {
  ACCENTS, setAccent, setMode, useAppearance, type AccentId,
} from '../lib/appearance'
import {
  changePassword, rename, signOut, useAuth, AuthFailure,
} from '../lib/auth'
import {
  loadRoster, probeHealth, setAuto, setModelEnabled, useRoster,
  type ModelHealth,
} from '../lib/models'
import { useWorkspace } from '../store/workspace'
import { IconMoon, IconSpace, IconSun } from '../components/Icons'
import '../assistant.css'

const MIN_PASSWORD = 12

/** A saved/failed flash beside a control, so a change that took has visible
 *  proof and a change that did not is not silently swallowed. */
type Flash = { kind: 'ok' | 'bad'; text: string } | null

function Section({ title, hint, wide, children }: {
  title: string; hint?: string; wide?: boolean; children: React.ReactNode
}) {
  return (
    <section className={`omx-set-sec ${wide ? 'wide' : ''}`}>
      <div className="omx-set-sechead">
        <h2>{title}</h2>
        {hint && <span className="omx-label">{hint}</span>}
      </div>
      {children}
    </section>
  )
}

/** A labelled switch. `locked` renders it on and inert with the reason as the
 *  tooltip — a control that silently ignores a click is worse than one that
 *  visibly cannot be clicked. */
function Toggle({ on, onChange, label, locked, lockedReason }: {
  on: boolean; onChange: (v: boolean) => void; label: string
  locked?: boolean; lockedReason?: string
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={locked ? lockedReason : label}
      disabled={locked}
      className={`omx-switch ${on ? 'on' : ''} ${locked ? 'locked' : ''}`}
      onClick={() => !locked && onChange(!on)}
    >
      <span className="knob" />
    </button>
  )
}

function ModelsSection() {
  const roster = useRoster()
  const [health, setHealth] = useState<Record<string, ModelHealth> | null>(null)
  const [probing, setProbing] = useState(false)

  useEffect(() => { void loadRoster() }, [])

  const runProbe = async () => {
    setProbing(true)
    try {
      setHealth(await probeHealth())
    } catch {
      setHealth(null)
    } finally {
      setProbing(false)
    }
  }

  const onCount = roster.models.filter((m) => m.enabled).length

  return (
    <Section title="Models" wide
             hint={roster.loaded
               ? `${onCount} of ${roster.models.length} on · open weights on NVIDIA NIM`
               : 'loading…'}>
      <Row label="Auto routing"
           hint="REVORA reads each question and picks the model for it — code and maths to the reasoning model, images to vision, research to the 120B. Turn this off to always use whatever is chosen in the chat composer.">
        <div className="omx-set-inline">
          <Toggle on={roster.auto} onChange={(v) => void setAuto(v)}
                  label="Automatic model routing" />
          <span className="omx-null">{roster.auto ? 'On' : 'Off'}</span>
        </div>
      </Row>

      {roster.pinned && (
        <div className="omx-set-warn">
          This run is pinned to one model by the <code>OMNIX_CHAT_MODEL</code>
          {' '}environment variable, so the switches below have no effect until
          it is unset.
        </div>
      )}

      <div className="omx-models">
        {roster.roles.map((g) => (
          <div className="omx-models-group" key={g.id}>
            <div className="omx-label">{g.label}</div>
            {g.models.map((m) => {
              const h = health?.[m.id]
              return (
                <div className={`omx-mrcard ${m.enabled ? '' : 'off'}`} key={m.id}>
                  <div className="hd">
                    <span className="nm">{m.label}</span>
                    {m.isDefault && <span className="omx-pill xs">Default</span>}
                    {h && (
                      <span className={`omx-pill xs ${h.ok ? 'ok' : h.ok === null ? '' : 'bad'}`}
                            title={h.error ?? ''}>
                        {h.ok ? `${h.ms}ms` : h.ok === null ? 'unknown' : 'no answer'}
                      </span>
                    )}
                    <Toggle
                      on={m.enabled}
                      onChange={(v) => void setModelEnabled(m.id, v)}
                      label={`${m.label} enabled`}
                      locked={m.locked}
                      lockedReason="This is the fallback model — it answers when
                                    every other model is cold, so it cannot be
                                    switched off."
                    />
                  </div>
                  <p className="bl">{m.blurb}</p>
                  <div className="ft omx-mono">
                    <span>{m.model}</span>
                    <span>{m.vendor} · {m.params}</span>
                    <span className="lic">{m.license}</span>
                    <span>~{m.ttft.toFixed(1)}s first token</span>
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="omx-set-inline" style={{ marginTop: 12 }}>
        <button className="omx-btn" onClick={() => void runProbe()} disabled={probing}>
          {probing ? <span className="omx-spin" /> : 'Test all models'}
        </button>
        <span className="omx-null">
          Sends a one-token request to each. Costs a real call, so it is never
          automatic.
        </span>
      </div>
    </Section>
  )
}

function Row({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div className="omx-set-row">
      <div className="omx-set-rowlabel">
        <span className="n">{label}</span>
        {hint && <span className="h">{hint}</span>}
      </div>
      <div className="omx-set-rowctl">{children}</div>
    </div>
  )
}

export function SettingsView() {
  const auth = useAuth()
  const { mode, accent } = useAppearance()

  const workspaces = useWorkspace((s) => s.workspaces)
  const workspaceId = useWorkspace((s) => s.workspaceId)
  const setWorkspace = useWorkspace((s) => s.setWorkspace)
  const summary = useWorkspace((s) => s.summary)

  const [name, setName] = useState(auth.user?.name ?? '')
  const [nameFlash, setNameFlash] = useState<Flash>(null)

  const [pwCurrent, setPwCurrent] = useState('')
  const [pwNext, setPwNext] = useState('')
  const [pwFlash, setPwFlash] = useState<Flash>(null)
  const [pwBusy, setPwBusy] = useState(false)

  const [newSpace, setNewSpace] = useState('')
  const [spaceFlash, setSpaceFlash] = useState<Flash>(null)

  const [health, setHealth] = useState<Record<string, unknown> | null>(null)

  // Re-seed when the signed-in user changes underneath us (a sign-out and back
  // in without a reload). Guarded on the field being untouched, so this cannot
  // overwrite something the user is halfway through typing.
  useEffect(() => {
    setName((n) => (n === '' ? auth.user?.name ?? '' : n))
  }, [auth.user?.name])

  useEffect(() => {
    fetch('/api/health', { credentials: 'include' })
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  async function saveName() {
    const next = name.trim()
    if (!next || next === auth.user?.name) return
    try {
      await rename(next)
      setNameFlash({ kind: 'ok', text: 'Saved' })
    } catch (e) {
      setNameFlash({
        kind: 'bad',
        text: e instanceof AuthFailure ? e.message : 'Could not save.',
      })
    }
    setTimeout(() => setNameFlash(null), 2600)
  }

  async function savePassword() {
    if (pwBusy) return
    setPwBusy(true)
    try {
      await changePassword(pwCurrent, pwNext)
      setPwCurrent(''); setPwNext('')
      setPwFlash({ kind: 'ok', text: 'Password changed. Other sessions signed out.' })
    } catch (e) {
      setPwFlash({
        kind: 'bad',
        text: e instanceof AuthFailure ? e.message : 'Could not change it.',
      })
    } finally {
      setPwBusy(false)
      setTimeout(() => setPwFlash(null), 4000)
    }
  }

  async function createSpace() {
    const n = newSpace.trim()
    if (!n) return
    try {
      const ws = await api.createWorkspace(n)
      setNewSpace('')
      // Re-read the list from the server rather than appending locally: the
      // response is one workspace, and the rail's counts come from a separate
      // call that has to be made anyway.
      const { workspaces: fresh } = await api.workspaces()
      useWorkspace.setState({ workspaces: fresh })
      await setWorkspace(ws.id)
      setSpaceFlash({ kind: 'ok', text: `Created "${n}".` })
    } catch (e) {
      setSpaceFlash({
        kind: 'bad',
        text: e instanceof Error ? e.message : 'Could not create it.',
      })
    }
    setTimeout(() => setSpaceFlash(null), 3000)
  }

  const created = auth.user?.created
    ? new Date(auth.user.created * 1000).toLocaleDateString()
    : null

  return (
    <div className="omx-scroll omx-settings">
      <header className="omx-set-hero">
        <div className="omx-label">Settings</div>
        <h1>Your account and this workspace</h1>
      </header>

      <Section title="Account"
               hint={auth.required ? 'signed in' : 'gate disabled (OMNIX_AUTH=off)'}>
        {auth.user ? (
          <>
            <div className="omx-set-id">
              <span className="omx-set-avatar">{auth.user.initials}</span>
              <div>
                <div className="n">{auth.user.name}</div>
                <div className="e">{auth.user.email}</div>
                {created && <div className="c">Account created {created}</div>}
              </div>
            </div>

            <Row label="Display name" hint="What REVORA calls you throughout the app">
              <div className="omx-set-inline">
                <input
                  className="omx-set-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void saveName() }}
                />
                <button className="omx-btn" onClick={() => void saveName()}
                        disabled={!name.trim() || name.trim() === auth.user.name}>
                  Save
                </button>
                {nameFlash && (
                  <span className={`omx-set-flash ${nameFlash.kind}`}>
                    {nameFlash.text}
                  </span>
                )}
              </div>
            </Row>

            <Row label="Password"
                 hint={`At least ${MIN_PASSWORD} characters. Changing it signs out every other session.`}>
              <div className="omx-set-inline wrap">
                <input
                  className="omx-set-input" type="password"
                  placeholder="Current password" autoComplete="current-password"
                  value={pwCurrent}
                  onChange={(e) => setPwCurrent(e.target.value)}
                />
                <input
                  className="omx-set-input" type="password"
                  placeholder="New password" autoComplete="new-password"
                  value={pwNext}
                  onChange={(e) => setPwNext(e.target.value)}
                />
                <button
                  className="omx-btn"
                  onClick={() => void savePassword()}
                  disabled={pwBusy || !pwCurrent || pwNext.length < MIN_PASSWORD}
                >{pwBusy ? <span className="omx-spin" /> : 'Change'}</button>
                {pwFlash && (
                  <span className={`omx-set-flash ${pwFlash.kind}`}>{pwFlash.text}</span>
                )}
              </div>
            </Row>

            <Row label="Session"
                 hint="Signing out drops the session on the server and returns you to the sign-in screen.">
              <button className="omx-btn danger" onClick={() => void signOut()}>
                Sign out
              </button>
            </Row>
          </>
        ) : (
          <div className="omx-set-anon">
            <p>
              No one is signed in. The gate is
              {auth.required ? ' on' : ' off for this run (OMNIX_AUTH=off)'},
              so the workspace is reachable without an account.
            </p>
            <button className="omx-btn primary" onClick={() => void signOut()}>
              Go to sign in
            </button>
          </div>
        )}
      </Section>

      <Section title="Appearance" hint="two axes, ten combinations">
        <Row label="Accent" hint="Painted from the theme ramp — what you see here is what the shell uses">
          <div className="omx-swatches" role="radiogroup" aria-label="Accent">
            {ACCENTS.map((a) => (
              <button
                key={a.id}
                data-accent={a.id}
                className={`omx-swatch ${a.id === accent ? 'on' : ''}`}
                onClick={() => setAccent(a.id as AccentId)}
                role="radio"
                aria-checked={a.id === accent}
                title={`${a.label} — ${a.hint}`}
                aria-label={a.label}
              />
            ))}
          </div>
        </Row>
        <Row label="Ground" hint="Dark or light. Applies to the map and graph canvases too.">
          <div className="omx-modes" role="radiogroup" aria-label="Ground">
            <button className={`omx-mode-btn ${mode === 'dark' ? 'on' : ''}`}
                    onClick={() => setMode('dark')}
                    role="radio" aria-checked={mode === 'dark'}>
              <IconMoon size={13} /> Dark
            </button>
            <button className={`omx-mode-btn ${mode === 'light' ? 'on' : ''}`}
                    onClick={() => setMode('light')}
                    role="radio" aria-checked={mode === 'light'}>
              <IconSun size={13} /> Light
            </button>
          </div>
        </Row>
      </Section>

      <ModelsSection />

      <Section title="Spaces" hint={`${workspaces.length}`}>
        <div className="omx-set-spaces">
          {workspaces.map((w) => (
            <button
              key={w.id}
              className={`omx-set-space ${w.id === workspaceId ? 'on' : ''}`}
              onClick={() => { void setWorkspace(w.id) }}
            >
              <span className="ic"><IconSpace size={15} /></span>
              <span className="bd">
                <span className="n">{w.name}</span>
                <span className="d">{w.description || 'No description'}</span>
              </span>
              {w.id === workspaceId && summary && (
                <span className="omx-mono ct">{summary.objects} objects</span>
              )}
            </button>
          ))}
        </div>
        <Row label="New Space" hint="A separate object graph, claim ledger and conversation">
          <div className="omx-set-inline">
            <input
              className="omx-set-input"
              placeholder="e.g. Semiconductor supply chain"
              value={newSpace}
              onChange={(e) => setNewSpace(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void createSpace() }}
            />
            <button className="omx-btn" onClick={() => void createSpace()}
                    disabled={!newSpace.trim()}>Create</button>
            {spaceFlash && (
              <span className={`omx-set-flash ${spaceFlash.kind}`}>{spaceFlash.text}</span>
            )}
          </div>
        </Row>
      </Section>

      <Section title="This build" wide hint="read from the server, not hardcoded">
        <div className="omx-set-facts">
          {[
            ['Models reachable',
              health ? String((health.models as unknown[] | undefined)?.length
                ?? health.model ?? '—') : '…'],
            ['Server', health ? 'reachable' : 'unreachable'],
            ['Auth gate', auth.required ? 'enforced' : 'off'],
            ['Storage', 'local SQLite + JSON, on this machine'],
          ].map(([k, v]) => (
            <div className="f" key={k}>
              <span className="k">{k}</span>
              <span className="v">{v}</span>
            </div>
          ))}
        </div>
        <p className="omx-set-fine">
          REVORA runs on 127.0.0.1 with no TLS. Passwords are stretched with
          scrypt and stored only as hashes; session tokens are stored only as
          digests. Nothing here is sent anywhere except to the model providers
          you have configured keys for.
        </p>
      </Section>
    </div>
  )
}
