// The contextual action bar — selection becomes context (§6).
//
// This is the single interaction that separates "a graph you look at" from "a
// workspace you manipulate". Selecting objects should expose verbs, so the user
// never has to type a sentence explaining what "this" refers to.
//
// Two rules it follows:
//
//   * **Only offer verbs that are real.** Every action here calls an endpoint
//     that exists. There is no Create > Presentation button that opens a toast
//     saying "coming soon" — an action that lies is worse than an action that
//     is absent, because it teaches the user not to trust the bar. Create now
//     exists precisely because it became real: its menu is built from
//     `/api/outputs/styles`, so the server decides what it can offer.
//   * **Arity gates the verb.** Compare and Connect need two or more objects
//     and are hidden below that, rather than shown disabled with a tooltip.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useContextIds, useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import { readLabel } from '../lib/graphModel'
import {
  IconClose, IconCompare, IconExpand, IconFocus, IconIntents, IconPlus,
  IconQuery, IconSwap, IconTrack,
} from './Icons'
import type { OmxObject, OutputStyle } from '../lib/types'

/** Verbs whose availability depends on how many objects are held. */
interface Action {
  key: string
  label: string
  icon: (p: { size?: number }) => React.ReactNode
  /** Minimum objects required. */
  min: number
  hint: string
}

const ACTIONS: Action[] = [
  // Focus is also bound to double-click on the canvas, but it has to exist as
  // a button too: §16 is explicit that spatial gestures must never be the only
  // route to something, and a drill-down you can only reach by double-clicking
  // a moving node is not reachable by keyboard at all.
  { key: 'focus', label: 'Focus', icon: IconFocus, min: 1,
    hint: 'Rebuild the canvas around this' },
  { key: 'compare', label: 'Compare', icon: IconCompare, min: 2,
    hint: 'Put these side by side' },
  { key: 'connect', label: 'Find Connections', icon: IconSwap, min: 2,
    hint: 'Trace the shortest chain between these, and light it on the graph' },
  { key: 'research', label: 'Research', icon: IconQuery, min: 1,
    hint: 'Send an agent to find more' },
  { key: 'expand', label: 'Expand', icon: IconExpand, min: 1,
    hint: 'Pull in the strongest neighbours' },
  { key: 'track', label: 'Track', icon: IconTrack, min: 1,
    hint: 'Watch these for changes' },
  // §12: creation is an output action over the selection, not a set of tabs.
  { key: 'create', label: 'Create', icon: IconPlus, min: 1,
    hint: 'Turn what is held into a document' },
  // §11: a standing Intent over the selection.
  { key: 'watch', label: 'Watch', icon: IconIntents, min: 1,
    hint: 'Keep pursuing this — creates an Intent' },
]

