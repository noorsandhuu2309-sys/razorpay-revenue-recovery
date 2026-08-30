// Agents as visible workers (§9).
//
// Selecting a worker shows status, current task, progress, model, token/cost
// usage, sources, artifacts and an audit trail — plus the controls that make it
// interruptible: Pause, Redirect, Cancel.
//
// Two copy rules, both about not overstating what the machinery does:
//
//   * Pause, redirect and cancel are cooperative. The buttons say so, because
//     a "Stop" that leaves a step running for another 20 seconds while the UI
//     claims it stopped is a lie the user catches immediately.
//   * Cost is measured or absent. An unpriced model shows its tokens and a
//     dash, never a plausible-looking number.

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { IconAgents, IconPause, IconPlay, IconStop } from '../components/Icons'
import { ViewIntro } from '../components/ViewIntro'
import type { AgentWorker } from '../lib/types'

const TERMINAL = ['completed', 'failed', 'cancelled']

function duration(ms: number): string {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

function Usage({ w }: { w: AgentWorker }) {
  const u = w.usage
  return (
    <div className="omx-agent-usage">
      <span title="Provider calls made by this run">{u.calls} calls</span>
      <span title="Tokens in / out">
        {u.inputTokens.toLocaleString()} → {u.outputTokens.toLocaleString()} tok
        {u.tokensEstimated && <i title="Estimated from length — the streaming path cannot report usage"> (est)</i>}
      </span>
      <span title="Measured provider cost">
        {u.costUsd > 0 ? `$${u.costUsd.toFixed(4)}` : '— unpriced'}
      </span>
      {!!u.models.length && <span className="omx-mono">{u.models.join(', ')}</span>}
      {!!u.errors && <span className="err">{u.errors} error(s)</span>}
    </div>
  )
}

function Controls({ w, onChanged }: { w: AgentWorker; onChanged: () => void }) {
  const [note, setNote] = useState('')
  const [instruction, setInstruction] = useState('')
  const live = !TERMINAL.includes(w.status)

  if (!live) return null

  // Widened deliberately: only some controls return a `note`, and the ones
  // that do are the ones whose semantics need explaining (pause, redirect,
  // cancel are all cooperative). Resume just resumes.
  const act = async (fn: () => Promise<unknown>) => {
    try {
      const r = await fn() as { note?: string }
      setNote(r.note || 'Done.')
      onChanged()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That control failed.')
    }
  }

  return (
    <div className="omx-agent-controls">
      <div className="row">
        {w.control.paused ? (
          <button className="omx-btn on"
                  onClick={() => void act(() => api.agentResume(w.id))}>
            <IconPlay size={12} /> Resume
          </button>
        ) : (
          <button className="omx-btn"
                  onClick={() => void act(() => api.agentPause(w.id))}>
            <IconPause size={12} /> Pause
          </button>
        )}
        <button className="omx-btn"
                onClick={() => void act(() => api.agentCancel(w.id))}>
          <IconStop size={12} /> Cancel
        </button>
      </div>

      <div className="row">
        <input
          className="omx-input"
          placeholder="Redirect: e.g. ignore gaming GPUs, focus on datacenter"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !instruction.trim()) return
            void act(() => api.agentRedirect(w.id, instruction)).then(
              () => setInstruction(''))
          }}
        />
        <button
          className="omx-btn"
          disabled={!instruction.trim()}
          onClick={() => void act(() => api.agentRedirect(w.id, instruction))
            .then(() => setInstruction(''))}
        >Redirect</button>
      </div>

      {!!w.control.pendingRedirects.length && (
        <div className="omx-label">
          Queued, not yet picked up: {w.control.pendingRedirects.join(' · ')}
        </div>
      )}
      {note && <div className="omx-agent-note">{note}</div>}
    </div>
  )
}

