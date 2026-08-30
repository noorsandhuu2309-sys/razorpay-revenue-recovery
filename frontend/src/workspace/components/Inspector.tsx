// One inspector for every object type. Sections are driven by what the object
// actually has, not by a per-type component — a Company and a Claim differ in
// their properties, not in how you interrogate them.
//
// The provenance badge is deliberately near the top and always present. The
// spec's rule is that a user must be able to tell where OMNIX got something,
// and burying that under a fold defeats it.

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import { describeEdge } from '../lib/graphModel'
import { RelationshipInspector } from './RelationshipInspector'
import { CountryDossier } from '../views/TerraIntel'
import type { OmxEvent, OmxObject, OmxRelationship, OmxSource } from '../lib/types'

function Provenance({ p, label }: { p: string; label: string }) {
  const explain: Record<string, string> = {
    user_created: 'You created or edited this.',
    verified: 'Confirmed against authoritative data.',
    source_backed: 'Backed by a retrieved source.',
    ai_inferred: 'Extracted by a model. Not independently confirmed.',
  }
  return (
    <span className="omx-prov" data-p={p} title={explain[p] ?? ''}>{label}</span>
  )
}

/** The inspector is contextual: what it shows follows what the canvas says the
 *  user is interrogating.
 *
 *      an edge selected   → the relationship, its measurement and its evidence
 *      one object         → that object
 *      several objects    → what they have in common, then the newest of them
 *
 *  Routing here rather than in the graph view means every surface that can
 *  select — Map, Table, the command palette — gets the same behaviour without
 *  knowing anything about it. */
export function Inspector() {
  const activeEdge = useGraphUi((s) => s.activeEdge)
  const graph = useWorkspace((s) => s.graph)
  const ontology = useWorkspace((s) => s.ontology)

  const symmetricRelations = useMemo(() => {
    const set = new Set<string>()
    for (const r of ontology?.relations ?? []) if (r.symmetric) set.add(r.key)
    return set
  }, [ontology])

  // Resolved from the payload rather than passed down, so the relationship
  // survives an inspector that was opened from anywhere.
  const edgeCtx = useMemo(() => {
    if (!activeEdge || !graph) return null
    const raw = graph.edges.find(
      (e) => (e.id ?? `${e.source}|${e.target}|${e.relation}`) === activeEdge)
    if (!raw) return null
    const meta = describeEdge(raw, symmetricRelations)
    // `source`/`target` are traversal order; the stored direction is in the
    // label. Swapping here is what makes the inspector's sentence agree with
    // the arrow the canvas drew.
    const srcId = meta.inbound ? raw.target : raw.source
    const dstId = meta.inbound ? raw.source : raw.target
    const from = graph.nodes.find((n) => n.id === srcId)
    const to = graph.nodes.find((n) => n.id === dstId)
    if (!from || !to) return null
    return { meta, from, to }
  }, [activeEdge, graph, symmetricRelations])

  if (edgeCtx) {
    return (
      <RelationshipInspector
        edge={edgeCtx.meta} from={edgeCtx.from} to={edgeCtx.to}
      />
    )
  }
  return <ObjectInspector />
}

