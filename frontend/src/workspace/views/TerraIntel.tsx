// TERRA's intelligence layer, restored as workspace views.
//
// These were in the injected `build_terra_intel.js` console: the Intel
// overview, the Situation Room, the Ask pane (semantic search, what-if,
// briefings), TERRA's own agent runs, and the country dossier. They read the
// same /api/terra/* endpoints the old console did — no backend change was
// needed to bring them back, only a UI that calls them.
//
// Everything here follows the workspace's rules rather than the old console's:
// selecting an entity drives the shared selection, and nothing renders a
// number the backend did not actually measure.

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { IconEmptyObject, IconSituation } from '../components/Icons'
import { Markdown } from '../components/Markdown'

const post = (p: string, body: unknown = {}) =>
  fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' },
             body: JSON.stringify(body) }).then((r) => r.json())
const get = (p: string) => fetch(p).then((r) => r.json())

/** Takes an icon node rather than a character — see the note on Views.tsx's
 *  copy of this component. */
function Empty({ glyph, title, body, action }: {
  glyph: React.ReactNode; title: string; body: string; action?: React.ReactNode
}) {
  return (
    <div className="omx-empty">
      <div className="glyph">{glyph}</div>
      <h3>{title}</h3><p>{body}</p>
      {action && <div className="acts">{action}</div>}
    </div>
  )
}

/** Resolve a TERRA entity id into the workspace object and select it. */
function useTerraSelect() {
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  return async (terraId: string) => {
    if (!ws || !terraId) return
    try {
      const r = await api.objects(ws, { externalId: `terra:${terraId}` })
      if (r.objects[0]) select(r.objects[0].id, 'graph')
    } catch { /* not projected into this workspace */ }
  }
}

