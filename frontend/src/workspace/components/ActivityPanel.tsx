// The Activity layer.
//
// Its purpose is trust in autonomous work: if agents change the Space while the
// user is not watching, there has to be a record of what changed and a way to
// get to it. Every row navigates to the object it is about — an activity feed
// you cannot click through is decoration.
//
// It shows what was recorded and nothing else. No synthetic "OMNIX is thinking"
// rows, and no chain-of-thought: the audit trail is a record of actions and
// results, which is a different thing from narrating the model's reasoning.
//
// It is now CONTEXTUAL. Selecting an object scopes the feed to that object;
// selecting several scopes it to all of them. That is what turns the graph, the
// inspector and this panel into one analytical surface rather than three
// widgets that happen to share a window — you select Russia and the feed
// becomes Russia's, without navigating anywhere.
//
// The whole-Space feed is never taken away: it is one click back, and the
// header always says which of the two you are reading.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useContextIds, useWorkspace } from '../store/workspace'
import { IconClose, IconRefresh } from './Icons'
import type { OmxEvent } from '../lib/types'

const RELATIVE = (iso: string): string => {
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return ''
  const mins = Math.round((Date.now() - t) / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h`
  return `${Math.round(hrs / 24)}d`
}

export function ActivityPanel() {
  const open = useWorkspace((s) => s.activityOpen)
  const setOpen = useWorkspace((s) => s.setActivity)
  const activity = useWorkspace((s) => s.activity)
  const reload = useWorkspace((s) => s.loadActivity)
  const select = useWorkspace((s) => s.select)
  const setView = useWorkspace((s) => s.setView)
  const ws = useWorkspace((s) => s.workspaceId)
  const objects = useWorkspace((s) => s.contextObjects)
  const ids = useContextIds()

  /** Scoped to the selection, or null while the whole Space is being shown. */
  const [scoped, setScoped] = useState<OmxEvent[] | null>(null)
  const [loading, setLoading] = useState(false)
  /** Set by the user to look past the selection without deselecting. */
  const [showAll, setShowAll] = useState(false)

  const key = ids.join(',')
  const scoping = !showAll && ids.length > 0

  useEffect(() => {
    if (!open || !ws || !scoping) { setScoped(null); return }
    let cancelled = false
    setLoading(true)
    // The timeline endpoint filters server-side, so a Space with thousands of
    // events does not have to be pulled down to show four rows about Russia.
    api.timeline(ws, { objects: key, limit: 80 })
      .then((r) => { if (!cancelled) setScoped(r.events) })
      .catch(() => { if (!cancelled) setScoped([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, ws, key, scoping])

  // A new selection means the user is looking at something else; the manual
  // "show everything" override should not survive that.
  useEffect(() => { setShowAll(false) }, [key])

  if (!open) return null

  const rows = scoping ? (scoped ?? []) : activity
  const held = ids.map((id) => objects[id]).filter(Boolean)
  const title = scoping
    ? held.length === 1 ? held[0].name
      : held.length ? `${held.length} objects` : 'Selection'
    : 'Activity'

  const go = (e: OmxEvent) => {
    if (e.objectId) { select(e.objectId, 'system'); setView('graph') }
    else setView('timeline')
  }

  return (
    <aside className="omx-activity" aria-label="Activity">
      <div className="omx-activity-head">
        <span className="omx-label">{scoping ? 'Activity ·' : 'Activity'}</span>
        {scoping && <span className="omx-activity-scope">{title}</span>}
        <div style={{ flex: 1 }} />
        {loading && <span className="omx-spin" />}
        <button className="omx-btn icon" title="Refresh" aria-label="Refresh activity"
                onClick={() => scoping ? setShowAll(false) : void reload()}>
          <IconRefresh size={14} />
        </button>
        <button className="omx-btn icon" title="Close" aria-label="Close activity"
                onClick={() => setOpen(false)}><IconClose size={14} /></button>
      </div>

      {/* Scope is always visible and always reversible. A feed that silently
          narrowed would look like activity had stopped. */}
      {ids.length > 0 && (
        <div className="omx-activity-tabs">
          <button className={`omx-chip sm ${scoping ? 'on' : ''}`}
                  onClick={() => setShowAll(false)}>Selection</button>
          <button className={`omx-chip sm ${showAll ? 'on' : ''}`}
                  onClick={() => setShowAll(true)}>Whole Space</button>
        </div>
      )}

      {rows.length ? (
        <div className="omx-activity-list">
          {rows.map((e) => (
            <button key={e.id} className="omx-activity-row" onClick={() => go(e)}>
              <span className={`dot ${e.relevance}`} />
              <span className="bd">
                <span className="n">{e.title}</span>
                {e.body && <span className="s">{e.body}</span>}
              </span>
              <span className="omx-mono t">
                {RELATIVE(e.detectedAt || e.occurredAt)}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="omx-empty-line" style={{ padding: '14px 16px' }}>
          {scoping
            ? `Nothing recorded about ${title} yet. Track it, or run Research, `
              + 'and anything OMNIX observes will appear here.'
            : 'Nothing recorded yet. Agent runs, discovered sources and inferred '
              + 'relationships appear here as they happen.'}
        </p>
      )}
    </aside>
  )
}
