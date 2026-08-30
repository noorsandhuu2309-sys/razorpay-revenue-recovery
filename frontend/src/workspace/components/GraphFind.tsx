// Find-in-graph.
//
// The graph draws up to 600 objects. Reading a specific name off it means
// scanning a field of dots for a label that may not even be one of the ~40 the
// budget allowed through — which is not a search, it is a hunt. The command
// palette could already select an object by name, but selecting it did not move
// the camera, so you were left holding something you still could not see.
//
// This does the whole gesture: type, see what matched, land on it. The canvas
// re-weights live while you type (matches keep their labels, everything else
// drops back), so the shape of the answer is visible before you commit to a
// result — with two matches you can usually tell which one you meant from where
// they sit, without clicking either.
//
// It shares row 3 with the filter panel and the store closes one when the other
// opens, so they can never stack.

import { useEffect, useMemo, useRef } from 'react'
import { useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import type { GraphModel } from '../lib/graphModel'
import { findMatches } from '../lib/findMatches'
import { ProvDot } from './Provenance'
import { IconClose } from './Icons'

export function GraphFind({ model, onReveal }: {
  model: GraphModel
  /** Put the camera on these ids. The panel does not own the camera; the view
   *  does, because only it knows which renderer is mounted. */
  onReveal: (ids: string[]) => void
}) {
  const open = useGraphUi((s) => s.findOpen)
  const setOpen = useGraphUi((s) => s.setFindOpen)
  const query = useGraphUi((s) => s.query)
  const setQuery = useGraphUi((s) => s.setQuery)
  const select = useWorkspace((s) => s.select)
  const inputRef = useRef<HTMLInputElement>(null)

  const matches = useMemo(() => findMatches(model, query), [model, query])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 20)
  }, [open])

  // Escape closes, and stops there: the shell binds Escape to popping the focus
  // trail and the graph binds it to leaving fullscreen, and without this one
  // press would do all three.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, setOpen])

  if (!open) return null

  const reveal = (id: string) => {
    select(id, 'graph')
    // Fit to the object AND its neighbours. One point has no extent, so the
    // camera would drive to maximum zoom and land the reader on an empty field
    // with the thing they searched for somewhere off the edge.
    const ids = [id, ...(model.adjacency.get(id) ?? [])]
    onReveal(ids)
  }

  return (
    <div className="omx-graph-find" role="dialog" aria-label="Find in graph">
      <div className="fhead">
        <input
          ref={inputRef}
          className="omx-input sm"
          value={query}
          placeholder="Find an object…"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // Enter takes the top match — the common case is that you typed
            // enough and the first row is right.
            if (e.key === 'Enter' && matches[0]) { e.preventDefault(); reveal(matches[0].id) }
          }}
          aria-label="Find an object in the graph"
        />
        <button className="omx-btn icon" onClick={() => setOpen(false)}
                aria-label="Close find"><IconClose size={13} /></button>
      </div>

      {query.trim() && (
        <div className="fcount omx-label">
          {matches.length === 0 ? 'No object matches'
            : `${matches.length}${matches.length === 12 ? '+' : ''} match${matches.length === 1 ? '' : 'es'}`}
        </div>
      )}

      <div className="flist">
        {matches.map((m) => {
          const n = model.byId.get(m.id)
          return (
            <button key={m.id} className="frow-hit" onClick={() => reveal(m.id)}>
              <span className="gl" style={{ color: n?.o.color }}>{n?.o.glyph}</span>
              <span className="nm">{m.name}</span>
              {n && <ProvDot p={n.o.provenance} />}
              <span className="omx-label ty">{n?.o.typeLabel}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
