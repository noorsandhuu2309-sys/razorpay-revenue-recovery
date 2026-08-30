// One menu for everything you can do with an answer once you have it: copy it
// in the form you need, or turn it into a document.
//
// Copy and export live in the same menu on purpose. They are the same intent —
// "get this out of here and into the thing I am actually writing" — and the
// only difference is how big the destination is. Splitting them into two
// controls made people hunt for the one they wanted.
//
// Every action reports back. A copy button that looks identical before and
// after the click is indistinguishable from a dead one, and an export that
// fails silently is worse than an export that is missing.

import { useEffect, useRef, useState } from 'react'
import {
  FORMATS, asPlainText, copyText, exportDocument, tablesAsTsv, titleFrom,
  type ExportFormat,
} from '../lib/exporting'
import { IconCheck, IconCopy, IconDownload, IconTable, IconWarning } from './Icons'

type Flash = { kind: 'ok' | 'bad'; text: string } | null

export function ShareMenu({ markdown, title, subtitle, compact }: {
  markdown: string
  /** Overrides the title derived from the answer's own first heading. */
  title?: string
  subtitle?: string
  /** Icon-only trigger, for a crowded message toolbar. */
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [flash, setFlash] = useState<Flash>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const wrap = useRef<HTMLDivElement>(null)

  // The menu is `position: fixed` and placed from the trigger's measured rect,
  // NOT absolutely positioned inside it. Its usual home is a message toolbar
  // inside `.omx-chat-scroll`, which is `overflow: hidden auto` — an absolutely
  // positioned child of that is clipped at the scroll box's edges, so the menu
  // opened somewhere off-screen and simply could not be seen. Measuring on open
  // is also what lets it flip above or below depending on the room available.
  useEffect(() => {
    if (!open) { setPos(null); return }
    const place = () => {
      const btn = wrap.current?.querySelector('button')
      if (!btn) return
      const r = btn.getBoundingClientRect()
      const H = 340          // a little more than the menu's natural height
      const W = 240
      const below = window.innerHeight - r.bottom
      // Prefer above (the toolbar sits under the answer, so up is toward the
      // content), but flip down when there is not room.
      const top = below < H && r.top > H ? r.top - H - 6 : r.bottom + 6
      const left = Math.min(Math.max(8, r.left), window.innerWidth - W - 8)
      setPos({ top, left })
    }
    place()
    // Re-place on scroll and resize: fixed positioning does not follow the
    // element it was measured from, so a scroll would leave it stranded.
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  // The flash lives on the trigger, not inside the menu, so the acknowledgement
  // survives the menu closing — which it does immediately after every action.
  const say = (kind: 'ok' | 'bad', text: string) => {
    setFlash({ kind, text })
    setTimeout(() => setFlash(null), 2400)
  }

  const copyOnce = async (text: string, label: string) => {
    setOpen(false)
    const ok = await copyText(text)
    say(ok ? 'ok' : 'bad', ok ? `${label} copied` : 'Clipboard blocked')
  }

  const doExport = async (fmt: ExportFormat) => {
    setBusy(fmt)
    try {
      await exportDocument(
        markdown, fmt, title || titleFrom(markdown), subtitle ?? '')
      setOpen(false)
      say('ok', `${fmt.toUpperCase()} saved`)
    } catch (e) {
      setOpen(false)
      say('bad', e instanceof Error ? e.message : 'Export failed')
    } finally {
      setBusy(null)
    }
  }

  const tsv = tablesAsTsv(markdown)

  return (
    <div className="omx-share" ref={wrap}>
      <button
        className={`omx-chat-act ${open ? 'on' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Copy or save this answer"
      >
        {flash
          ? (flash.kind === 'ok' ? <IconCheck size={13} /> : <IconWarning size={13} />)
          : <IconDownload size={13} />}
        {!compact && <span>{flash ? flash.text : 'Save'}</span>}
      </button>

      {open && pos && (
        <div className="omx-share-pop" role="menu"
             style={{ top: pos.top, left: pos.left }}>
          <div className="omx-label">Copy</div>
          <button role="menuitem" className="omx-share-row"
                  onClick={() => void copyOnce(markdown, 'Markdown')}>
            <IconCopy size={13} />
            <span className="n">Markdown</span>
            <span className="h">Keeps the formatting</span>
          </button>
          <button role="menuitem" className="omx-share-row"
                  onClick={() => void copyOnce(asPlainText(markdown), 'Text')}>
            <IconCopy size={13} />
            <span className="n">Plain text</span>
            <span className="h">For email and docs</span>
          </button>
          {/* Only offered when there IS a table. An always-present action that
              usually does nothing teaches people to distrust the menu. */}
          {tsv && (
            <button role="menuitem" className="omx-share-row"
                    onClick={() => void copyOnce(tsv, 'Table')}>
              <IconTable size={13} />
              <span className="n">Table</span>
              <span className="h">Pastes into a spreadsheet</span>
            </button>
          )}

          <div className="omx-label">Save as</div>
          {FORMATS.map((f) => (
            <button
              key={f.id} role="menuitem" className="omx-share-row"
              disabled={busy !== null}
              onClick={() => void doExport(f.id)}
            >
              {busy === f.id ? <span className="omx-spin" /> : <IconDownload size={13} />}
              <span className="n">{f.label}</span>
              <span className="h">{f.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
