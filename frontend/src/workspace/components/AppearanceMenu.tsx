// The appearance control in the topbar: five accents by two modes.
//
// One popover rather than two separate controls, because the two axes are read
// together — "which theme am I in" is answered by the pair, not by either half.
// The trigger shows the live accent as a filled dot, so the answer is already
// on screen before anything is opened.

import { useEffect, useState } from 'react'
import {
  ACCENTS, setAccent, setMode, useAppearance, type AccentId,
} from '../lib/appearance'
import { IconMoon, IconSun } from './Icons'

export function AppearanceMenu() {
  const { mode, accent } = useAppearance()
  const [open, setOpen] = useState(false)

  // Escape closes, and defers to nothing: this is the innermost thing on
  // screen when it is open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open])

  const pick = (id: AccentId) => {
    setAccent(id)
    // Left open on purpose. Choosing an accent is a comparison — you want to
    // see it land and then try the next one, and a menu that closes on the
    // first click makes that five round trips.
  }

  const activeLabel = ACCENTS.find((a) => a.id === accent)?.label ?? accent

  return (
    <div className="omx-appearance">
      <button
        className={`omx-btn icon ${open ? 'on' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title={`Appearance — ${activeLabel}, ${mode} mode`}
        aria-label="Appearance"
        aria-expanded={open}
      >
        <span className="sw" aria-hidden="true" />
      </button>

      {open && (
        <>
          <div className="omx-appearance-scrim" onClick={() => setOpen(false)} />
          <div className="omx-appearance-menu" role="dialog" aria-label="Appearance">
            <div>
              <span className="omx-label">Accent</span>
              <div className="omx-swatches" role="radiogroup" aria-label="Accent">
                {ACCENTS.map((a) => (
                  <button
                    key={a.id}
                    // The swatch paints itself: workspace.css keys the accent
                    // ramp off this attribute on ANY element, so what you see
                    // here is the same declaration the shell will use.
                    data-accent={a.id}
                    className={`omx-swatch ${a.id === accent ? 'on' : ''}`}
                    onClick={() => pick(a.id)}
                    role="radio"
                    aria-checked={a.id === accent}
                    title={`${a.label} — ${a.hint}`}
                    aria-label={a.label}
                  />
                ))}
              </div>
            </div>

            <div>
              <span className="omx-label">Ground</span>
              <div className="omx-modes" role="radiogroup" aria-label="Ground">
                <button
                  className={`omx-mode-btn ${mode === 'dark' ? 'on' : ''}`}
                  onClick={() => setMode('dark')}
                  role="radio"
                  aria-checked={mode === 'dark'}
                >
                  <IconMoon size={13} /> Dark
                </button>
                <button
                  className={`omx-mode-btn ${mode === 'light' ? 'on' : ''}`}
                  onClick={() => setMode('light')}
                  role="radio"
                  aria-checked={mode === 'light'}
                >
                  <IconSun size={13} /> Light
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
