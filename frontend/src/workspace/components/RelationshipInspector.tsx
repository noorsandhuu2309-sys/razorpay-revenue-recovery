// The relationship inspector — "why does OMNIX believe these two are
// connected?", answered without leaving the canvas.
//
// It is the missing half of the graph. Objects have had an inspector since the
// substrate landed; relationships had nothing, so the evidence chain the
// product is built on stopped at the node. Clicking a line now opens this.
//
// It is scrupulous about what it does not know. In this dataset relationship
// `confidence` is null on every record and `/api/relationships/{id}/sources`
// returns an empty list, so:
//
//   * confidence renders as "not measured", never as 0% and never as an
//     invented number. A fabricated 93% is worse than a blank, because the
//     reader cannot tell it is fabricated.
//   * strength is labelled as what it is — a decayed, corroboration-weighted
//     edge weight — rather than dressed up as a probability.
//   * an empty evidence list is stated, with the reason, rather than hidden by
//     omitting the section.
//
// It sits in the right-hand column alongside the object inspector rather than
// floating over the canvas, so it never covers the thing it is describing.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import {
  CERTAINTY_LABEL, CLASS_COLOR, CLASS_LABEL, confidenceText, shortDate,
  strengthText, type EdgeMeta,
} from '../lib/graphModel'
import { IconClose, IconFocus, IconLink, IconQuery, IconTrack } from './Icons'
import type { OmxObject, OmxRelationship, OmxSource } from '../lib/types'

