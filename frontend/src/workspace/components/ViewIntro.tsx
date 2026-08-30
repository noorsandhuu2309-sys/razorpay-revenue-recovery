// A one-band explanation of what a view is for.
//
// Written because of a real question: "I didn't get the point of Intents,
// Outputs and Agents." All three of them DO explain themselves — in their
// empty states. Which means the explanation is visible to exactly the users
// who have never used the feature, and disappears the moment it starts
// working, i.e. the moment there is something on screen to be confused BY.
//
// So the explanation is not an empty state. It sits above the content, states
// what the thing is, what it does on its own, and what the user does with it —
// and can be dismissed. Dismissal is per view and persisted, so it is a
// one-time cost rather than a banner to close on every visit, and the `?`
// button in the header brings it back for the time somebody else is looking
// over your shoulder.

import { useState } from 'react'
import { IconClose } from './Icons'

const KEY = 'omx.intro.dismissed.v1'

function dismissed(): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} }
}

function persist(next: Record<string, boolean>) {
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* private mode */ }
}

export function ViewIntro({ id, title, what, how }: {
  /** Stable key for the dismissal, independent of the copy — editing the text
   *  must not un-dismiss the band for everyone who has already read it. */
  id: string
  title: string
  /** What the feature IS and what it does without being asked. */
  what: string
  /** The concrete next action, in the user's words. */
  how: string
}) {
  const [open, setOpen] = useState(() => !dismissed()[id])

  const close = () => {
    setOpen(false)
    persist({ ...dismissed(), [id]: true })
  }

  if (!open) {
    return (
      <button className="omx-intro-recall" onClick={() => setOpen(true)}>
        What is {title}?
      </button>
    )
  }

  return (
    <aside className="omx-intro">
      <div className="omx-intro-body">
        <h3>{title}</h3>
        <p>{what}</p>
        <p className="how"><strong>To use it:</strong> {how}</p>
      </div>
      <button className="omx-intro-x" onClick={close}
              title="Dismiss — reachable again from the header">
        <IconClose size={13} />
      </button>
    </aside>
  )
}
