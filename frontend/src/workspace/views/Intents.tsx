// Intents — the fourth primitive (§11).
//
// An Intent is something the user WANTS, standing over time, as opposed to an
// object (something that exists), an agent (something that works) or a Space
// (where the work lives).
//
// The interface rule that matters here: never let silence read as all-clear.
// A monitor that has never run says so in words, and one that ran and found
// nothing says that instead — those are different facts and conflating them is
// how monitoring products lose trust.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useContextIds, useWorkspace } from '../store/workspace'
import { IconClose, IconIntents } from '../components/Icons'
import { ViewIntro } from '../components/ViewIntro'
import type { Intent, IntentHit } from '../lib/types'

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const ms = Date.now() - new Date(iso).getTime()
  const mins = Math.round(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

function HitRow({ hit }: { hit: IntentHit }) {
  const select = useWorkspace((s) => s.select)
  return (
    <div className="omx-hit" data-r={hit.relevance}>
      <span className="omx-hit-kind">{hit.kind}</span>
      <button
        className="omx-hit-title"
        disabled={!hit.objectId}
        onClick={() => hit.objectId && select(hit.objectId, 'intents')}
      >{hit.title}</button>
      <span className="omx-label">{ago(hit.createdAt)}</span>
    </div>
  )
}

function IntentCard({ intent, onChanged }: {
  intent: Intent; onChanged: () => void
}) {
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  const [checking, setChecking] = useState(false)
  const [note, setNote] = useState('')
  const [hits, setHits] = useState<IntentHit[] | null>(null)

  const check = async () => {
    setChecking(true)
    setNote('')
    try {
      const r = await api.checkIntent(ws, intent.id)
      setNote(r.newHits
        ? `${r.newHits} new`
        : 'Checked just now — nothing new since the last check.')
      onChanged()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Check failed.')
    } finally {
      setChecking(false)
    }
  }

  const toggle = async () => {
    await api.updateIntent(ws, intent.id, {
      status: intent.status === 'active' ? 'paused' : 'active',
    })
    onChanged()
  }

  const showHits = async () => {
    if (hits) { setHits(null); return }
    const r = await api.intentHits(ws, intent.id)
    setHits(r.hits)
  }

  return (
    <div className="omx-intent" data-status={intent.status}>
      <div className="omx-intent-head">
        <div>
          <div className="omx-intent-title">{intent.title}</div>
          <div className="omx-label">
            {intent.status} · every {intent.cadenceMinutes}m ·
            {' '}{intent.relevanceFloor}+ relevance
          </div>
        </div>
        <div className="omx-intent-actions">
          <button className="omx-btn" onClick={() => void check()} disabled={checking}>
            {checking ? 'Checking…' : 'Check now'}
          </button>
          <button className="omx-btn" onClick={() => void toggle()}>
            {intent.status === 'active' ? 'Pause' : 'Resume'}
          </button>
          <button
            className="omx-btn icon"
            title="Delete this Intent"
            aria-label="Delete this Intent"
            onClick={async () => { await api.deleteIntent(ws, intent.id); onChanged() }}
          ><IconClose size={14} /></button>
        </div>
      </div>

      {intent.description && <p className="omx-intent-desc">{intent.description}</p>}

      <div className="omx-intent-watching">
        {intent.objects.map((o) => (
          <button key={o.id} className="omx-chip"
                  onClick={() => select(o.id, 'intents')}>{o.name}</button>
        ))}
        {intent.keywords.map((k) => (
          <span key={k} className="omx-chip kw">“{k}”</span>
        ))}
      </div>

      {/* The honesty line. `lastCheckedAt === null` is a different statement
          from "checked, found nothing", and the copy keeps them apart. */}
      <div className="omx-intent-state">
        {intent.lastCheckedAt === null ? (
          <span className="warn">
            Never checked yet — the first sweep runs within a minute.
          </span>
        ) : (
          <span>
            Last checked {ago(intent.lastCheckedAt)}
            {intent.hitCount
              ? ` · ${intent.hitCount} hit${intent.hitCount === 1 ? '' : 's'} total`
                + ` · last ${ago(intent.lastHitAt)}`
              : ' · nothing caught so far'}
          </span>
        )}
        {note && <span className="omx-intent-note">{note}</span>}
      </div>

      {!!intent.recentHits.length && (
        <div className="omx-hits">
          {(hits ?? intent.recentHits).map((h) => <HitRow key={h.id} hit={h} />)}
          {intent.hitCount > intent.recentHits.length && (
            <button className="omx-btn ghost" onClick={() => void showHits()}>
              {hits ? 'Show less' : `Show all ${intent.hitCount}`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function NewIntent({ onCreated }: { onCreated: () => void }) {
  const ws = useWorkspace((s) => s.workspaceId)
  const ids = useContextIds()
  const objects = useWorkspace((s) => s.contextObjects)
  const [title, setTitle] = useState('')
  const [keywords, setKeywords] = useState('')
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)

  const held = ids.map((id) => objects[id]).filter(Boolean)

  const submit = async () => {
    setError('')
    try {
      await api.createIntent(ws, {
        title: title.trim(),
        objectIds: ids,
        keywords: keywords.split(',').map((k) => k.trim()).filter(Boolean),
      })
      setTitle(''); setKeywords(''); setOpen(false)
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not create that Intent.')
    }
  }

  if (!open) {
    return (
      <button className="omx-btn on" onClick={() => setOpen(true)}>
        ＋ New Intent
      </button>
    )
  }

  return (
    <div className="omx-intent-new">
      <input
        className="omx-input"
        placeholder="What should OMNIX keep pursuing? e.g. Monitor NVIDIA"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input
        className="omx-input"
        placeholder="Keywords, comma separated (optional if objects are held)"
        value={keywords}
        onChange={(e) => setKeywords(e.target.value)}
      />
      <div className="omx-label">
        {held.length
          ? `Watching ${held.length} held object${held.length === 1 ? '' : 's'}: `
            + held.map((h) => h.name).join(', ')
          : 'Nothing held — add keywords, or select objects first.'}
      </div>
      {error && <div className="omx-intent-note">{error}</div>}
      <div style={{ display: 'flex', gap: 7 }}>
        <button className="omx-btn on" onClick={() => void submit()}>Create</button>
        <button className="omx-btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  )
}

export function IntentsView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const [intents, setIntents] = useState<Intent[]>([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    if (!ws) return
    api.intents(ws)
      .then((r) => setIntents(r.intents))
      .catch(() => setIntents([]))
      .finally(() => setLoading(false))
  }

  useEffect(load, [ws])

  if (loading) return <div className="omx-scroll"><span className="omx-spin" /></div>

  return (
    <div className="omx-scroll">
      <ViewIntro
        id="intents"
        title="Intents"
        what="A standing monitor, not a question you ask once. An Intent names an
              outcome you care about — “tell me when Russia and China start
              cooperating on this” — and REVORA re-checks it on its own cadence
              while you are elsewhere. It reads real material: new events, new
              relationships and new claims that have appeared since the last
              check, filtered to the relevance floor you set. Each thing it
              catches is recorded as a hit against the Intent, deduplicated, so
              the same development is never reported twice."
        how="Select the objects you care about anywhere in REVORA and press
             Watch, or create one here with keywords. Then leave. Hits collect
             under the Intent and surface in the Brief."
      />
      <div className="omx-intents-bar">
        <span className="omx-label">
          {intents.length} intent{intents.length === 1 ? '' : 's'} ·
          {' '}{intents.filter((i) => i.status === 'active').length} active
        </span>
        <NewIntent onCreated={load} />
      </div>

      {!intents.length ? (
        <div className="omx-empty">
          <div className="glyph"><IconIntents size={34} /></div>
          <h3>No standing Intents</h3>
          <p>
            An Intent is an outcome REVORA keeps pursuing rather than a question
            asked once — “monitor this company”, “tell me when these two start
            cooperating”. Select objects and press <strong>Watch</strong>, or
            create one here. REVORA checks each on its own cadence and records
            what it caught.
          </p>
        </div>
      ) : (
        intents.map((i) => (
          <IntentCard key={i.id} intent={i} onChanged={load} />
        ))
      )}
    </div>
  )
}