function ObjectInspector() {
  const primary = useWorkspace((s) => s.primary)
  const selected = useWorkspace((s) => s.selected)
  const ws = useWorkspace((s) => s.workspaceId)
  const ctxObjects = useWorkspace((s) => s.contextObjects)
  const select = useWorkspace((s) => s.select)
  const pin = useWorkspace((s) => s.pin)
  const expandNode = useWorkspace((s) => s.expandNode)
  const refreshSummary = useWorkspace((s) => s.refreshSummary)

  const [obj, setObj] = useState<OmxObject | null>(null)
  const [rels, setRels] = useState<OmxRelationship[]>([])
  const [srcs, setSrcs] = useState<OmxSource[]>([])
  const [events, setEvents] = useState<OmxEvent[]>([])
  const [loading, setLoading] = useState(false)
  // Names for the other end of each relationship. Without this the list falls
  // back to raw ids, which is useless to a reader — the whole point of the
  // section is "what is this connected to".
  const [names, setNames] = useState<Record<string, OmxObject>>({})

  useEffect(() => {
    if (!primary || !ws) { setObj(null); return }
    let cancelled = false
    setLoading(true)
    Promise.all([
      api.object(ws, primary).catch(() => null),
      api.objectRelationships(ws, primary).catch(() => ({ relationships: [] })),
      api.objectSources(ws, primary).catch(() => ({ sources: [] })),
      api.objectEvents(ws, primary).catch(() => ({ events: [] })),
    ]).then(([o, r, s, e]) => {
      if (cancelled) return
      setObj(o)
      setRels(r.relationships)
      setSrcs(s.sources)
      setEvents(e.events)
      setLoading(false)

      // Resolve the far end of each edge. Bounded to what is actually shown.
      const need = Array.from(new Set(
        r.relationships.slice(0, 30)
          .map((rel) => (rel.src === primary ? rel.dst : rel.src))
          .filter((id) => !ctxObjects[id])))
      if (need.length) {
        Promise.all(need.map((id) => api.object(ws, id).catch(() => null)))
          .then((found) => {
            if (cancelled) return
            const map: Record<string, OmxObject> = {}
            for (const f of found) if (f) map[f.id] = f
            setNames(map)
          })
      } else {
        setNames({})
      }
    })
    return () => { cancelled = true }
  }, [primary, ws, ctxObjects])

  if (!primary) {
    return (
      <div className="omx-inspector">
        <div className="omx-section">
          <h4>Inspector</h4>
          <p style={{ color: 'var(--omx-text-faint)', fontSize: 12.5, margin: 0 }}>
            Select an object to inspect it. Ctrl-click to add more to context.
          </p>
        </div>
      </div>
    )
  }

  if (!obj) {
    return (
      <div className="omx-inspector">
        <div className="omx-section">
          <h4>Inspector</h4>
          {loading ? <span className="omx-spin" /> : <span className="omx-label">Not found</span>}
        </div>
      </div>
    )
  }

  const props = Object.entries(obj.properties || {})
    .filter(([k, v]) => v !== null && v !== '' && k !== 'raw')

  const toggleTrack = async () => {
    const next = await api.track(ws, obj.id, !obj.tracked)
    setObj(next)
    void refreshSummary()
  }

  return (
    <div className="omx-inspector">
      {/* Multi-selection. The inspector still profiles ONE object — there is
          no meaningful union of two dossiers — but it says plainly that more
          is held and that the comparison verbs are on the action bar, rather
          than leaving the reader wondering why the panel ignored their second
          click. */}
      {selected.length > 1 && (
        <div className="omx-section multi">
          <div className="omx-label">{selected.length} objects held</div>
          <p className="omx-empty-line">
            Showing the most recent. Compare and Find Connections act on all of
            them, from the action bar below.
          </p>
        </div>
      )}

      {/* Overview */}
      <div className="omx-section">
        <div className="omx-insp-head">
          <span className="gl" style={{ color: obj.color }}>{obj.glyph}</span>
          <div className="t">
            <h3>{obj.name}</h3>
            <div className="omx-label">
              {obj.typeLabel} · {obj.familyLabel} · {obj.domain}
            </div>
          </div>
        </div>
        <div className="omx-insp-badges">
          <Provenance p={obj.provenance} label={obj.provenanceLabel} />
          {obj.tracked && <span className="omx-pill on">tracked</span>}
        </div>
        {obj.description && (
          <p style={{ marginTop: 12, marginBottom: 0, fontSize: 12.5, color: 'var(--omx-text-dim)', lineHeight: 1.6 }}>
            {obj.description}
          </p>
        )}

        {/* Three measured quantities, side by side. `confidence` is nullable
            and prints as "not measured" rather than 0% — a zero here would
            claim the model was certain it was wrong. */}
        <div className="omx-insp-tiles">
          <div className="tile">
            <div className="omx-label">confidence</div>
            <div className="v">
              {obj.confidence == null
                ? <span className="omx-null">not measured</span>
                : `${Math.round(obj.confidence * 100)}%`}
            </div>
          </div>
          <div className="tile">
            <div className="omx-label">salience</div>
            <div className="v gold">{(obj.salience ?? 0).toFixed(2)}</div>
          </div>
          <div className="tile">
            <div className="omx-label">degree</div>
            <div className="v gold">{obj.degree ?? <span className="omx-null">—</span>}</div>
          </div>
        </div>
      </div>

      {/* Actions — context-sensitive per §11 */}
      <div className="omx-section">
        <h4>Actions</h4>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="omx-btn" onClick={() => void expandNode(obj.id)}>Expand</button>
          <button className="omx-btn" onClick={() => pin(obj.id)}>Pin to context</button>
          <button className={`omx-btn ${obj.tracked ? 'on' : ''}`} onClick={() => void toggleTrack()}>
            {obj.tracked ? 'Untrack' : 'Track'}
          </button>
        </div>
      </div>

      {/* Country dossier — TERRA's profile/economy panel, restored. Only for
          objects that actually have an ISO code to look up. */}
      {obj.type === 'country' && typeof obj.properties?.iso2 === 'string' && (
        <CountryDossier iso={String(obj.properties.iso2)} />
      )}

      {/* Properties */}
      {props.length > 0 && (
        <div className="omx-section">
          <h4>Properties</h4>
          {props.map(([k, v]) => (
            <div className="omx-kv" key={k}>
              <span className="k">{k}</span>
              <span className="v">{String(v).slice(0, 120)}</span>
            </div>
          ))}
          {obj.confidence === null ? (
            <div className="omx-kv">
              <span className="k">confidence</span>
              {/* Not a zero. "Not measured" is a different statement. */}
              <span className="v" style={{ color: 'var(--omx-text-faint)' }}>not measured</span>
            </div>
          ) : (
            <div className="omx-kv">
              <span className="k">confidence</span>
              <span className="v">{Math.round(obj.confidence * 100)}%</span>
            </div>
          )}
        </div>
      )}

      {/* Relationships */}
      <div className="omx-section">
        <h4>Relationships · {rels.length}</h4>
        {rels.length === 0 && <span className="omx-label">None recorded</span>}
        {rels.slice(0, 30).map((r) => {
          const otherId = r.src === obj.id ? r.dst : r.src
          const other = ctxObjects[otherId] ?? names[otherId]
          return (
            <div className="omx-rel" key={r.id} onClick={() => select(otherId, 'graph')}>
              <span className="lbl">{r.src === obj.id ? r.label : `${r.label} ←`}</span>
              <span style={{
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                color: other ? undefined : 'var(--omx-text-faint)',
              }}>
                {other ? (
                  <>
                    <span style={{ color: other.color, marginRight: 5 }}>{other.glyph}</span>
                    {other.name}
                  </>
                ) : 'resolving…'}
              </span>
              <Provenance p={r.provenance} label={r.provenance === 'ai_inferred' ? 'AI' : 'SRC'} />
            </div>
          )
        })}
      </div>

      {/* Timeline */}
      {events.length > 0 && (
        <div className="omx-section">
          <h4>Timeline · {events.length}</h4>
          {events.slice(0, 8).map((e) => (
            <div key={e.id} style={{ padding: '5px 0', borderBottom: '1px solid var(--omx-line-neutral)' }}>
              <div style={{ fontSize: 12 }}>{e.title}</div>
              <div className="omx-label" style={{ marginTop: 2 }}>
                {new Date(e.occurredAt).toLocaleDateString()} · {e.relevance}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Sources — the provenance answer */}
      <div className="omx-section">
        <h4>Sources · {srcs.length}</h4>
        {srcs.length === 0 && (
          <span className="omx-label">No evidence attached</span>
        )}
        {srcs.map((s) => (
          <a
            key={s.id}
            href={s.url}
            target="_blank"
            rel="noreferrer noopener"
            style={{ display: 'block', padding: '6px 0', textDecoration: 'none', color: 'inherit' }}
          >
            <div style={{ fontSize: 12, color: 'var(--omx-info)' }}>
              {s.title || s.url}
            </div>
            <div className="omx-label" style={{ marginTop: 2 }}>
              {s.publisher} {s.tierLabel && `· ${s.tierLabel}`}
            </div>
            {s.excerpt && (
              <div style={{ fontSize: 11.5, color: 'var(--omx-text-faint)', marginTop: 3 }}>
                {s.excerpt.slice(0, 160)}…
              </div>
            )}
          </a>
        ))}
      </div>
    </div>
  )
}
