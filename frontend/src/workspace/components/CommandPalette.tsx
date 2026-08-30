// ⌘K. Two kinds of entry coexist: static commands (switch view, research,
// sync) and live object search results. Object results are debounced against
// the API so typing stays responsive on a large workspace.
//
// Commands are context-sensitive: with objects selected, actions that operate
// on a selection appear and ones that do not are hidden.

import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { ACCENTS, setAccent, setMode, useAppearance } from '../lib/appearance'
import { contextIds, useWorkspace } from '../store/workspace'
import { ProvDot } from './Provenance'
import type { OmxObject, ViewId } from '../lib/types'

interface Cmd {
  id: string
  label: string
  hint: string
  run: () => void | Promise<void>
}

export function CommandPalette() {
  const open = useWorkspace((s) => s.paletteOpen)
  const setOpen = useWorkspace((s) => s.setPalette)
  const setView = useWorkspace((s) => s.setView)
  const ws = useWorkspace((s) => s.workspaceId)
  const workspaces = useWorkspace((s) => s.workspaces)
  const setWorkspace = useWorkspace((s) => s.setWorkspace)
  const select = useWorkspace((s) => s.select)
  const loadGraph = useWorkspace((s) => s.loadGraph)
  const refreshSummary = useWorkspace((s) => s.refreshSummary)
  const setBusy = useWorkspace((s) => s.setBusy)

  // Subscribed, not read once: the appearance commands leave the palette open
  // so you can compare accents, which means this list has to restate itself
  // after each swap or it offers the theme you are already in.
  const { mode, accent } = useAppearance()

  const [q, setQ] = useState('')
  const [cursor, setCursor] = useState(0)
  const [results, setResults] = useState<OmxObject[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  // global shortcut
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(!useWorkspace.getState().paletteOpen)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  useEffect(() => {
    if (open) { setQ(''); setCursor(0); setResults([]); setTimeout(() => inputRef.current?.focus(), 30) }
  }, [open])

  // debounced object search
  useEffect(() => {
    if (!open || !ws || q.trim().length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      api.search(ws, q.trim(), 8).then((r) => setResults(r.results)).catch(() => setResults([]))
    }, 160)
    return () => clearTimeout(t)
  }, [q, open, ws])

  const selection = contextIds(useWorkspace.getState())

  const commands = useMemo<Cmd[]>(() => {
    const views: [ViewId, string][] = [
      ['graph', 'Graph'], ['timeline', 'Timeline'], ['table', 'Table'],
      ['brief', 'Intelligence Brief'], ['claims', 'Claim Ledger'],
      ['sources', 'Source Library'],
    ]
    const base: Cmd[] = views.map(([v, label]) => ({
      id: `view:${v}`, label: `Go to ${label}`, hint: 'view',
      run: () => { setView(v); setOpen(false) },
    }))

    if (q.trim()) {
      base.unshift({
        id: 'research',
        label: `Research "${q.trim()}"`,
        hint: 'oracle',
        run: async () => {
          setOpen(false)
          setBusy('Researching…')
          try {
            await api.research(ws, q.trim(), selection, 'standard')
          } finally { setBusy(null) }
        },
      })
    }

    base.push({
      id: 'sync-terra',
      label: 'Sync TERRA geopolitical graph',
      hint: 'import',
      run: async () => {
        setOpen(false); setBusy('Syncing TERRA…')
        try { await api.terraSync(ws); await loadGraph(); await refreshSummary() }
        finally { setBusy(null) }
      },
    })
    base.push({
      id: 'sync-tracking',
      label: 'Check tracked objects for updates',
      hint: 'tracking',
      run: async () => {
        setOpen(false); setBusy('Checking tracked objects…')
        try { await api.syncTracking(ws); await refreshSummary() }
        finally { setBusy(null) }
      },
    })

    for (const w of workspaces) {
      if (w.id === ws) continue
      base.push({
        id: `ws:${w.id}`, label: `Switch to ${w.name}`, hint: 'workspace',
        run: async () => { setOpen(false); await setWorkspace(w.id) },
      })
    }

    for (const a of ACCENTS) {
      if (a.id === accent) continue
      base.push({
        id: `accent:${a.id}`, label: `Accent: ${a.label}`, hint: 'theme',
        // Deliberately does NOT close the palette: switching accent from here
        // is usually a comparison, same as in the picker.
        run: () => setAccent(a.id),
      })
    }
    base.push({
      id: 'mode',
      label: mode === 'light' ? 'Switch to dark ground' : 'Switch to light ground',
      hint: 'theme',
      run: () => setMode(mode === 'light' ? 'dark' : 'light'),
    })

    return base
  }, [q, ws, workspaces, selection, mode, accent, setView, setOpen, setWorkspace,
      loadGraph, refreshSummary, setBusy])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return commands
    return commands.filter((c) =>
      c.label.toLowerCase().includes(needle) || c.id.startsWith('research'))
  }, [commands, q])

  const items = [
    ...filtered.map((c) => ({ kind: 'cmd' as const, cmd: c })),
    ...results.map((o) => ({ kind: 'obj' as const, obj: o })),
  ]

  if (!open) return null

  const activate = (i: number) => {
    const it = items[i]
    if (!it) return
    if (it.kind === 'cmd') void it.cmd.run()
    else { select(it.obj.id, 'system'); setOpen(false) }
  }

  return (
    <>
      <div className="omx-palette-bg" onClick={() => setOpen(false)} />
      <div className="omx-palette">
        <input
          ref={inputRef}
          value={q}
          placeholder="Search objects, run a command, research a topic…"
          onChange={(e) => { setQ(e.target.value); setCursor(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, items.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
            if (e.key === 'Enter') { e.preventDefault(); activate(cursor) }
          }}
        />
        <div className="omx-palette-list">
          {items.length === 0 && (
            <div className="omx-palette-item"><span style={{ opacity: 0.6 }}>No matches</span></div>
          )}
          {items.map((it, i) => (
            <div
              key={it.kind === 'cmd' ? it.cmd.id : it.obj.id}
              className={`omx-palette-item ${i === cursor ? 'on' : ''}`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => activate(i)}
            >
              {it.kind === 'cmd' ? (
                <>
                  <span style={{ color: 'var(--omx-gold)' }}>›</span>
                  <span>{it.cmd.label}</span>
                  <span className="hint">{it.cmd.hint}</span>
                </>
              ) : (
                <>
                  <span style={{ color: it.obj.color }}>{it.obj.glyph}</span>
                  <span>{it.obj.name}</span>
                  {/* Provenance travels with the result. Picking an object out
                      of search is the moment you decide to act on it, so the
                      trust level has to be visible at the point of choice
                      rather than one click later in the inspector. */}
                  <ProvDot p={it.obj.provenance} />
                  <span className="hint">{it.obj.familyLabel}</span>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