export function RelationshipInspector({ edge, from, to }: {
  edge: EdgeMeta
  /** The semantic source and target — already resolved through the label
   *  convention by the caller, so this component never has to think about
   *  traversal order. */
  from: OmxObject
  to: OmxObject
}) {
  const ws = useWorkspace((s) => s.workspaceId)
  const setView = useWorkspace((s) => s.setView)
  const select = useWorkspace((s) => s.select)
  const selectMany = useWorkspace((s) => s.selectMany)
  const setBusy = useWorkspace((s) => s.setBusy)
  const setActiveEdge = useGraphUi((s) => s.setActiveEdge)
  const setPath = useGraphUi((s) => s.setPath)
  const setFocusMode = useGraphUi((s) => s.setFocusMode)

  const [record, setRecord] = useState<OmxRelationship | null>(null)
  const [sources, setSources] = useState<OmxSource[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [note, setNote] = useState('')

  // The subgraph payload carries no confidence and no timestamps — those live
  // on the relationship record. Resolved through the endpoint the object
  // inspector already uses rather than by listing all 2000 relationships.
  useEffect(() => {
    if (!ws) return
    let cancelled = false
    setLoading(true)
    setRecord(null)
    setSources(null)
    api.objectRelationships(ws, edge.e.source)
      .then(({ relationships }) => {
        if (cancelled) return
        const hit = relationships.find((r) => r.id === edge.e.id)
          ?? relationships.find((r) =>
            r.relation === edge.e.relation
            && ((r.src === edge.e.source && r.dst === edge.e.target)
              || (r.src === edge.e.target && r.dst === edge.e.source)))
        setRecord(hit ?? null)
        setLoading(false)
        const relId = hit?.id ?? edge.e.id
        if (!relId) { setSources([]); return }
        return api.relationshipSources(ws, relId)
          .then((r) => { if (!cancelled) setSources(r.sources) })
          .catch(() => { if (!cancelled) setSources([]) })
      })
      .catch(() => {
        if (cancelled) return
        setRecord(null); setSources([]); setLoading(false)
      })
    return () => { cancelled = true }
  }, [ws, edge.e.id, edge.e.source, edge.e.target, edge.e.relation])

  const cls = edge.relClass
  const observations = record?.observations ?? edge.e.count ?? 1
  const sentiment = record?.sentiment ?? edge.e.sentiment ?? 0

  const focusRelationship = () => {
    // Both endpoints, and the pair marked as a path so the canvas draws the
    // link between them at full contrast with everything else pulled back.
    selectMany([from.id, to.id], 'graph')
    setPath([from.id, to.id], `${from.name} — ${to.name}`)
    setFocusMode(true)
  }

  const track = async () => {
    setBusy('Tracking…')
    try {
      await Promise.all([
        api.track(ws, from.id, true),
        api.track(ws, to.id, true),
      ])
      setNote('Both objects are now tracked. Changes appear under Activity.')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Tracking failed.')
    } finally { setBusy(null) }
  }

  const ask = () => {
    // Selection IS the context NOVA receives, so putting both endpoints in it
    // is what lets the user ask "why are they in conflict" without naming them.
    selectMany([from.id, to.id], 'graph')
    setView('nova')
  }

  return (
    <div className="omx-inspector">
      <div className="omx-section">
        <div className="omx-insp-head">
          <span className="gl" style={{ color: CLASS_COLOR[cls] }}><IconLink size={17} /></span>
          <div className="t">
            <h3>{edge.label.toUpperCase()}</h3>
            <div className="omx-label">Relationship · {CLASS_LABEL[cls]}</div>
          </div>
          <button className="omx-btn icon" onClick={() => setActiveEdge(null)}
                  title="Close" aria-label="Close relationship inspector">
            <IconClose size={14} />
          </button>
        </div>

        {/* The claim itself, as a sentence. Direction comes from the label
            convention, not from source/target — reading it off the traversal
            order produces arrows that contradict their own labels. */}
        <div className="omx-rel-claim">
          <button className="ep" onClick={() => select(from.id, 'graph')}
                  title={`Inspect ${from.name}`}>
            <span className="g" style={{ color: from.color }}>{from.glyph}</span>
            {from.name}
          </button>
          <span className="arrow" aria-label={edge.symmetric ? 'mutual' : 'directed'}>
            {edge.symmetric ? '↔' : '→'}
          </span>
          <button className="ep" onClick={() => select(to.id, 'graph')}
                  title={`Inspect ${to.name}`}>
            <span className="g" style={{ color: to.color }}>{to.glyph}</span>
            {to.name}
          </button>
        </div>

        <div className="omx-insp-badges">
          <span className="omx-prov" data-p={edge.e.provenance}>
            {CERTAINTY_LABEL[edge.certainty]}
          </span>
          {edge.symmetric && <span className="omx-pill">mutual</span>}
        </div>
      </div>

      {/* Measured quantities. Each one says what it is measuring, because
          "strength 62%" with no unit is the kind of number a reader either
          over-trusts or ignores entirely. */}
      <div className="omx-section">
        <h4>Measurement</h4>
        <div className="omx-insp-tiles">
          <div className="tile">
            <div className="omx-label">confidence</div>
            <div className="v">
              {record?.confidence == null
                ? <span className="omx-null">not measured</span>
                : confidenceText(record.confidence)}
            </div>
          </div>
          <div className="tile">
            <div className="omx-label">strength</div>
            <div className="v gold">{strengthText(edge.strength)}</div>
          </div>
          <div className="tile">
            <div className="omx-label">observations</div>
            <div className="v gold">{observations}</div>
          </div>
        </div>
        <div className="omx-kv">
          <span className="k">line style</span>
          <span className="v">
            {edge.certainty === 'confirmed' ? 'solid — corroborated more than once'
              : edge.certainty === 'inferred' ? 'dashed — inferred or seen once'
                : 'dotted — incidental co-occurrence'}
          </span>
        </div>
        <div className="omx-kv">
          <span className="k">strength basis</span>
          <span className="v">
            edge weight {(edge.e.weight ?? 0).toFixed(2)}, decayed over time
          </span>
        </div>
        {sentiment !== 0 && (
          <div className="omx-kv">
            <span className="k">sentiment</span>
            <span className="v" style={{
              color: sentiment > 0 ? 'var(--omx-pos)' : 'var(--omx-neg)',
            }}>
              {sentiment > 0 ? 'positive' : 'negative'} {sentiment.toFixed(2)}
            </span>
          </div>
        )}
        {record && (
          <>
            <div className="omx-kv">
              <span className="k">first observed</span>
              <span className="v">{shortDate(record.firstSeen)}</span>
            </div>
            <div className="omx-kv">
              <span className="k">last observed</span>
              <span className="v">{shortDate(record.lastSeen)}</span>
            </div>
          </>
        )}
        {!record && !loading && (
          <p className="omx-empty-line">
            No stored record for this link — it exists in the traversal but has
            no relationship row, so first and last observed are unavailable.
          </p>
        )}
      </div>

      <div className="omx-section">
        <h4>Actions</h4>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="omx-btn" onClick={focusRelationship}>
            <IconFocus size={12} /> Focus relationship
          </button>
          <button className="omx-btn" onClick={() => setView('timeline')}>Timeline</button>
          <button className="omx-btn" onClick={() => setView('claims')}>Claims</button>
          <button className="omx-btn" onClick={() => void track()}>
            <IconTrack size={12} /> Track both
          </button>
          <button className="omx-btn" onClick={ask}>
            <IconQuery size={12} /> Ask REVORA
          </button>
        </div>
        {note && <p className="omx-empty-line" style={{ marginTop: 8 }}>{note}</p>}
      </div>

      {/* Evidence. An empty list is an answer, and it is stated as one. */}
      <div className="omx-section">
        <h4>Evidence · {sources?.length ?? 0}</h4>
        {sources === null && <span className="omx-spin" />}
        {sources !== null && sources.length === 0 && (
          <p className="omx-empty-line">
            No sources are attached to this relationship. It was recorded from
            {edge.e.provenance === 'ai_inferred'
              ? ' model extraction, so nothing independent stands behind it yet.'
              : ' upstream ingestion without per-link citations.'}
            {' '}The objects at either end may still carry sources of their own.
          </p>
        )}
        {sources?.map((s) => (
          <a key={s.id} href={s.url} target="_blank" rel="noreferrer noopener"
             className="omx-src-row">
            <div className="t">{s.title || s.url}</div>
            <div className="omx-label">
              {s.publisher}{s.tierLabel ? ` · ${s.tierLabel}` : ''}
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
