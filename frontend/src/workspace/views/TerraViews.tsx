// The TERRA surfaces, kept as first-class views of the workspace.
//
// These read the existing /api/terra/* endpoints directly rather than going
// through the object graph. That is deliberate: the news corpus is 1,500
// articles refreshed every 15 minutes, and projecting all of it into `object`
// rows would bloat the graph with material nobody selected. The graph holds
// what research and the TERRA bridge promoted; these views show the live feed
// underneath it, and selecting anything here still drives the shared selection.

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { IconAnalysis, IconFlag, IconSwap } from '../components/Icons'
import { Markdown } from '../components/Markdown'

/** Takes an icon node rather than a character — see the note on Views.tsx's
 *  copy of this component. */
function Empty({ glyph, title, body, action }: {
  glyph: React.ReactNode; title: string; body: string; action?: React.ReactNode
}) {
  return (
    <div className="omx-empty">
      <div className="glyph">{glyph}</div>
      <h3>{title}</h3>
      <p>{body}</p>
      {action && <div className="acts">{action}</div>}
    </div>
  )
}

const ago = (ts: number) => {
  const d = Date.now() / 1000 - ts
  if (d < 60) return 'now'
  if (d < 3600) return `${Math.floor(d / 60)}m`
  if (d < 86400) return `${Math.floor(d / 3600)}h`
  return `${Math.floor(d / 86400)}d`
}

// ---------------------------------------------------------------------------
// News — the live world feed
// ---------------------------------------------------------------------------
interface Cluster {
  id: string; title: string; url: string; size: number
  sources: Record<string, number>
  countries?: string[]; domains?: string[]
  score?: number; ts?: number; summary?: string
  sentiment?: number
}

