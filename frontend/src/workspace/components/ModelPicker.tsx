// The model picker in the chat composer bar.
//
// A popover rather than a `<select>`, for one reason: the choice is not
// self-explanatory from a name. "Nemotron Reasoning 30B" tells a user nothing,
// and picking a model badly is worse than not picking one — so each row carries
// the job it is for, its licence and its measured first-token time, and Auto
// sits at the top described as the recommended setting rather than as the
// absence of a choice.
//
// Auto is the default and stays the default. Everything here is an override of
// a router that is right most of the time; the picker exists for the case where
// the user knows something the classifier does not.

import { useEffect, useRef, useState } from 'react'
import { AUTO_MODEL } from '../lib/chat'
import { loadRoster, useRoster, type ModelCard } from '../lib/models'
import { IconChevron, IconModel } from './Icons'

const ROLE_ORDER = ['chat', 'code', 'research', 'vision', 'fast']

export function ModelPicker({ value, onChange, disabled }: {
  value: string
  onChange: (id: string) => void
  disabled?: boolean
}) {
  const roster = useRoster()
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => { void loadRoster() }, [])

  // Close on an outside click or Escape. Pointerdown rather than click, so the
  // popover is gone before whatever was clicked underneath reacts.
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const enabled = roster.models.filter((m) => m.enabled)
  const current = enabled.find((m) => m.id === value)
  const pinned = roster.pinned
    ? roster.models.find((m) => m.id === roster.pinned)
    : null

  // A conversation saved with a model that has since been switched off would
  // otherwise show a blank button while silently routing by role — which is
  // what the server does with it, so the label says so.
  const label = pinned ? pinned.label
    : value === AUTO_MODEL ? 'Auto'
    : current ? current.label
    : 'Auto'

  const groups = ROLE_ORDER
    .map((r) => ({
      id: r,
      label: enabled.find((m) => m.role === r)?.roleLabel ?? r,
      models: enabled.filter((m) => m.role === r),
    }))
    .filter((g) => g.models.length)

  const choose = (id: string) => { onChange(id); setOpen(false) }

  const Row = ({ m }: { m: ModelCard }) => (
    <button
      className={`omx-mp-row ${value === m.id ? 'on' : ''}`}
      onClick={() => choose(m.id)}
      role="option"
      aria-selected={value === m.id}
    >
      <span className="hd">
        <span className="nm">{m.label}</span>
        <span className="omx-mono tt">{m.ttft.toFixed(1)}s</span>
      </span>
      <span className="bl">{m.blurb}</span>
      <span className="ft">
        <span className="omx-mono">{m.vendor} · {m.params}</span>
        <span className="lic">{m.license}</span>
      </span>
    </button>
  )

  return (
    <div className="omx-mp" ref={wrapRef}>
      <button
        className={`omx-btn tiny ${open ? 'on' : ''}`}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={pinned
          ? `Pinned to ${pinned.label} by OMNIX_CHAT_MODEL for this run`
          : 'Choose which model answers'}
      >
        <IconModel size={12} />
        {label}
        {pinned && <span className="omx-mp-pin">pinned</span>}
        <IconChevron size={11} />
      </button>

      {open && (
        <div className="omx-mp-pop" role="listbox" aria-label="Model">
          {pinned && (
            <div className="omx-mp-note">
              This run is pinned to <b>{pinned.label}</b> by the
              {' '}<code>OMNIX_CHAT_MODEL</code> environment variable. Choices
              here will not take effect until it is unset.
            </div>
          )}

          <button
            className={`omx-mp-row auto ${value === AUTO_MODEL ? 'on' : ''}`}
            onClick={() => choose(AUTO_MODEL)}
            role="option"
            aria-selected={value === AUTO_MODEL}
          >
            <span className="hd">
              <span className="nm">Auto</span>
              <span className="omx-pill xs">Recommended</span>
            </span>
            <span className="bl">
              OMNIX reads the question and routes it — code and maths to the
              reasoning model, an image to the vision model, a research question
              to the 120B with web search.
            </span>
          </button>

          {roster.error && (
            <div className="omx-mp-note bad">
              The roster could not be loaded ({roster.error}). Auto still works.
            </div>
          )}

          {groups.map((g) => (
            <div className="omx-mp-group" key={g.id}>
              <div className="omx-label">{g.label}</div>
              {g.models.map((m) => <Row key={m.id} m={m} />)}
            </div>
          ))}

          <div className="omx-mp-foot">
            Every model here is open-weight, running on NVIDIA NIM. Switch models
            on and off in Settings.
          </div>
        </div>
      )}
    </div>
  )
}
