// Outputs — what Create produced (§12).
//
// Two panes: the things this Space has made, and the one currently open. The
// document renderer walks the same `sections` shape the backend emits, which is
// why every output style draws here without this file knowing what a
// "presentation" is.
//
// The provenance strip at the top is not decoration. An output inherits the
// weakest provenance of its inputs, and a reader who cannot see that a
// confident-sounding report was built from AI-inferred entities has been
// misled by their own tool.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { IconClose } from '../components/Icons'
import { ViewIntro } from '../components/ViewIntro'
import type { OmxOutput, OutputSection, OutputStyle } from '../lib/types'

function Section({ section }: { section: OutputSection }) {
  return (
    <section className="omx-doc-section">
      <h3>{section.heading}</h3>

      {section.kind === 'text' && (
        <>
          {section.generated && (
            <div className="omx-doc-generated">
              Written by a model from the material in this document. Every other
              section is measured.
            </div>
          )}
          {section.body.split('\n\n').filter(Boolean).map((para, i) => (
            <p key={i}>{para}</p>
          ))}
        </>
      )}

      {section.kind === 'list' && (
        <ul className="omx-doc-list">
          {section.items.map((item, i) => <li key={i}>{item}</li>)}
        </ul>
      )}

      {section.kind === 'table' && (
        <div className="omx-doc-tablewrap">
          <table className="omx-table">
            <thead>
              <tr>{section.columns.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {section.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => <td key={j}>{String(cell)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section.kind === 'metrics' && (
        <div className="omx-doc-tiles">
          {section.metrics.map((m) => (
            <div className="omx-doc-tile" key={m.label}>
              <div className="v">{m.value}</div>
              <div className="l">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      {section.kind === 'chart' && (() => {
        const top = Math.max(...section.series.map((p) => p.value), 1)
        return (
          <div className="omx-doc-bars">
            {section.series.map((p) => (
              <div className="omx-doc-bar" key={p.label}>
                <span className="t">{p.label}</span>
                <span className="track">
                  <span className="fill"
                        style={{ width: `${Math.max(1, (p.value / top) * 100)}%` }} />
                </span>
                <span className="n">{p.value}</span>
              </div>
            ))}
          </div>
        )
      })()}
    </section>
  )
}

function Document({ id, onClose }: { id: string; onClose: () => void }) {
  const select = useWorkspace((s) => s.select)
  const [doc, setDoc] = useState<OmxOutput | null>(null)
  const [error, setError] = useState('')
  const [styles, setStyles] = useState<OutputStyle[]>([])

  useEffect(() => {
    let live = true
    // Discard a reply that lost the race: the key can change while a request
    // is still in flight, and the slower reply would otherwise land last and
    // overwrite the newer one.
    setDoc(null)
    setError('')
    api.output(id)
      .then((d) => { if (live) setDoc(d) })
      .catch((e) => { if (live) setError(String(e.message || e)) })
    return () => { live = false }
  }, [id])

  // The server owns the format list. Fetched once and reused for every document
  // opened in this session.
  useEffect(() => {
    api.outputStyles().then((r) => setStyles(r.styles)).catch(() => { /* falls
      back to the two formats every style implements */ })
  }, [])

  if (error) return <div className="omx-doc"><p className="omx-label">{error}</p></div>
  if (!doc?.content) {
    return <div className="omx-doc"><span className="omx-spin" /></div>
  }

  const c = doc.content
  const formats = styles.find((s) => s.key === c.style)?.formats ?? ['md', 'html']
  return (
    <div className="omx-doc">
      <div className="omx-doc-head">
        <div>
          <div className="omx-label">{c.styleLabel}</div>
          <h2>{c.title}</h2>
        </div>
        <div className="omx-doc-actions">
          {/* Formats come from the style the document was built with, not from
              a list here. The hardcoded ['md','html','csv'] this replaces had
              already drifted: it offered CSV on documents with no table to put
              in one, and could never have offered a format added later. */}
          {(doc.tags || []).includes('output') && formats.map((f) => (
            f === 'pdf'
              // PDF is the print-ready page; the browser writes the file. It
              // must OPEN, not download — downloading it saves an .html the
              // user then has to find and open themselves to reach the dialog.
              ? (
                <a key={f} className="omx-btn primary"
                   href={api.outputHref(doc.id, 'pdf')}
                   target="_blank" rel="noreferrer"
                   title="Opens the print dialog — choose 'Save as PDF'">
                  PDF
                </a>
              )
              : (
                <a key={f} className="omx-btn" href={api.outputHref(doc.id, f, true)}
                   download>{f.toUpperCase()}</a>
              )
          ))}
          <a className="omx-btn" href={api.outputHref(doc.id, 'html')}
             target="_blank" rel="noreferrer">Open</a>
          <button className="omx-btn icon" onClick={onClose} title="Back to list"
                  aria-label="Back to list"><IconClose size={14} /></button>
        </div>
      </div>

      <div className="omx-doc-prov" data-floor={c.provenance.floor}>
        <strong>{c.provenance.floorLabel}</strong>
        <span>
          {' '}— this output is only as trustworthy as its weakest input.
          {c.provenance.synthesised
            ? ' One section was written by a model; the rest are measured.'
            : ' Every section is measured.'}
        </span>
        <span className="counts">
          {Object.entries(c.counts).filter(([, v]) => v)
            .map(([k, v]) => `${v} ${k}`).join(' · ')}
        </span>
      </div>

      {/* Built from these — clicking selects, so an output feeds back into
          context exactly like any other object. */}
      {!!c.inputs.objects.length && (
        <div className="omx-doc-inputs">
          <span className="omx-label">Built from</span>
          {c.inputs.objects.map((o) => (
            <button key={o.id} className="omx-chip"
                    onClick={() => select(o.id, 'outputs')}>{o.name}</button>
          ))}
        </div>
      )}

      {c.sections.map((s, i) => <Section key={i} section={s} />)}
    </div>
  )
}

export function OutputsView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const activeOutput = useWorkspace((s) => s.activeOutput)
  const openOutput = useWorkspace((s) => s.openOutput)
  const [list, setList] = useState<OmxOutput[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!ws) return
    let live = true
    // Discard a reply that lost the race: the key can change while a request
    // is still in flight, and the slower reply would otherwise land last and
    // overwrite the newer one.
    setLoading(true)
    api.outputs(ws)
      .then((r) => { if (live) setList(r.outputs) })
      .catch(() => { if (live) setList([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws, activeOutput])

  if (activeOutput) {
    return <Document id={activeOutput} onClose={() => openOutput(null)} />
  }
  if (loading) return <div className="omx-scroll"><span className="omx-spin" /></div>

  if (!list.length) {
    return (
      <div className="omx-empty">
        <div className="glyph">✚</div>
        <h3>No outputs yet</h3>
        <p>
          Select objects in any view, then choose <strong>Create</strong> on the
          action bar — report, brief, dashboard, timeline, chart, spreadsheet,
          presentation, page or note. What you make becomes an object in this
          Space, so it can be selected and fed back into an agent.
        </p>
      </div>
    )
  }

  return (
    <div className="omx-scroll">
      <ViewIntro
        id="outputs"
        title="Outputs"
        what="The documents this Space has produced — dossier, report, brief,
              dashboard, timeline, chart, spreadsheet, page or note. They are
              assembled from objects you already hold rather than written from
              nothing, so every line traces back to the sources behind it. An
              output inherits the WEAKEST provenance of its inputs: a
              confident-looking report built on one AI-inferred entity is
              labelled as such. Each renders to Markdown, HTML or PDF depending
              on its style."
        how="Select objects anywhere in OMNIX, open Create in the action bar
             that appears, and pick a style. The style list comes from the
             server, so it can only offer what it can actually produce."
      />
      <div className="omx-label" style={{ marginBottom: 12 }}>
        {list.length} output{list.length === 1 ? '' : 's'}
      </div>
      <div className="omx-cards">
        {list.map((o) => (
          <button key={o.id} className="omx-card" onClick={() => openOutput(o.id)}>
            <div className="omx-card-head">
              <span className="omx-label">{o.type}</span>
              <span className="omx-label">
                {new Date(o.createdAt).toLocaleDateString(undefined,
                  { month: 'short', day: 'numeric' })}
              </span>
            </div>
            <div className="omx-card-title">{o.title}</div>
            <div className="omx-label">
              {(o.tags || []).filter((t) => t !== 'output').join(' · ')}
              {o.version > 1 ? ` · v${o.version}` : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
