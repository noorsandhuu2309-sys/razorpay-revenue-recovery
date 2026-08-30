// Compare — selected objects side by side (§6, §8).
//
// The blueprint's worked example is "select NVIDIA + AMD + Google TPU and
// choose Compare". The temptation is to ask a model to write a comparison
// paragraph. This does not: it lays out what the graph actually holds for each
// object, aligned on shared property keys and shared relationships, and marks
// what is missing as missing.
//
// That distinction is the product. A generated paragraph is worth the same
// whether the underlying data is rich or empty; a table that shows "—" where a
// property was never observed tells the user what OMNIX does not know, which is
// what makes the rest of it trustworthy.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useContextIds, useWorkspace } from '../store/workspace'
import { IconCompare } from '../components/Icons'
import type { OmxObject, OmxRelationship } from '../lib/types'

export function CompareView() {
  const ids = useContextIds()
  const objects = useWorkspace((s) => s.contextObjects)
  const ws = useWorkspace((s) => s.workspaceId)
  const focusOn = useWorkspace((s) => s.focusOn)
  const [rels, setRels] = useState<Record<string, OmxRelationship[]>>({})

  const held: OmxObject[] = ids.map((id) => objects[id]).filter(Boolean)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const out: Record<string, OmxRelationship[]> = {}
      for (const o of held) {
        try {
          const r = await api.objectRelationships(ws, o.id)
          out[o.id] = r.relationships
        } catch { out[o.id] = [] }
      }
      if (!cancelled) setRels(out)
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws, ids.join(',')])

  if (held.length < 2) {
    return (
      <div className="omx-empty">
        <div className="glyph"><IconCompare size={34} /></div>
        <h3>Select two or more objects</h3>
        <p>
          Ctrl-click objects in the Graph, Map or Table to hold them, then
          Compare puts them side by side.
        </p>
      </div>
    )
  }

  // Union of property keys, so a value one object has and another lacks is
  // visible as a gap rather than silently dropped.
  const keys = Array.from(new Set(held.flatMap((o) => Object.keys(o.properties))))
    .sort()

  // A relation counts as shared when every held object has it.
  const relSets = held.map((o) => new Set((rels[o.id] ?? []).map((r) => r.relation)))
  const sharedRels = relSets.length
    ? Array.from(relSets[0]).filter((r) => relSets.every((s) => s.has(r)))
    : []

  return (
    <div className="omx-scroll omx-compare">
      <div className="omx-sec-head">
        <h2>Comparing {held.length}</h2>
        <span className="omx-label">from the graph, not generated</span>
      </div>

      <div className="omx-cmp-grid" style={{ '--n': held.length } as React.CSSProperties}>
        <div className="omx-cmp-row head">
          <div className="omx-cmp-key" />
          {held.map((o) => (
            <button key={o.id} className="omx-cmp-col-head"
                    onClick={() => void focusOn(o)} title={`Focus ${o.name}`}>
              <span className="g" style={{ color: o.color }}>{o.glyph}</span>
              <span className="n">{o.name}</span>
              <span className="omx-label">{o.typeLabel}</span>
            </button>
          ))}
        </div>

        <div className="omx-cmp-row">
          <div className="omx-cmp-key">Provenance</div>
          {held.map((o) => (
            <div key={o.id} className="omx-cmp-cell">
              <span className="omx-prov" data-p={o.provenance}>
                {o.provenanceLabel}
              </span>
            </div>
          ))}
        </div>

        <div className="omx-cmp-row">
          <div className="omx-cmp-key">Confidence</div>
          {held.map((o) => (
            <div key={o.id} className="omx-cmp-cell">
              {o.confidence === null
                ? <span className="omx-null">not measured</span>
                : <span className="omx-mono">{o.confidence.toFixed(2)}</span>}
            </div>
          ))}
        </div>

        <div className="omx-cmp-row">
          <div className="omx-cmp-key">Relationships</div>
          {held.map((o) => (
            <div key={o.id} className="omx-cmp-cell">
              <span className="omx-mono">{rels[o.id]?.length ?? '—'}</span>
            </div>
          ))}
        </div>

        {keys.map((k) => (
          <div className="omx-cmp-row" key={k}>
            <div className="omx-cmp-key">{k}</div>
            {held.map((o) => {
              const v = o.properties[k]
              return (
                <div key={o.id} className="omx-cmp-cell">
                  {v === undefined || v === null || v === ''
                    ? <span className="omx-null">—</span>
                    : <span>{String(v)}</span>}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="omx-sec-head" style={{ marginTop: 22 }}>
        <h2>In common</h2>
        <span className="omx-label">relations all of them have</span>
      </div>
      {sharedRels.length ? (
        <div className="omx-chip-row">
          {sharedRels.map((r) => <span key={r} className="omx-pill">{r.replace(/_/g, ' ')}</span>)}
        </div>
      ) : (
        <p className="omx-empty-line">
          These objects share no relation type. That may mean they are genuinely
          unconnected, or that OMNIX has not researched them deeply enough yet.
        </p>
      )}
    </div>
  )
}