function Worker({ w, onChanged }: { w: AgentWorker; onChanged: () => void }) {
  const openOutput = useWorkspace((s) => s.openOutput)
  const [trail, setTrail] = useState<AgentWorker['trail']>()
  const live = !TERMINAL.includes(w.status)
  const pct = Math.round((w.progress.fraction || 0) * 100)

  return (
    <div className="omx-agent" data-status={w.status}>
      <div className="omx-agent-head">
        <div>
          <div className="omx-agent-title">
            <span className="omx-agent-code">{w.agent.toUpperCase()}</span>
            {w.title || 'Untitled run'}
          </div>
          <div className="omx-label">
            {w.status}{w.control.paused ? ' · paused' : ''} ·
            {' '}{w.progress.completed}/{w.progress.steps} steps ·
            {' '}{duration(w.durationMs)}
            {w.sourceCount ? ` · ${w.sourceCount} sources` : ''}
          </div>
        </div>
        {live && <span className="omx-spin" />}
      </div>

      <div className="omx-agent-progress">
        <span className="fill" style={{ width: `${pct}%` }} />
      </div>

      {w.progress.current && (
        <div className="omx-label">
          Now: {w.progress.current.title}
        </div>
      )}
      {w.error && <div className="omx-agent-note err">{w.error}</div>}

      <Usage w={w} />
      <Controls w={w} onChanged={onChanged} />

      {!!w.artifacts.length && (
        <div className="omx-agent-artifacts">
          <span className="omx-label">Produced</span>
          {w.artifacts.map((a) => (
            <button key={a.id} className="omx-chip"
                    onClick={() => openOutput(a.id)}>{a.title || a.type}</button>
          ))}
        </div>
      )}

      <details
        className="omx-agent-trail"
        onToggle={(e) => {
          if (!(e.currentTarget as HTMLDetailsElement).open || trail) return
          api.agent(w.id).then((full) => setTrail(full.trail)).catch(() => setTrail([]))
        }}
      >
        <summary className="omx-label">Activity trail</summary>
        {!trail && <span className="omx-spin" />}
        {trail?.map((e) => (
          <div className="omx-trail-row" key={e.seq}>
            <span className="omx-mono">{e.type}</span>
            <span>
              {String(e.payload.title || e.payload.stage || e.payload.step
                || e.payload.detail || '')}
            </span>
            <span className="omx-label">
              {new Date(e.ts).toLocaleTimeString()}
            </span>
          </div>
        ))}
        {trail && !trail.length && (
          <div className="omx-label">No recorded steps.</div>
        )}
      </details>
    </div>
  )
}

export function AgentsView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const [data, setData] = useState<{ active: AgentWorker[]; recent: AgentWorker[] }>()
  const [loading, setLoading] = useState(true)
  const timer = useRef<number | null>(null)

  // `alive` is the poll loop's cancellation flag. Without it a reply for the
  // Space the user just left can still land — the loop stops scheduling new
  // ticks, but the request already in flight resolves regardless and writes
  // another Space's workers into this view until the next tick corrects it.
  const load = (alive: () => boolean = () => true) => {
    if (!ws) return
    api.agentsLive(ws)
      .then((d) => { if (alive()) setData(d) })
      .catch(() => { if (alive()) setData({ active: [], recent: [] }) })
      .finally(() => { if (alive()) setLoading(false) })
  }

  // Busy-ness is read through a ref rather than a dependency: keying the
  // effect on it would tear down and re-run the poll on every response, and
  // re-running calls load() again — a loop that polls as fast as the network
  // allows.
  const busy = useRef(false)
  useEffect(() => { busy.current = !!data?.active.length }, [data])

  // Always polling, at two speeds. An earlier version started the interval
  // only once a worker was already visible, so a run launched while this view
  // was open never appeared — the view could only ever show work that predated
  // it. Idle polling is cheap; being blind to new work is not.
  useEffect(() => {
    if (!ws) return
    let cancelled = false
    const tick = () => {
      if (cancelled) return
      load(() => !cancelled)
      timer.current = window.setTimeout(tick, busy.current ? 3000 : 10000)
    }
    tick()
    return () => {
      cancelled = true
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [ws])

  if (loading) return <div className="omx-scroll"><span className="omx-spin" /></div>

  const active = data?.active ?? []
  const recent = data?.recent ?? []

  return (
    <div className="omx-scroll">
      <ViewIntro
        id="agents"
        title="Agents"
        what="Every long-running job in this Space, as a worker you can watch
              and interrupt — a research run, a CHALLENGE panel, a security
              scan. Each shows its current step, which model is answering, the
              tokens and the measured cost (never an estimate: an unpriced
              model shows a dash), the sources it pulled and the artifacts it
              produced. Pause, Redirect and Cancel are cooperative — the worker
              acts on them at its next checkpoint, which is why the buttons say
              “pausing” rather than claiming it already stopped."
        how="Nothing to set up. Ask OMNIX to research something, or run a
             CHALLENGE, and the worker appears here while it runs. Click one to
             open its audit trail."
      />
      <div className="omx-label" style={{ marginBottom: 12 }}>
        {active.length ? `${active.length} working now` : 'Nothing running'}
        {recent.length ? ` · ${recent.length} recent` : ''}
      </div>

      {active.map((w) => <Worker key={w.id} w={w} onChanged={load} />)}

      {!active.length && !recent.length && (
        <div className="omx-empty">
          <div className="glyph"><IconAgents size={34} /></div>
          <h3>No agent runs in this Space</h3>
          <p>
            Ask OMNIX to research something, or select objects and press
            Research. Agents appear here while they work — with their current
            step, model, measured cost, and controls to pause, redirect or
            cancel them.
          </p>
        </div>
      )}

      {!!recent.length && (
        <>
          <div className="omx-label" style={{ margin: '22px 0 10px' }}>Recent</div>
          {recent.map((w) => <Worker key={w.id} w={w} onChanged={load} />)}
        </>
      )}
    </div>
  )
}