export function ActionBar() {
  const ids = useContextIds()
  const objects = useWorkspace((s) => s.contextObjects)
  const ws = useWorkspace((s) => s.workspaceId)
  const setView = useWorkspace((s) => s.setView)
  const clearSelection = useWorkspace((s) => s.clearSelection)
  const expandNode = useWorkspace((s) => s.expandNode)
  const focusOn = useWorkspace((s) => s.focusOn)
  const setBusy = useWorkspace((s) => s.setBusy)
  const loadActivity = useWorkspace((s) => s.loadActivity)
  const refreshSummary = useWorkspace((s) => s.refreshSummary)
  const openOutput = useWorkspace((s) => s.openOutput)
  const [note, setNote] = useState<string>('')
  const [menu, setMenu] = useState<'create' | null>(null)
  const [styles, setStyles] = useState<OutputStyle[]>([])

  // Fetched once, lazily: the menu must reflect what the backend implements,
  // and a hard-coded copy here is exactly how a "coming soon" button appears.
  useEffect(() => {
    if (menu !== 'create' || styles.length) return
    api.outputStyles().then((r) => setStyles(r.styles)).catch(() => setStyles([]))
  }, [menu, styles.length])

  const held: OmxObject[] = ids.map((id) => objects[id]).filter(Boolean)
  if (!held.length) return null

  const create = async (style: OutputStyle) => {
    setMenu(null)
    setNote('')
    setBusy(`Creating ${style.label.toLowerCase()}…`)
    try {
      const { output } = await api.createOutput(ws, style.key, ids)
      // Land on the document rather than a list: the artifact is the answer to
      // the action, and making the user hunt for it undoes the point of §12.
      openOutput(output.id)
      void loadActivity()
      void refreshSummary()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That output could not be built.')
    } finally {
      setBusy(null)
    }
  }

  const run = async (key: string) => {
    setNote('')
    try {
      switch (key) {
        case 'focus':
          // Focus is single-object by nature: the canvas rebuilds around one
          // root, so the newest held object wins rather than silently picking.
          await focusOn(held[held.length - 1])
          break

        case 'compare':
          setView('compare')
          break

        case 'connect': {
          // The honest answer to "how are these related" is the actual path,
          // including "there isn't one within 4 hops" — which is information,
          // not a failure.
          setBusy('Tracing connection…')
          const r = await api.path(ws, held[0].id, held[1].id)
          if (r.found && r.path.length) {
            // Print the chain AND light it on the canvas. The text alone was
            // the old behaviour and it threw away the answer: the reader was
            // told the route and then left to find it among the dots
            // themselves. The graph is where a path belongs.
            const ids = [r.path[0].from.id, ...r.path.map((s) => s.to.id)]
            useGraphUi.getState().setPath(
              ids, `${held[0].name} → ${held[1].name}`)
            useGraphUi.getState().setFocusMode(false)
            // `label` still carries the traversal's "(inbound)" marker, which
            // is bookkeeping and not English — printed raw it produced
            // "United States of America located in (inbound) Nvidia". Strip
            // it and reverse the sentence, which is what it was recording.
            setNote(r.path.map((s) => {
              const { label, inbound } = readLabel({ label: s.label, relation: '' })
              return inbound
                ? `${s.to.name} ${label} ${s.from.name}`
                : `${s.from.name} ${label} ${s.to.name}`
            }).join('  ·  '))
          } else {
            useGraphUi.getState().setPath(null)
            setNote(`No path between ${held[0].name} and ${held[1].name} within `
              + '4 hops. That is a gap in what OMNIX knows, not proof they are '
              + 'unrelated.')
          }
          break
        }

        case 'research': {
          setBusy('Researching…')
          const question = held.length === 1
            ? `Research ${held[0].name}`
            : `Research the relationship between ${held.map((h) => h.name).join(', ')}`
          const { executionId } = await api.research(ws, question, ids)
          setNote(`Agent started. Findings will land in this Space as objects `
            + `and sources (run ${executionId.slice(0, 8)}).`)
          void loadActivity()
          break
        }

        case 'expand':
          setBusy('Expanding…')
          for (const o of held) await expandNode(o.id)
          break

        case 'track': {
          setBusy('Tracking…')
          const next = !held.every((h) => h.tracked)
          for (const o of held) await api.track(ws, o.id, next)
          setNote(next
            ? `Tracking ${held.length} object${held.length > 1 ? 's' : ''}. `
              + 'Changes will appear under What changed.'
            : 'Tracking stopped.')
          void refreshSummary()
          break
        }

        case 'create':
          setMenu((m) => (m === 'create' ? null : 'create'))
          break

        case 'watch': {
          // Tracking and an Intent are different promises: tracking records
          // that something changed, an Intent says what you are watching FOR.
          setBusy('Creating Intent…')
          const title = held.length === 1
            ? `Monitor ${held[0].name}`
            : `Monitor ${held.slice(0, 3).map((h) => h.name).join(', ')}`
            + (held.length > 3 ? ` +${held.length - 3}` : '')
          const { intent } = await api.createIntent(ws, { title, objectIds: ids })
          setNote(`Intent “${intent.title}” is active. It has not run yet — `
            + `OMNIX checks it every ${intent.cadenceMinutes} minutes, and `
            + 'anything it catches appears under Intents.')
          break
        }
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That action failed.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="omx-actionbar" role="toolbar" aria-label="Actions for selection">
      <div className="omx-actionbar-held">
        <span className="omx-label">{held.length} held</span>
        <div className="chips">
          {held.slice(0, 4).map((o) => (
            <button key={o.id} className="omx-chip" onClick={() => void focusOn(o)}
                    title={`Focus ${o.name}`}>
              <span className="g" style={{ color: o.color }}>{o.glyph}</span>
              {o.name}
            </button>
          ))}
          {held.length > 4 && (
            <span className="omx-chip more">+{held.length - 4}</span>
          )}
        </div>
      </div>

      <div className="omx-actionbar-verbs">
        {ACTIONS.filter((a) => held.length >= a.min).map((a) => {
          const Icon = a.icon
          return (
            <button key={a.key} className="omx-verb" title={a.hint}
                    onClick={() => void run(a.key)}>
              <span className="g"><Icon size={13} /></span>{a.label}
            </button>
          )
        })}
        <button className="omx-verb ghost" onClick={clearSelection} title="Clear"
                aria-label="Clear selection">
          <IconClose size={13} />
        </button>
      </div>

      {menu === 'create' && (
        <div className="omx-create-menu" role="menu" aria-label="Create an output">
          {!styles.length && <span className="omx-label">Loading styles…</span>}
          {styles.map((st) => {
            const short = held.length < st.minObjects
            return (
              <button
                key={st.key}
                className="omx-create-item"
                role="menuitem"
                disabled={short}
                title={short
                  ? `${st.label} needs at least ${st.minObjects} objects`
                  : st.hint}
                onClick={() => void create(st)}
              >
                <span className="g">{st.glyph}</span>
                <span className="l">{st.label}</span>
                <span className="h">{st.hint}</span>
                <span className="f">{st.formats.join(' · ')}</span>
              </button>
            )
          })}
        </div>
      )}

      {note && (
        <div className="omx-actionbar-note">
          {note}
          <button className="omx-verb ghost" onClick={() => setNote('')}
                  aria-label="Dismiss"><IconClose size={13} /></button>
        </div>
      )}
    </div>
  )
}