// ---------------------------------------------------------------------------
// INTEL — ranked global picture
// ---------------------------------------------------------------------------
export function IntelView() {
  const [d, setD] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const pick = useTerraSelect()

  const load = () => {
    setLoading(true)
    get('/api/terra/overview').then(setD).catch(() => setD(null)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const refresh = async () => {
    setRefreshing(true)
    try { await post('/api/terra/refresh'); load() } finally { setRefreshing(false) }
  }

  if (loading) return <div className="omx-scroll"><span className="omx-spin" /></div>
  if (!d) return <Empty glyph={<IconEmptyObject size={34} />} title="Intel unavailable" body="TERRA's corpus could not be read." />

  const events = d.events || []
  const countries = d.countries || d.scores || []
  const alerts = d.alerts || []

  return (
    <div className="omx-scroll">
      <div className="omx-toolbar">
        <span className="omx-live"><i /> Live</span>
        <span className="omx-label">
          {events.length} ranked events
          {d.stats?.articles ? ` · ${d.stats.articles} articles` : ''}
        </span>
        <div style={{ flex: 1 }} />
        <button className="omx-btn" onClick={() => void refresh()} disabled={refreshing}>
          {refreshing ? <><span className="omx-spin" /> Refreshing</> : 'Refresh corpus'}
        </button>
      </div>

      {alerts.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div className="omx-label" style={{ color: 'var(--omx-neg)', marginBottom: 9 }}>Alerts</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {alerts.map((a: any, i: number) => (
              <div className="omx-card" key={i} style={{ borderColor: 'var(--omx-neg-line)' }}>
                <div style={{ fontSize: 13 }}>{a.title || a.text || JSON.stringify(a).slice(0, 200)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="omx-split2">
        <div>
          <div className="omx-label" style={{ marginBottom: 9 }}>Ranked events</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {events.slice(0, 30).map((e: any) => (
              <div className="omx-card click" key={e.id}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span className="omx-rank">{e.size ?? '·'}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, lineHeight: 1.45 }}>{e.title}</div>
                    <div className="omx-label" style={{ marginTop: 5, display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                      {(e.countries || []).slice(0, 5).map((c: string) => (
                        <button key={c} className="omx-linkbtn sm"
                                onClick={() => void pick(`country:${c}`)}>{c}</button>
                      ))}
                      {(e.domains || []).slice(0, 3).map((x: string) => <span key={x}>{x}</span>)}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="omx-label" style={{ marginBottom: 9 }}>Country risk</div>
          <div style={{ display: 'grid', gap: 6 }}>
            {(Array.isArray(countries) ? countries : Object.values(countries))
              .slice(0, 26).map((c: any) => (
                <button className="omx-riskrow" key={c.iso2}
                        onClick={() => void pick(`country:${c.iso2}`)}>
                  <span className="dot" style={{ background: c.color }} />
                  <span style={{ flex: 1, textAlign: 'left' }}>{c.name}</span>
                  <span className="omx-bar" style={{ width: 68 }}>
                    <i style={{ width: `${Math.min(100, c.score * 4)}%`, background: c.color }} />
                  </span>
                  <span className="omx-mono" style={{ fontSize: 10.5, width: 30, textAlign: 'right' }}>
                    {Number(c.score).toFixed(1)}
                  </span>
                </button>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SITUATION ROOM — per-theatre assessment
// ---------------------------------------------------------------------------
export function SituationView() {
  const [theatres, setTheatres] = useState<any[]>([])
  const [active, setActive] = useState<string>('')
  const [report, setReport] = useState<any>(null)
  const [running, setRunning] = useState(false)
  const pick = useTerraSelect()

  useEffect(() => {
    get('/api/terra/theatres').then((d) => {
      setTheatres(d.theatres || [])
      if (d.theatres?.[0]) setActive(d.theatres[0].key)
    }).catch(() => setTheatres([]))
  }, [])

  const run = async (key: string) => {
    setRunning(true); setReport(null)
    try {
      const started = await post('/api/terra/jobs/situation', { theatre: key })
      const jobId = started.id || started.job_id
      if (!jobId) { setReport(started); return }
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2500))
        const j = await get(`/api/terra/jobs/${jobId}`)
        if (['done', 'error'].includes(j.status)) { setReport(j.result ?? j); break }
      }
    } finally { setRunning(false) }
  }

  if (!theatres.length) {
    return <Empty glyph={<IconSituation size={34} />} title="No theatres" body="TERRA defines no active theatres right now." />
  }

  const t = theatres.find((x) => x.key === active)

  return (
    <div className="omx-scroll">
      <div className="omx-toolbar">
        {theatres.map((x) => (
          <button key={x.key} className={`omx-btn ${active === x.key ? 'on' : ''}`}
                  onClick={() => { setActive(x.key); setReport(null) }}>
            {x.name}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="omx-btn on" onClick={() => void run(active)} disabled={running}>
          {running ? <><span className="omx-spin" /> Assessing…</> : 'Run assessment'}
        </button>
      </div>

      {t && (
        <div className="omx-card" style={{ marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <strong style={{ fontSize: 14 }}>{t.name}</strong>
            <span className="omx-label">{t.countries?.length ?? 0} countries</span>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 9 }}>
            {(t.countries || []).map((c: string) => (
              <button key={c} className="omx-pill click" onClick={() => void pick(`country:${c}`)}>{c}</button>
            ))}
          </div>
        </div>
      )}

      {report && <SituationReport report={report} pick={pick} />}

      {!report && !running && (
        <p style={{ color: 'var(--omx-text-faint)', fontSize: 12.5 }}>
          Run an assessment to synthesise this theatre from the live corpus.
        </p>
      )}
    </div>
  )
}

/** One theatre assessment, rendered as a report rather than as its payload.
 *
 *  The generic key/value dumper this replaces printed `extent` as a JSON block
 *  and `events` as an array whose first field was a 300-character Google RSS
 *  redirect, so the most useful product TERRA generates read as a debug dump.
 *  Every field below is one the backend actually returns; the geometry
 *  (`extent`, `glyph`, `key`) is deliberately NOT shown — it drives the map,
 *  and is not something a reader of the report needs. */
function SituationReport({ report, pick }: {
  report: Record<string, any>
  pick: (terraId: string) => void | Promise<void>
}) {
  const r = report || {}
  const pct = (v: unknown) =>
    typeof v === 'number' ? `${Math.round(v * 100)}%` : null

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="omx-card">
        <div className="omx-label" style={{ marginBottom: 7 }}>
          Assessment
          {pct(r.confidence) && ` · confidence ${pct(r.confidence)}`}
          {typeof r.event_count === 'number' &&
            ` · ${r.event_count} events / ${r.article_count} articles`}
          {r.mode === 'deterministic' && ' · no model'}
        </div>
        <Markdown className="omx-md"
                  text={r.summary || 'No summary was produced for this theatre.'} />
      </div>

      {Array.isArray(r.timeline) && r.timeline.length > 0 && (
        <div className="omx-card">
          <div className="omx-label" style={{ marginBottom: 7 }}>Timeline</div>
          {r.timeline.map((t: any, i: number) => (
            <div key={i} style={{ display: 'flex', gap: 10, marginBottom: 6, fontSize: 12.5 }}>
              <span className="omx-mono" style={{ opacity: 0.6, minWidth: 62 }}>{t.when}</span>
              <span style={{ lineHeight: 1.55 }}>{t.what}</span>
            </div>
          ))}
        </div>
      )}

      {Array.isArray(r.players) && r.players.length > 0 && (
        <div className="omx-card">
          <div className="omx-label" style={{ marginBottom: 7 }}>Players</div>
          {r.players.map((p: any, i: number) => (
            <div key={i} style={{ marginBottom: 8, fontSize: 12.5, lineHeight: 1.55 }}>
              <strong>{p.name}</strong>
              <span className="omx-mono" style={{ opacity: 0.6, marginLeft: 6 }}>
                {p.type}{p.posture ? ` · ${p.posture}` : ''}
              </span>
              {/* `in_graph` is the honest marker for whether this Space holds
                  the player at all; only those can be selected. */}
              {p.in_graph && p.type === 'country' && (
                <button className="omx-linkbtn" style={{ marginLeft: 6 }}
                        onClick={() => void pick(`country:${p.name}`)}>select</button>
              )}
              {p.role && <div style={{ opacity: 0.85 }}>{p.role}</div>}
            </div>
          ))}
        </div>
      )}

      {Array.isArray(r.predictions) && r.predictions.length > 0 && (
        <div className="omx-card">
          <div className="omx-label" style={{ marginBottom: 7 }}>Predictions</div>
          {r.predictions.map((p: any, i: number) => (
            <div key={i} style={{ marginBottom: 7, fontSize: 12.5, lineHeight: 1.55 }}>
              {p.claim}
              <span className="omx-mono" style={{ opacity: 0.6, marginLeft: 6 }}>
                {p.horizon}{pct(p.confidence) ? ` · ${pct(p.confidence)}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {Array.isArray(r.risk) && r.risk.length > 0 && (
        <div className="omx-card">
          <div className="omx-label" style={{ marginBottom: 7 }}>Country risk</div>
          {r.risk.map((x: any) => (
            <div key={x.iso2} style={{ display: 'flex', alignItems: 'center',
                                       gap: 9, marginBottom: 5, fontSize: 12.5 }}>
              <button className="omx-linkbtn" onClick={() => void pick(`country:${x.iso2}`)}>
                {x.name}
              </button>
              <span className="omx-mono" style={{ color: x.color }}>
                {Math.round(x.score)} {x.band}
              </span>
              {x.top_dimension && (
                <span className="omx-label">driven by {x.top_dimension}</span>
              )}
              {x.thin && <span className="omx-label warn">thin</span>}
            </div>
          ))}
        </div>
      )}

      {Array.isArray(r.events) && r.events.length > 0 && (
        <div className="omx-card">
          <div className="omx-label" style={{ marginBottom: 7 }}>Events</div>
          {r.events.map((e: any) => (
            <div key={e.id} style={{ marginBottom: 6, fontSize: 12.5, lineHeight: 1.55 }}>
              {/* The href is a long RSS redirect — it belongs BEHIND the title,
                  never printed as the body of the report. */}
              {e.url
                ? <a className="omx-linkbtn" href={e.url} target="_blank" rel="noreferrer">{e.title}</a>
                : e.title}
              <span className="omx-mono" style={{ opacity: 0.6, marginLeft: 6 }}>
                {e.sources} outlets
                {typeof e.corroboration === 'number'
                  && ` · corroboration ${e.corroboration.toFixed(2)}`}
                {e.when ? ` · ${e.when}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      <StrList label="What to watch" items={r.watch} />
      <StrList label="Gaps in the reporting" items={r.gaps} />
    </div>
  )
}

/** A card of plain strings, rendered only when there are some. */
function StrList({ label, items }: { label: string; items: unknown }) {
  const list = (Array.isArray(items) ? items : [])
    .map((x) => (typeof x === 'string' ? x : String(x ?? '')).trim())
    .filter(Boolean)
  if (!list.length) return null
  return (
    <div className="omx-card">
      <div className="omx-label" style={{ marginBottom: 7 }}>{label}</div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7 }}>
        {list.map((x, i) => <li key={i}>{x}</li>)}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ASK — semantic search, what-if, briefings
// ---------------------------------------------------------------------------
// The view opened as a mode switcher, an empty input and nothing else, which
// told a first-time user neither what the three modes do differently nor what
// any of them would return. An input box with no context is not a neutral
// starting point — it reads as an unfinished page, and the fastest way to make
// someone conclude a feature is broken is to show them nothing.
//
// So the pre-query state carries its own weight: what each mode is for, what it
// gives back, and examples that run on click. The examples are not decoration —
// the hardest part of a semantic search over a corpus you have not seen is
// guessing what it is capable of answering.

const ASK_MODES = [
  {
    id: 'search' as const,
    label: 'Semantic search',
    what: 'Finds the passages in the live corpus that answer a question, ' +
      'resolves the entities it recognises, and synthesises across them.',
    returns: 'Ranked passages with a retrieval score, plus resolved entities ' +
      'you can pull into the graph.',
    examples: [
      'Who is buying Russian crude at a discount?',
      'What is constraining Red Sea shipping capacity?',
      'Which countries have restricted rare-earth exports?',
    ],
  },
  {
    id: 'whatif' as const,
    label: 'What-if',
    what: 'Takes a scenario you describe and walks the relationship graph to ' +
      'find who and what it touches.',
    returns: 'Affected actors and the chain of dependencies that connects ' +
      'them to the scenario.',
    examples: [
      'Iran closes the Strait of Hormuz',
      'Taiwan semiconductor exports halt for a quarter',
      'OPEC+ cuts output by two million barrels a day',
    ],
  },
  {
    id: 'brief' as const,
    label: 'Briefing',
    what: 'Assembles a standing briefing on a topic from everything the ' +
      'corpus currently holds. Leave the box empty for a global brief.',
    returns: 'A structured briefing with the stories behind each section.',
    examples: [
      'Sahel security',
      'Semiconductor supply chain',
      'Arctic shipping routes',
    ],
  },
]

export function AskView() {
  const [mode, setMode] = useState<'search' | 'whatif' | 'brief'>('search')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<any>(null)
  const pick = useTerraSelect()

  // `override` lets an example chip run its own text: setQ is async, so a chip
  // that only called setQ would submit whatever was in the box before it.
  const run = async (override?: string) => {
    const text = (override ?? q).trim()
    if (override !== undefined) setQ(override)
    if (!text && mode !== 'brief') return
    setBusy(true); setRes(null)
    try {
      if (mode === 'search') {
        setRes(await get(`/api/terra/search?q=${encodeURIComponent(text)}&synthesize=true`))
      } else {
        const kind = mode === 'whatif' ? 'whatif' : 'brief'
        const started = await post(`/api/terra/jobs/${kind}`,
          mode === 'whatif' ? { scenario: text } : { topic: text })
        const jobId = started.id || started.job_id
        if (!jobId) { setRes(started); return }
        for (let i = 0; i < 90; i++) {
          await new Promise((r) => setTimeout(r, 2500))
          const j = await get(`/api/terra/jobs/${jobId}`)
          if (['done', 'error'].includes(j.status)) { setRes(j.result ?? j); break }
        }
      }
    } catch (e) { setRes({ error: String((e as Error).message) }) }
    finally { setBusy(false) }
  }

  const placeholder = mode === 'search'
    ? 'Ask anything about the world corpus…'
    : mode === 'whatif'
      ? 'Describe a scenario — "Iran closes the Strait of Hormuz"'
      : 'Topic for a briefing — leave blank for a global brief'

  const active = ASK_MODES.find((m) => m.id === mode) ?? ASK_MODES[0]

  return (
    // `omx-tquery-*`, not `omx-ask-*`: `.omx-ask` is Home's Ask button and
    // `.omx-ask-empty` is NOVA's empty state. Both would apply here.
    <div className="omx-scroll omx-tquery">
      <div className="omx-tquery-intro">
        <div className="omx-label">TERRA</div>
        <h2>Query the world corpus</h2>
        <p>
          Three ways of interrogating the live article corpus. This is TERRA's
          own index, not your Space — results carry no workspace provenance and
          nothing here becomes a claim until you research it.
        </p>
      </div>

      <div className="omx-toolbar">
        {ASK_MODES.map((m) => (
          <button key={m.id} className={`omx-btn ${mode === m.id ? 'on' : ''}`}
                  onClick={() => { setMode(m.id); setRes(null) }}>
            {m.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input className="omx-input" placeholder={placeholder} value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') void run() }} />
        <button className="omx-btn on" onClick={() => void run()} disabled={busy}>
          {busy ? <span className="omx-spin" /> : 'Run'}
        </button>
      </div>

      {busy && <div className="omx-label">Working — this runs against the live corpus…</div>}

      {/* The pre-query state. Only while there is genuinely nothing to show:
          once a result exists it is the more useful thing to look at. */}
      {!res && !busy && (
        <div className="omx-tquery-empty">
          <div className="omx-tquery-modes">
            {ASK_MODES.map((m) => (
              <button
                key={m.id}
                className={`omx-tquery-mode ${mode === m.id ? 'on' : ''}`}
                onClick={() => { setMode(m.id); setRes(null) }}
              >
                <span className="t">{m.label}</span>
                <span className="w">{m.what}</span>
                <span className="r">Returns: {m.returns}</span>
              </button>
            ))}
          </div>

          <div className="omx-tquery-examples">
            <div className="omx-label">Try one of these</div>
            <div className="chips">
              {active.examples.map((ex) => (
                <button key={ex} className="omx-pill click"
                        onClick={() => void run(ex)}>
                  {ex}
                </button>
              ))}
            </div>
            {mode === 'brief' && (
              <p className="omx-null">
                Or run it with the box empty for a brief on everything the
                corpus is currently tracking.
              </p>
            )}
          </div>
        </div>
      )}

      {res?.resolution?.entities?.length > 0 && (
        <div className="omx-card" style={{ marginBottom: 11 }}>
          <div className="omx-label" style={{ marginBottom: 8 }}>Resolved entities</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {res.resolution.entities.map((e: any) => (
              <button key={e.id} className="omx-pill click" onClick={() => void pick(e.id)}>
                {e.name} <span className="omx-label">{e.type}</span>
              </button>
            ))}
          </div>
          {res.resolution.related?.length > 0 && (
            <>
              <div className="omx-label" style={{ margin: '11px 0 7px' }}>Related</div>
              <div style={{ display: 'grid', gap: 4 }}>
                {res.resolution.related.slice(0, 10).map((r: any, i: number) => (
                  <button className="omx-rel" key={i} onClick={() => void pick(r.id)}>
                    <span className="lbl">{r.relation}</span>
                    <span style={{ flex: 1, textAlign: 'left' }}>{r.name}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {res?.error && (
        <div className="omx-card"><div className="omx-label warn">Error</div>
          <p style={{ margin: 0, fontSize: 13 }}>{String(res.error)}</p></div>
      )}
      {res && !res.error && <AskResult res={res} pick={pick} />}
    </div>
  )
}

/** A citation may be a bare result number or the result object itself. */
function citeRef(c: any): string {
  if (c == null) return ''
  if (typeof c === 'number' || typeof c === 'string') return String(c)
  return String(c.n ?? c.title ?? '')
}

/** `why` is the retrieval score breakdown ({text, graph, direct_entities, …}),
 *  not a sentence — interpolating it straight into JSX prints [object Object].
 *  Only the two continuous scores are worth surfacing; the entity counts are
 *  already visible as the resolved-entity pills above. */
function whyText(why: any): string {
  if (!why || typeof why !== 'object') return typeof why === 'string' ? why : ''
  const parts: string[] = []
  if (typeof why.text === 'number') parts.push(`text ${why.text.toFixed(2)}`)
  if (typeof why.graph === 'number') parts.push(`graph ${why.graph.toFixed(2)}`)
  return parts.join(' · ')
}

/** Renders whichever of the three Ask products came back.
 *
 *  Dispatching on the PAYLOAD rather than on the `mode` state is deliberate:
 *  what-if and briefing arrive from a job poll, and a user who switches mode
 *  while one is in flight would otherwise get the wrong renderer. */
function AskResult({ res, pick }: {
  res: Record<string, any>
  pick: (terraId: string) => void | Promise<void>
}) {
  const pct = (v: unknown) =>
    typeof v === 'number' ? `${Math.round(v * 100)}%` : null

  // --- semantic search -----------------------------------------------------
  if (res.synthesis || Array.isArray(res.results)) {
    const syn = res.synthesis || {}
    return (
      <>
        {(syn.answer || syn.declined) && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>
              Answer
              {/* `grounded:false` means the model answered from its own
                  knowledge rather than the corpus. That is a claim about
                  trust and must never be silent. */}
              {syn.grounded === false && ' · not drawn from the corpus'}
            </div>
            {/* Prefer a real answer over the decline notice: the two flags
                are set independently, and hiding text the model did produce is
                worse than a slightly redundant caveat. */}
            <Markdown className="omx-md"
                      text={syn.answer || 'The corpus did not support an answer to this question.'} />
            {Array.isArray(syn.citations) && syn.citations.length > 0 && (
              <div className="omx-label" style={{ marginTop: 8 }}>
                Cites results {syn.citations.map(citeRef).filter(Boolean).join(', ')}
              </div>
            )}
          </div>
        )}
        {Array.isArray(res.results) && res.results.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 8 }}>
              {res.results.length} results
            </div>
            {res.results.map((r: any) => (
              <div key={r.id || r.n} style={{ marginBottom: 9, fontSize: 12.5, lineHeight: 1.55 }}>
                <span className="omx-mono" style={{ opacity: 0.5, marginRight: 6 }}>{r.n}</span>
                {r.url
                  ? <a className="omx-linkbtn" href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
                  : r.title}
                <div className="omx-label" style={{ marginTop: 2 }}>
                  {r.source}
                  {pct(r.confidence) && ` · confidence ${pct(r.confidence)}`}
                  {whyText(r.why) && ` · ${whyText(r.why)}`}
                </div>
              </div>
            ))}
          </div>
        )}
      </>
    )
  }

  // --- what-if -------------------------------------------------------------
  if (res.scenario) {
    return (
      <>
        <div className="omx-card" style={{ marginBottom: 10 }}>
          <div className="omx-label" style={{ marginBottom: 7 }}>
            Simulation
            {pct(res.confidence) && ` · confidence ${pct(res.confidence)}`}
            {res.mode === 'deterministic' && ' · no model'}
          </div>
          <Markdown className="omx-md" text={res.summary || ''} />
          {/* The backend ships this caveat with every what-if; it is the whole
              difference between a simulation and a forecast. */}
          {res.note && (
            <div className="omx-label warn" style={{ marginTop: 8 }}>{res.note}</div>
          )}
        </div>

        {Array.isArray(res.timeline) && res.timeline.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Timeline</div>
            {res.timeline.map((t: any, i: number) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <div className="omx-mono" style={{ fontSize: 12, opacity: 0.7 }}>{t.when}</div>
                <ul style={{ margin: '3px 0 0', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6 }}>
                  {(t.effects || []).map((e: string, j: number) => <li key={j}>{e}</li>)}
                </ul>
              </div>
            ))}
          </div>
        )}

        {Array.isArray(res.markets) && res.markets.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Markets</div>
            {res.markets.map((m: any, i: number) => (
              <div key={i} style={{ marginBottom: 7, fontSize: 12.5, lineHeight: 1.55 }}>
                <strong>{m.asset}</strong>
                <span className="omx-mono" style={{ opacity: 0.7, marginLeft: 6 }}>
                  {m.direction} · {m.magnitude}
                </span>
                {m.reasoning && <div style={{ opacity: 0.85 }}>{m.reasoning}</div>}
              </div>
            ))}
          </div>
        )}

        {Array.isArray(res.countries) && res.countries.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Countries affected</div>
            {res.countries.map((c: any, i: number) => (
              <div key={i} style={{ marginBottom: 7, fontSize: 12.5, lineHeight: 1.55 }}>
                <button className="omx-linkbtn" onClick={() => void pick(`country:${c.iso2}`)}>
                  {c.name}
                </button>
                {pct(c.severity) && (
                  <span className="omx-mono" style={{ opacity: 0.7, marginLeft: 6 }}>
                    severity {pct(c.severity)}
                  </span>
                )}
                {c.impact && <div style={{ opacity: 0.85 }}>{c.impact}</div>}
              </div>
            ))}
          </div>
        )}

        {Array.isArray(res.companies) && res.companies.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Companies exposed</div>
            {res.companies.map((c: any, i: number) => (
              <div key={i} style={{ marginBottom: 6, fontSize: 12.5, lineHeight: 1.55 }}>
                <strong>{c.name}</strong>
                {c.impact && <div style={{ opacity: 0.85 }}>{c.impact}</div>}
              </div>
            ))}
          </div>
        )}

        <StrList label="Mitigations" items={res.mitigations} />
        <StrList label="Assumptions" items={res.assumptions} />
      </>
    )
  }

  // --- briefing ------------------------------------------------------------
  if (res.text) {
    return (
      <>
        <div className="omx-card" style={{ marginBottom: 10 }}>
          <div className="omx-label" style={{ marginBottom: 7 }}>
            {res.label || 'Briefing'}
            {res.grounded === false && ' · not drawn from the corpus'}
            {res.mode === 'deterministic' && ' · no model'}
          </div>
          <Markdown className="omx-md" text={res.text || ''} />
        </div>

        {Array.isArray(res.alerts) && res.alerts.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Signals</div>
            {res.alerts.map((a: any, i: number) => (
              <div key={i} style={{ marginBottom: 5, fontSize: 12.5 }}>
                <strong>{a.label}</strong> — {a.title}
                {pct(a.confidence) && (
                  <span className="omx-label" style={{ marginLeft: 6 }}>{pct(a.confidence)}</span>
                )}
              </div>
            ))}
          </div>
        )}

        {Array.isArray(res.risk_top) && res.risk_top.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Highest risk</div>
            {res.risk_top.map((r: any) => (
              <div key={r.iso2} style={{ marginBottom: 4, fontSize: 12.5 }}>
                <button className="omx-linkbtn" onClick={() => void pick(`country:${r.iso2}`)}>
                  {r.name}
                </button>
                <span className="omx-mono" style={{ opacity: 0.7, marginLeft: 6 }}>
                  {Math.round(r.score)} {r.band}
                </span>
              </div>
            ))}
          </div>
        )}

        {Array.isArray(res.events) && res.events.length > 0 && (
          <div className="omx-card" style={{ marginBottom: 10 }}>
            <div className="omx-label" style={{ marginBottom: 7 }}>Events behind it</div>
            {res.events.map((e: any) => (
              <div key={e.id} style={{ marginBottom: 5, fontSize: 12.5, lineHeight: 1.55 }}>
                {e.url
                  ? <a className="omx-linkbtn" href={e.url} target="_blank" rel="noreferrer">{e.title}</a>
                  : e.title}
                <span className="omx-mono" style={{ opacity: 0.6, marginLeft: 6 }}>
                  {e.sources} outlets
                  {typeof e.corroboration === 'number'
                    && ` · corroboration ${e.corroboration.toFixed(2)}`}
                </span>
              </div>
            ))}
          </div>
        )}
      </>
    )
  }

  return (
    <div className="omx-card">
      <div className="omx-label">No result</div>
      <p style={{ margin: 0, fontSize: 12.5 }}>
        The run finished without producing a report.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// TERRA AGENTS — the analyst runs TERRA can perform
// ---------------------------------------------------------------------------
const TERRA_JOBS: [string, string, string][] = [
  ['analysis', 'Global analysis', 'Synthesise the whole corpus into an assessment'],
  ['situation', 'Situation report', 'Assess one theatre in depth'],
  ['brief', 'Briefing', 'Produce a briefing on a topic'],
  ['whatif', 'What-if', 'Simulate a scenario against current conditions'],
  ['predict', 'Projections', 'Forward-looking projections with probabilities'],
  ['verify', 'Verification', 'Check a claim against the corpus'],
]

export function TerraAgentsView() {
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    get('/api/terra/jobs').then((d) => setJobs(d.jobs || []))
      .catch(() => setJobs([])).finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const start = async (kind: string) => { await post(`/api/terra/jobs/${kind}`); load() }

  return (
    <div className="omx-scroll">
      <div className="omx-label" style={{ marginBottom: 11 }}>TERRA analyst runs</div>
      <div className="omx-grid3" style={{ marginBottom: 22 }}>
        {TERRA_JOBS.map(([kind, name, blurb]) => (
          <div className="omx-card click" key={kind} onClick={() => void start(kind)}>
            <strong style={{ fontSize: 13 }}>{name}</strong>
            <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--omx-text-faint)' }}>{blurb}</p>
          </div>
        ))}
      </div>

      <div className="omx-label" style={{ marginBottom: 9 }}>
        History {loading && <span className="omx-spin" />}
      </div>
      {!jobs.length && <span className="omx-label">No runs yet</span>}
      <div style={{ display: 'grid', gap: 7 }}>
        {jobs.slice(0, 40).map((j) => (
          <div className="omx-card" key={j.id}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span className={`omx-status ${j.status}`}>{j.status}</span>
              <strong style={{ fontSize: 12.5 }}>{j.kind}</strong>
              <div style={{ flex: 1 }} />
              <span className="omx-label">
                {j.elapsed ? `${Math.round(j.elapsed)}s` : ''}
              </span>
            </div>
            {j.stage && <div className="omx-label" style={{ marginTop: 5 }}>{j.stage}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// COUNTRY DOSSIER — shown in the inspector rail when a country is selected
// ---------------------------------------------------------------------------
export function CountryDossier({ iso }: { iso: string }) {
  const [d, setD] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  // Loaded ON DEMAND, not on selection. /api/terra/country/{iso} fetches World
  // Bank indicators live and was measured at ~60s cold. Firing that every time
  // a country is selected would put a minute-long spinner in the inspector on
  // every map click. The button also sets the expectation that this is a
  // network trip rather than local data.
  useEffect(() => { setD(null); setLoading(false) }, [iso])

  const load = () => {
    setLoading(true)
    get(`/api/terra/country/${iso}`).then(setD).catch(() => setD(null))
      .finally(() => setLoading(false))
  }

  if (!d) {
    return (
      <div className="omx-section">
        <h4>Dossier</h4>
        <button className="omx-btn" onClick={load} disabled={loading}>
          {loading
            ? <><span className="omx-spin" /> Fetching (up to a minute)</>
            : 'Load country dossier'}
        </button>
        <p style={{ margin: '8px 0 0', fontSize: 11.5, color: 'var(--omx-text-faint)' }}>
          Profile, capital, currency and World Bank economic indicators. Fetched
          live from the source, so it is slow the first time.
        </p>
      </div>
    )
  }
  if (d.status !== 'ok') return null

  const eco = d.economy || {}
  const fmt = (v: any) => {
    const n = Number(v?.value ?? v)
    if (!isFinite(n)) return String(v?.display ?? v ?? '—')
    if (n >= 1e12) return `${(n / 1e12).toFixed(2)}T`
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
    return n.toLocaleString()
  }

  return (
    <>
      <div className="omx-section">
        <h4>Dossier {d.flag}</h4>
        {d.profile && Object.entries(d.profile)
          .filter(([k, v]) => v && !['flag', 'latlng'].includes(k))
          .map(([k, v]) => (
            <div className="omx-kv" key={k}>
              <span className="k">{k.replace(/_/g, ' ')}</span>
              <span className="v">{String(v)}</span>
            </div>
          ))}
      </div>
      {Object.keys(eco).length > 0 && (
        <div className="omx-section">
          <h4>Economy</h4>
          {Object.entries(eco).map(([k, v]: [string, any]) => (
            <div className="omx-kv" key={k}>
              <span className="k">{v?.label || k}{v?.year ? ` (${v.year})` : ''}</span>
              <span className="v">{fmt(v)}{v?.unit === 'USD' ? ' USD' : ''}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