export function NewsView() {
  const [events, setEvents] = useState<Cluster[]>([])
  const [loading, setLoading] = useState(true)
  const [domain, setDomain] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    fetch(`/api/terra/events?limit=60${domain ? `&domain=${domain}` : ''}`)
      .then((r) => r.json())
      .then((d) => setEvents(d.events || []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false))
  }
  useEffect(load, [domain])

  const domains = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) for (const d of e.domains || []) set.add(d)
    return [...set].sort()
  }, [events])

  if (loading && !events.length) {
    return <div className="omx-scroll"><span className="omx-spin" /></div>
  }
  if (!events.length) {
    return <Empty glyph={<IconFlag size={34} />} title="No stories" body="TERRA's ingest has not produced clustered stories yet." />
  }

  return (
    <div className="omx-scroll">
      <div className="omx-toolbar">
        <span className="omx-live"><i /> Live</span>
        <span className="omx-label">{events.length} clustered stories</span>
        <div style={{ flex: 1 }} />
        <button className={`omx-btn ${!domain ? 'on' : ''}`} onClick={() => setDomain('')}>All</button>
        {domains.slice(0, 7).map((d) => (
          <button key={d} className={`omx-btn ${domain === d ? 'on' : ''}`}
                  onClick={() => setDomain(d)}>{d}</button>
        ))}
        <button className="omx-btn" onClick={load}>Refresh</button>
      </div>

      <div style={{ display: 'grid', gap: 9 }}>
        {events.map((e) => {
          const srcCount = Object.keys(e.sources || {}).length
          return (
            <div className="omx-card click" key={e.id}
                 onClick={() => setOpen(open === e.id ? null : e.id)}>
              <div style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
                <span className="omx-rank">{e.size}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, lineHeight: 1.45 }}>{e.title}</div>
                  <div className="omx-label" style={{ marginTop: 6, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <span>{srcCount} source{srcCount > 1 ? 's' : ''}</span>
                    {e.ts && <span>{ago(e.ts)}</span>}
                    {(e.countries || []).slice(0, 4).map((c) => (
                      <span key={c} style={{ color: 'var(--omx-gold)' }}>{c}</span>
                    ))}
                    {typeof e.sentiment === 'number' && (
                      <span style={{
                        color: e.sentiment > 0.12 ? 'var(--omx-pos)'
                          : e.sentiment < -0.12 ? 'var(--omx-neg)' : 'var(--omx-text-faint)',
                      }}>
                        {e.sentiment > 0.12 ? 'positive' : e.sentiment < -0.12 ? 'negative' : 'neutral'}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {open === e.id && (
                <div style={{ marginTop: 11, paddingTop: 10, borderTop: '1px solid var(--omx-line)' }}>
                  {e.summary && (
                    <p style={{ margin: '0 0 9px', fontSize: 12.5, color: 'var(--omx-text-dim)' }}>
                      {e.summary}
                    </p>
                  )}
                  <div className="omx-label" style={{ marginBottom: 6 }}>Carried by</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {Object.entries(e.sources || {}).map(([name, n]) => (
                      <span key={name} className="omx-pill">{name}{n > 1 ? ` ×${n}` : ''}</span>
                    ))}
                  </div>
                  {e.url && (
                    <a href={e.url} target="_blank" rel="noreferrer noopener"
                       className="omx-btn" style={{ marginTop: 10, display: 'inline-flex' }}>
                      Open story
                    </a>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Relationships — the edge ledger
// ---------------------------------------------------------------------------
interface RelRow {
  source: { id: string; name: string; color: string; glyph: string }
  target: { id: string; name: string; color: string; glyph: string }
  relation: string; label: string; weight: number; count: number
  sentiment: number; static: boolean; llm: boolean
  articles?: string[]
}

export function RelationshipsView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  const [rows, setRows] = useState<RelRow[]>([])
  const [loading, setLoading] = useState(true)
  const [rel, setRel] = useState('')

  useEffect(() => {
    let live = true
    // Discard a reply that lost the race: the key can change while a request
    // is still in flight, and the slower reply would otherwise land last and
    // overwrite the newer one.
    setLoading(true)
    // Encoded: relation names come from the data, and one containing `&`, `#`
    // or a space would otherwise split the query string and silently filter by
    // something other than what the user picked.
    const q = rel ? `&relations=${encodeURIComponent(rel)}` : ''
    fetch(`/api/terra/relationships?limit=200${q}`)
      .then((r) => r.json())
      .then((d) => { if (live) setRows(d.rows || []) })
      .catch(() => { if (live) setRows([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [rel])

  const relations = useMemo(() => {
    const set = new Set<string>()
    for (const r of rows) set.add(r.relation)
    return [...set].sort()
  }, [rows])

  // Clicking an endpoint resolves it into the workspace so the rest of the
  // app follows — the same contract the Map honours.
  const pick = async (terraId: string) => {
    if (!ws) return
    try {
      const res = await api.objects(ws, { externalId: `terra:${terraId}` })
      if (res.objects[0]) select(res.objects[0].id, 'graph')
    } catch { /* not projected */ }
  }

  if (loading && !rows.length) {
    return <div className="omx-scroll"><span className="omx-spin" /></div>
  }
  if (!rows.length) {
    return <Empty glyph={<IconSwap size={34} />} title="No relationships" body="TERRA has not extracted relationships from the current corpus." />
  }

  return (
    <div className="omx-scroll">
      <div className="omx-toolbar">
        <span className="omx-label">{rows.length} relationships</span>
        <div style={{ flex: 1 }} />
        <button className={`omx-btn ${!rel ? 'on' : ''}`} onClick={() => setRel('')}>All</button>
        {relations.slice(0, 8).map((r) => (
          <button key={r} className={`omx-btn ${rel === r ? 'on' : ''}`}
                  onClick={() => setRel(r)}>{r.replace(/_/g, ' ')}</button>
        ))}
      </div>

      <div style={{ display: 'grid', gap: 7 }}>
        {rows.map((r, i) => (
          <div className="omx-card" key={`${r.source.id}-${r.target.id}-${r.relation}-${i}`}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <button className="omx-linkbtn" onClick={() => void pick(r.source.id)}>
                <span style={{ color: r.source.color }}>{r.source.glyph}</span> {r.source.name}
              </button>
              <span className="omx-rel-arrow">
                <span className="omx-label" style={{ color: 'var(--omx-gold)' }}>{r.label}</span>
              </span>
              <button className="omx-linkbtn" onClick={() => void pick(r.target.id)}>
                <span style={{ color: r.target.color }}>{r.target.glyph}</span> {r.target.name}
              </button>
              <div style={{ flex: 1 }} />
              <span className="omx-label">{r.count} obs</span>
              {r.llm && <span className="omx-prov" data-p="ai_inferred">AI</span>}
              {!r.llm && <span className="omx-prov" data-p="source_backed">SRC</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 8 }}>
              <span className="omx-bar" style={{ flex: 1 }}>
                <i style={{ width: `${Math.min(100, (r.weight / (rows[0]?.weight || 1)) * 100)}%` }} />
              </span>
              <span className="omx-mono" style={{
                fontSize: 10,
                color: r.sentiment > 0.12 ? 'var(--omx-pos)'
                  : r.sentiment < -0.12 ? 'var(--omx-neg)' : 'var(--omx-text-faint)',
              }}>
                {r.sentiment >= 0 ? '+' : ''}{r.sentiment.toFixed(2)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Analysis — TERRA's generated situation report
// ---------------------------------------------------------------------------
export function AnalysisView() {
  const [data, setData] = useState<Record<string, any> | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)

  const load = () => {
    setLoading(true)
    fetch('/api/terra/analysis').then((r) => r.json())
      .then(setData).catch(() => setData(null)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const generate = async () => {
    setRunning(true)
    try {
      await fetch('/api/terra/jobs/analysis', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      // The job is asynchronous; poll until it produces a report rather than
      // claiming success the moment it is accepted.
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 3000))
        const res = await fetch('/api/terra/analysis').then((r) => r.json())
        if (res && res.status !== 'none') { setData(res); break }
      }
    } finally { setRunning(false) }
  }

  if (loading) return <div className="omx-scroll"><span className="omx-spin" /></div>

  if (!data || data.status === 'none') {
    return <Empty
      glyph={<IconAnalysis size={34} />}
      title="No situation report yet"
      body="TERRA can synthesise a global assessment from the current corpus — ranked events, country risk and the relationships driving them."
      action={
        <button className="omx-btn on" onClick={() => void generate()} disabled={running}>
          {running ? <><span className="omx-spin" /> Generating…</> : 'Generate assessment'}
        </button>
      }
    />
  }

  const master: Record<string, any> = data.master || {}
  const analysts: Record<string, any>[] = Array.isArray(data.analysts) ? data.analysts : []

  return (
    <div className="omx-scroll">
      <div className="omx-toolbar">
        <span className="omx-label">Situation report</span>
        {typeof data.events_considered === 'number' && (
          <span className="omx-label">{data.events_considered} events considered</span>
        )}
        {typeof data.generated_at === 'number' && (
          <span className="omx-label">
            {/* `ago` returns the bare word "now" inside the first minute, and
                "now ago" is not a time. */}
            {ago(data.generated_at) === 'now' ? 'just now' : `${ago(data.generated_at)} ago`}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button className="omx-btn" onClick={() => void generate()} disabled={running}>
          {running ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>

      {/* The desk assessment leads, because it is the one reading that saw all
          six analysts. Its cross-domain insights are the whole point of running
          six of them, so they get their own block rather than a bullet list. */}
      {master.headline && (
        <div className="omx-card" style={{ marginBottom: 11 }}>
          <div className="omx-label" style={{ marginBottom: 8 }}>
            Desk assessment
            {typeof master.confidence === 'number' &&
              ` · confidence ${Math.round(master.confidence * 100)}%`}
            {master.mode === 'deterministic' && ' · no model'}
          </div>
          <h3 style={{ margin: '0 0 8px', fontSize: 16, lineHeight: 1.35 }}>
            {master.headline}
          </h3>
          {master.assessment && (
            <Markdown className="omx-md" text={master.assessment} />
          )}

          {Array.isArray(master.cross_domain) && master.cross_domain.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="omx-label" style={{ marginBottom: 6 }}>Cross-domain</div>
              {master.cross_domain.map((c: any, i: number) => (
                <div key={i} style={{ marginBottom: 7, fontSize: 12.5, lineHeight: 1.6 }}>
                  {c.insight}
                  {Array.isArray(c.domains) && c.domains.length > 0 && (
                    <span className="omx-mono" style={{ opacity: 0.6, marginLeft: 6 }}>
                      {c.domains.join(' · ')}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          <Bullets label="Priorities" items={master.priorities} ordered />
          <Bullets label="What would change this" items={master.watch} />
        </div>
      )}

      <div style={{ display: 'grid', gap: 11 }}>
        {analysts.map((a, i) => (
          <div className="omx-card" key={a.domain || i}>
            <div className="omx-label" style={{ marginBottom: 8 }}>
              <span style={{ color: a.color }}>{a.glyph} </span>
              {a.name}
              {typeof a.confidence === 'number' &&
                ` · confidence ${Math.round(a.confidence * 100)}%`}
              {typeof a.events === 'number' &&
                ` · ${a.events} events / ${a.articles} articles`}
              {a.mode === 'deterministic' && ' · no model'}
            </div>
            {a.headline && (
              <div style={{ fontSize: 14, lineHeight: 1.4, marginBottom: 6 }}>{a.headline}</div>
            )}
            {a.assessment && (
              <Markdown className="omx-md" text={a.assessment} />
            )}
            <Bullets label="Key points" items={a.key_points} />
            <Bullets label="Watching" items={a.watch} />
            {Array.isArray(a.top_events) && a.top_events.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="omx-label" style={{ marginBottom: 5 }}>Top events</div>
                {a.top_events.slice(0, 6).map((e: any) => (
                  <div key={e.id} style={{ fontSize: 12, lineHeight: 1.55, marginBottom: 3 }}>
                    {e.url ? (
                      <a className="omx-linkbtn" href={e.url} target="_blank" rel="noreferrer">
                        {e.title}
                      </a>
                    ) : e.title}
                    <span className="omx-mono" style={{ opacity: 0.55, marginLeft: 6 }}>
                      {e.sources} outlets
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/** A labelled bullet list that renders nothing when there is nothing to say.
 *
 *  Every list here arrives from a model via `str_list`, so it is always an
 *  array of strings — but an EMPTY array is a real and common answer, and an
 *  empty "Watching" heading reads as a rendering fault rather than as silence. */
function Bullets({ label, items, ordered }: {
  label: string; items: unknown; ordered?: boolean
}) {
  const list = (Array.isArray(items) ? items : [])
    .map((x) => (typeof x === 'string' ? x : String(x ?? '')).trim())
    // Models asked for a ranked list usually number it themselves, and an
    // already-numbered item inside an <ol> renders as "1. 1. Monitor…".
    .map((x) => (ordered ? x.replace(/^\s*\d+[.)]\s*/, '') : x))
    .filter(Boolean)
  if (!list.length) return null
  const Tag = ordered ? 'ol' : 'ul'
  return (
    <div style={{ marginTop: 10 }}>
      <div className="omx-label" style={{ marginBottom: 5 }}>{label}</div>
      <Tag style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7 }}>
        {list.map((item, i) => <li key={i}>{item}</li>)}
      </Tag>
    </div>
  )
}
