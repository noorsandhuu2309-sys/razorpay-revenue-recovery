// The focus trail (§7).
//
// Focus Mode's real job is to make a large graph navigable without lying about
// where you are. The trail is the escape hatch the blueprint asks for in §16:
// every step is clickable, so no drill-down is a one-way door.
//
// The first crumb is always the Space itself, because "up" must always have a
// destination — a trail that starts at an object gives the user nowhere to
// return to.

import { useWorkspace } from '../store/workspace'
import { IconArrowUp } from './Icons'

export function Breadcrumbs() {
  const focus = useWorkspace((s) => s.focus)
  const focusTo = useWorkspace((s) => s.focusTo)
  const workspaces = useWorkspace((s) => s.workspaces)
  const workspaceId = useWorkspace((s) => s.workspaceId)

  const space = workspaces.find((w) => w.id === workspaceId)
  if (!focus.length) return null

  return (
    <nav className="omx-crumbs" aria-label="Focus trail">
      <button className="omx-crumb" onClick={() => void focusTo(0)}>
        {space?.name ?? 'Space'}
      </button>
      {focus.map((f, i) => (
        <span key={f.id} className="omx-crumb-wrap">
          <span className="sep">/</span>
          <button
            className={`omx-crumb ${i === focus.length - 1 ? 'on' : ''}`}
            onClick={() => void focusTo(i + 1)}
            aria-current={i === focus.length - 1 ? 'page' : undefined}
          >
            <span className="g">{f.glyph}</span>{f.name}
          </button>
        </span>
      ))}
      <button className="omx-crumb up" onClick={() => void focusTo(focus.length - 1)}
              title="Back out one level (Esc)" aria-label="Back out one level">
        <IconArrowUp size={13} />
      </button>
    </nav>
  )
}
