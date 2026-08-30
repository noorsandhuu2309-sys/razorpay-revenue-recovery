// The artifact card (in the transcript) and the artifact panel (docked beside
// it). See lib/artifacts.ts for what qualifies as an artifact and why.
//
// The panel is a sibling column rather than a modal. A modal would cover the
// conversation, and the most common thing to do with an artifact is read it
// while re-reading the request that produced it — covering one with the other
// is exactly wrong. It is a column at desk widths and an overlay only when the
// window is too narrow to hold both.

import { useEffect, useRef, useState } from 'react'
import {
  fileNameFor, isPreviewable, previewDoc, type Artifact,
} from '../lib/artifacts'
import { Markdown } from './Markdown'
import {
  IconCheck, IconClose, IconCopy, IconDownload, IconExpand,
} from './Icons'

const KIND_LABEL: Record<Artifact['kind'], string> = {
  html: 'Web page', svg: 'Vector image', code: 'Code', doc: 'Document',
}

/** The in-transcript card. Opens the panel; never renders the content itself.
 *
 *  It shows the line count because that is the one thing a reader wants before
 *  deciding to open it — "is this eight lines or four hundred". */
export function ArtifactCard({ a, open, onOpen }: {
  a: Artifact; open: boolean; onOpen: () => void
}) {
  const lines = a.code.split('\n').length
  return (
    <button className={`omx-artcard ${open ? 'on' : ''}`} onClick={onOpen}
            title={`Open ${a.title}`}>
      <span className="ic" aria-hidden="true"><IconExpand size={15} /></span>
      <span className="bd">
        <span className="t">{a.title}</span>
        <span className="s">
          {KIND_LABEL[a.kind]}
          {a.lang && a.kind === 'code' ? ` · ${a.lang}` : ''}
          {' · '}{lines} line{lines === 1 ? '' : 's'}
        </span>
      </span>
      <span className="go">{open ? 'Open' : 'View'}</span>
    </button>
  )
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button className="omx-btn tiny" title="Copy source"
            onClick={() => {
              navigator.clipboard.writeText(text)
                .then(() => setOk(true)).catch(() => setOk(false))
              setTimeout(() => setOk(false), 1600)
            }}>
      {ok ? <IconCheck size={12} /> : <IconCopy size={12} />} {ok ? 'Copied' : 'Copy'}
    </button>
  )
}

export function ArtifactPanel({ a, onClose }: { a: Artifact; onClose: () => void }) {
  const previewable = isPreviewable(a)
  const [tab, setTab] = useState<'preview' | 'source'>(
    previewable ? 'preview' : 'source')
  const frameRef = useRef<HTMLIFrameElement>(null)

  // A different artifact in the same panel starts on ITS best tab, not on
  // whichever one the previous artifact was left on — landing on "Source" for
  // a web page because the last thing open was a Python file reads as the
  // preview being broken.
  useEffect(() => {
    setTab(isPreviewable(a) ? 'preview' : 'source')
  }, [a.id, a.kind])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const download = () => {
    const blob = new Blob([a.code], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const el = document.createElement('a')
    el.href = url
    el.download = fileNameFor(a)
    el.click()
    // Revoked on a later tick: revoking synchronously races the download in
    // Chromium and lands an empty file.
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  }

  return (
    <aside className="omx-artpanel" aria-label={`Artifact: ${a.title}`}>
      <header className="omx-artpanel-head">
        <div className="ttl">
          <span className="t">{a.title}</span>
          <span className="omx-label">{KIND_LABEL[a.kind]}</span>
        </div>
        <div className="acts">
          {previewable && (
            <div className="omx-artpanel-tabs" role="tablist">
              <button role="tab" aria-selected={tab === 'preview'}
                      className={tab === 'preview' ? 'on' : ''}
                      onClick={() => setTab('preview')}>Preview</button>
              <button role="tab" aria-selected={tab === 'source'}
                      className={tab === 'source' ? 'on' : ''}
                      onClick={() => setTab('source')}>Source</button>
            </div>
          )}
          <CopyBtn text={a.code} />
          <button className="omx-btn tiny" onClick={download} title="Download">
            <IconDownload size={12} /> Save
          </button>
          <button className="omx-btn tiny" onClick={onClose} title="Close — Esc">
            <IconClose size={12} />
          </button>
        </div>
      </header>

      <div className="omx-artpanel-body">
        {tab === 'source' || !previewable ? (
          <pre className="omx-artpanel-src"><code>{a.code}</code></pre>
        ) : a.kind === 'doc' ? (
          <div className="omx-artpanel-doc"><Markdown text={a.code} /></div>
        ) : (
          // srcDoc + a sandbox WITHOUT allow-same-origin. See previewDoc() for
          // why that pairing is the entire safety argument for running
          // model-authored markup here.
          <iframe
            ref={frameRef}
            className="omx-artpanel-frame"
            title={a.title}
            sandbox="allow-scripts allow-forms allow-modals allow-popups"
            srcDoc={previewDoc(a)}
          />
        )}
      </div>
    </aside>
  )
}
