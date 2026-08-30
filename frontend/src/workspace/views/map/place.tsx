// Location — everything happening at the point the user just picked.
//
// The map could already say *where* something is and the graph could already
// say what is happening to a *country*. Between those two sat the question a
// world map actually gets asked: click a place, or search one, and be told
// what is going on there.
//
// This panel is that answer. It reads `/api/terra/place`, which resolves the
// point to a city/region/country and returns TERRA's corpus filtered two ways
// at once — articles that NAME the place (`scope: 'local'`) and articles about
// its country (`scope: 'country'`).
//
// Three rules it keeps, all of them the same rule:
//
//   * **The two scopes stay visibly separate.** A local row shows the term
//     that matched it. A country row says so. Merging them into one "news
//     here" list would quietly upgrade a national story into a local one,
//     which is the kind of small lie that makes a whole briefing untrustworthy.
//   * **Empty is a real answer.** Click open ocean and the panel says the
//     corpus is silent, rather than padding the space with the country's feed
//     for a country that is not there.
//   * **One fetch per point, guarded.** Clicking a second place before the
//     first answers is ordinary use; a sequence check keeps the panel from
//     describing a place the user has already left.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { geo, type EnvironmentResponse } from '../../lib/geo'
import { severityVar, severityLabel, countryName } from './highlights'
import { Empty, type Point } from './panels'

// ---------------------------------------------------------------------------
// The payload. Only the fields this panel renders are typed — the endpoint is
// the definition of the shape, and restating all of it here would make this
// file a second, silently drifting copy of it.
// ---------------------------------------------------------------------------
export interface PlaceNews {
  id: string
  title: string
  url: string
  source: string
  summary: string
  published_ts: number
  when: string
  domains: string[]
  sentiment: number
  severity: number
  countries: string[]
  scope: 'local' | 'country'
  matched: string[]
}

export interface PlaceStory {
  id: string
  title: string
  url: string
  size: number
  sources: number
  last_ts: number
  when: string
  severity: number
  domains: string[]
  countries: string[]
  status: string
  scope: 'local' | 'country'
  matched: string[]
}

export interface PlaceBrief {
  status: string
  place: {
    label: string; name: string; address: string; city: string; region: string
    country: string; iso2: string; lat: number | null; lon: number | null
    resolved_by: string; freshness: string
  }
  country: {
    id: string; iso2: string; name: string
    risk: {
      score: number; band: string; color: string; top_dimension: string
      dimensions: Record<string, number>; articles: number
    } | null
    risk_delta: number | null
  }
  terms: string[]
  summary: {
    local: number; country: number; stories: number; sources: number
    domains: Record<string, number>; sentiment: number | null
    window_hours: number; corpus_articles: number
  }
  news: PlaceNews[]
  stories: PlaceStory[]
  entities: { id: string; name: string; type: string; count: number
              glyph?: string; color?: string; type_label?: string }[]
  graph_hits: { id: string; name: string; type: string; type_label: string
                glyph: string; color: string; mentions: number }[]
}

type Scope = 'all' | 'local' | 'country'

/** Sentiment as a three-band tone, not a number.
 *
 *  A signed decimal beside a headline reads as precision the extractor does
 *  not have; a dot reads as the direction, which it does. */
function toneOf(sentiment: number): string {
  if (sentiment <= -0.25) return 'var(--omx-neg)'
  if (sentiment >= 0.25) return 'var(--omx-pos)'
  return 'var(--omx-text-faint)'
}

// ---------------------------------------------------------------------------
export function PlacePanel({ focus, isoHint, workspaceId, onFly, onCountry }: {
  focus: Point | null
  /** ISO of the country polygon under the click. The client already knows it
   *  from the layer it drew, so passing it makes the briefing correct even
   *  when the reverse geocoder is unreachable. */
  isoHint: string
  workspaceId: string
  onFly: (p: Point, zoom?: number) => void
  /** Select the country's workspace object, so the rest of OMNIX follows. */
  onCountry: (iso: string) => void
}) {
  const [brief, setBrief] = useState<PlaceBrief | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [domain, setDomain] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [env, setEnv] = useState<EnvironmentResponse | null>(null)
  const [dossier, setDossier] = useState<any>(null)
  const [dossierBusy, setDossierBusy] = useState(false)
  const seq = useRef(0)

  const key = focus ? `${focus.lat.toFixed(4)},${focus.lon.toFixed(4)}` : ''
  const label = focus?.label || ''

  // -- the briefing --------------------------------------------------------
  useEffect(() => {
    if (!focus) { setBrief(null); setError(''); return }
    const mine = ++seq.current
    setLoading(true)
    setExpanded(null)
    // 250ms: a click is one point, but dragging the focus across the map (or
    // arrowing down a search list) is several, and each one is a reverse
    // geocode plus a corpus scan on the server.
    const timer = setTimeout(() => {
      const qs = new URLSearchParams({
        lat: String(focus.lat), lon: String(focus.lon), limit: '60',
      })
      // A searched place carries its own name; a bare map click does not, and
      // sending the "17.3850, 78.4867" placeholder as a name would have the
      // server matching headlines against a coordinate string.
      if (label && !/^-?\d/.test(label)) qs.set('name', label)
      if (isoHint) qs.set('iso', isoHint)
      if (workspaceId) qs.set('workspace', workspaceId)
      fetch(`/api/terra/place?${qs}`)
        .then((r) => {
          if (!r.ok) throw new Error(`the location service returned HTTP ${r.status}`)
          return r.json()
        })
        .then((d: PlaceBrief) => {
          if (mine !== seq.current) return
          setBrief(d)
          setError('')
        })
        .catch((e: unknown) => {
          if (mine !== seq.current) return
          setBrief(null)
          setError(e instanceof Error ? e.message : 'the location service failed')
        })
        .finally(() => { if (mine === seq.current) setLoading(false) })
    }, 250)
    return () => clearTimeout(timer)
  }, [key, label, isoHint, workspaceId, focus])

  // -- conditions ----------------------------------------------------------
  // "What is happening here" includes the weather. Kept to one line: the
  // Conditions panel next door is where the full read lives, and repeating it
  // would make this panel a worse copy of that one.
  useEffect(() => {
    if (!focus) { setEnv(null); return }
    let live = true
    geo.environment(focus.lat, focus.lon)
      .then((e) => { if (live) setEnv(e) })
      .catch(() => { if (live) setEnv(null) })
    return () => { live = false }
  }, [key, focus])

  useEffect(() => { setDossier(null) }, [brief?.country.iso2])

  const loadDossier = useCallback(() => {
    const iso = brief?.country.iso2
    if (!iso) return
    setDossierBusy(true)
    fetch(`/api/terra/country/${iso}`).then((r) => r.json())
      .then(setDossier).catch(() => setDossier(null))
      .finally(() => setDossierBusy(false))
  }, [brief?.country.iso2])

  const news = useMemo(() => {
    if (!brief) return []
    return brief.news.filter((n) =>
      (scope === 'all' || n.scope === scope)
      && (!domain || n.domains.includes(domain)))
  }, [brief, scope, domain])

  const stories = useMemo(() => {
    if (!brief) return []
    return brief.stories.filter((s) =>
      (scope === 'all' || s.scope === scope)
      && (!domain || s.domains.includes(domain)))
  }, [brief, scope, domain])

  if (!focus) {
    return (
      <Empty>
        Click anywhere on the map — or pick a result in Search — and this panel
        reads back what is happening there: local news, the stories moving in
        that country, its risk, and who is involved.
      </Empty>
    )
  }

  const p = brief?.place
  const risk = brief?.country.risk
  const sum = brief?.summary
  const domains = Object.entries(sum?.domains || {})

  return (
    <div className="omx-gx-place">
      {/* -- what this place is ------------------------------------------- */}
      <header className="omx-gx-place-head">
        <div className="omx-gx-metaline">
          <h2 className="omx-gx-place-name">
            {p?.label || focus.label || 'This point'}
          </h2>
          {loading && <span className="omx-gx-pulse" />}
        </div>
        <p className="omx-gx-sub">
          {[p?.city && p.city !== p?.label ? p.city : '', p?.region, p?.country]
            .filter(Boolean).join(' · ')
            || (loading ? 'Resolving this point…' : 'Unresolved point')}
        </p>
        <div className="omx-gx-place-meta">
          <span className="omx-gx-tag">
            {focus.lat.toFixed(3)}°, {focus.lon.toFixed(3)}°
          </span>
          {p?.iso2 && <span className="omx-gx-flag">{p.iso2}</span>}
          {p?.resolved_by && (
            <span className="omx-gx-tag">via {p.resolved_by}</span>
          )}
          {env?.weather && (
            <span className="omx-gx-tag">
              {env.weather.emoji} {env.weather.temperatureC != null
                ? `${Math.round(env.weather.temperatureC)}°C` : ''}
              {env.weather.description ? ` · ${env.weather.description}` : ''}
            </span>
          )}
          {env?.airQuality?.index != null && (
            <span className="omx-gx-tag">AQI {env.airQuality.index} · {env.airQuality.band}</span>
          )}
        </div>
        <div className="omx-gx-acts">
          <button className="omx-btn" onClick={() => onFly(focus, 8)}>Centre here</button>
          {p?.iso2 && (
            <button className="omx-btn" onClick={() => onCountry(p.iso2)}>
              Select {p.country || p.iso2}
            </button>
          )}
        </div>
      </header>

      {error && <p className="omx-gx-empty">{error}</p>}

      {/* -- how loud it is ------------------------------------------------ */}
      {sum && (
        <section className="omx-gx-sec">
          <div className="omx-gx-facts">
            <div className="omx-gx-fact">
              <div className="omx-gx-lbl">naming this place</div>
              <strong>{sum.local}</strong>
            </div>
            <div className="omx-gx-fact">
              <div className="omx-gx-lbl">about {p?.iso2 || 'the country'}</div>
              <strong>{sum.country}</strong>
            </div>
            <div className="omx-gx-fact">
              <div className="omx-gx-lbl">clustered stories</div>
              <strong>{sum.stories}</strong>
            </div>
            <div className="omx-gx-fact">
              <div className="omx-gx-lbl">distinct sources</div>
              <strong>{sum.sources}</strong>
            </div>
          </div>
          <p className="omx-gx-sub dim" style={{ marginTop: 8 }}>
            Last {Math.round(sum.window_hours / 24)} days of TERRA's corpus
            {sum.sentiment != null && <> · tone{' '}
              <span style={{ color: toneOf(sum.sentiment) }}>
                {sum.sentiment <= -0.25 ? 'negative'
                  : sum.sentiment >= 0.25 ? 'positive' : 'mixed'}
              </span></>}
            {brief?.terms.length ? <> · matched on {brief.terms.join(', ')}</> : null}
          </p>
        </section>
      )}

      {/* -- risk ---------------------------------------------------------- */}
      {risk && (
        <section className="omx-gx-sec">
          <div className="omx-gx-metaline">
            <h3 className="omx-gx-h">Risk · {brief?.country.name}</h3>
            <span className="omx-gx-tag" style={{ color: risk.color }}>
              {Math.round(risk.score)} · {risk.band}
            </span>
          </div>
          <div className="omx-gx-riskbars">
            {Object.entries(risk.dimensions || {})
              .sort((a, b) => b[1] - a[1])
              .map(([dim, value]) => (
                <div className="omx-gx-riskbar" key={dim}>
                  <span className="omx-gx-lbl">{dim}</span>
                  <span className="track">
                    <i style={{ width: `${Math.min(100, value * 10)}%`,
                                background: risk.color }} />
                  </span>
                  <span className="v">{value.toFixed(1)}</span>
                </div>
              ))}
          </div>
          <p className="omx-gx-sub dim">
            Scored from {risk.articles} article{risk.articles === 1 ? '' : 's'} in
            the window, driven by {risk.top_dimension || 'no single dimension'}.
          </p>
        </section>
      )}

      {/* -- filters ------------------------------------------------------- */}
      {brief && (brief.news.length > 0 || brief.stories.length > 0) && (
        <div className="omx-gx-hi-filters">
          {(['all', 'local', 'country'] as Scope[]).map((s) => (
            <button key={s} className={`omx-chip ${scope === s ? 'on' : ''}`}
                    onClick={() => setScope(s)}>
              {s === 'local' ? `here (${sum?.local ?? 0})`
                : s === 'country' ? `${p?.iso2 || 'country'} (${sum?.country ?? 0})`
                : 'everything'}
            </button>
          ))}
          {domains.map(([d, n]) => (
            <button key={d} className={`omx-chip ${domain === d ? 'on' : ''}`}
                    onClick={() => setDomain(domain === d ? '' : d)}>
              {d} · {n}
            </button>
          ))}
        </div>
      )}

      {/* -- what is happening --------------------------------------------- */}
      {stories.length > 0 && (
        <section className="omx-gx-sec">
          <h3 className="omx-gx-h">What's happening</h3>
          <div className="omx-gx-hi-list">
            {stories.map((s) => (
              <button key={s.id} className="omx-gx-story"
                      style={{ ['--omx-sev' as string]: severityVar(s.severity) }}
                      data-on={expanded === s.id}
                      onClick={() => setExpanded(expanded === s.id ? null : s.id)}>
                <span className="omx-gx-story-rank">{s.size}</span>
                <span className="omx-gx-story-body">
                  <span className="omx-gx-story-title">{s.title}</span>
                  <span className="omx-gx-story-meta">
                    <span className="sev">{severityLabel(s.severity)}</span>
                    {s.when && <span>{s.when}</span>}
                    <span>{s.sources} source{s.sources === 1 ? '' : 's'}</span>
                    {s.scope === 'local'
                      ? <span className="omx-gx-here">names {s.matched[0]}</span>
                      : (s.countries || []).slice(0, 2).map((c) => (
                          <span className="omx-gx-flag" key={c}>{c}</span>))}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* -- the news ------------------------------------------------------ */}
      <section className="omx-gx-sec">
        <div className="omx-gx-metaline">
          <h3 className="omx-gx-h">Latest news</h3>
          <span className="omx-gx-tag">{news.length}</span>
        </div>

        {!loading && brief && news.length === 0 && (
          <p className="omx-gx-empty">
            {brief.summary.local + brief.summary.country === 0
              ? (brief.place.iso2
                  ? `TERRA's corpus has nothing from ${brief.place.country || brief.place.iso2} in the last ${Math.round(brief.summary.window_hours / 24)} days.`
                  : 'That point is not in any country TERRA covers — open water, or unclaimed territory.')
              : 'Nothing under this filter. Widen the scope or clear the domain.'}
          </p>
        )}
        {loading && !brief && <p className="omx-gx-sub dim">Reading the corpus…</p>}

        <div className="omx-gx-newslist">
          {news.map((n) => (
            <article className="omx-gx-newsrow" key={n.id} data-scope={n.scope}>
              <a href={n.url} target="_blank" rel="noopener noreferrer"
                 className="omx-gx-newstitle">{n.title}</a>
              <div className="omx-gx-story-meta">
                <span style={{ color: toneOf(n.sentiment) }}>●</span>
                <span>{n.source || 'unattributed'}</span>
                <span>{n.when}</span>
                {n.scope === 'local'
                  ? <span className="omx-gx-here">names {n.matched.join(', ')}</span>
                  : (n.countries || []).slice(0, 2).map((c) => (
                      <span className="omx-gx-flag" key={c} title={countryName(c)}>{c}</span>))}
                {(n.domains || []).slice(0, 2).map((d) => <span key={d}>{d}</span>)}
              </div>
              {n.summary && <p className="omx-gx-newssum">{n.summary}</p>}
            </article>
          ))}
        </div>
      </section>

      {/* -- who and what ---------------------------------------------------
          Counted over the articles above, so these are the names in the
          stories on screen rather than a generic "important in this country"
          list the reader cannot trace. */}
      {brief && brief.entities.length > 0 && (
        <section className="omx-gx-sec">
          <h3 className="omx-gx-h">Who and what is involved</h3>
          <div className="omx-gx-chips">
            {brief.entities.map((e) => (
              <span className="omx-gx-tag" key={e.id} title={e.type_label || e.type}>
                {e.glyph && <span style={{ color: e.color, marginRight: 4 }}>{e.glyph}</span>}
                {e.name} · {e.count}
              </span>
            ))}
          </div>
        </section>
      )}

      {brief && brief.graph_hits.length > 0 && (
        <section className="omx-gx-sec">
          <h3 className="omx-gx-h">TERRA objects named for this place</h3>
          <div className="omx-gx-chips">
            {brief.graph_hits.map((g) => (
              <span className="omx-gx-tag" key={g.id} title={g.type_label}>
                <span style={{ color: g.color, marginRight: 4 }}>{g.glyph}</span>
                {g.name}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* -- dossier --------------------------------------------------------
          On demand, not on selection: the country card fetches World Bank
          indicators live and was measured at ~60s cold, so firing it on every
          map click would put a minute-long spinner behind every point. */}
      {brief?.country.iso2 && (
        <section className="omx-gx-sec">
          <h3 className="omx-gx-h">Country dossier</h3>
          {!dossier && (
            <>
              <button className="omx-btn" onClick={loadDossier} disabled={dossierBusy}>
                {dossierBusy ? 'Fetching (up to a minute)…'
                  : `Load ${brief.country.name || brief.country.iso2} dossier`}
              </button>
              <p className="omx-gx-sub dim">
                Profile, capital, currency and World Bank indicators, fetched
                live from the source.
              </p>
            </>
          )}
          {dossier?.status === 'ok' && (
            <>
              <div className="omx-gx-facts">
                {Object.entries(dossier.profile || {})
                  .filter(([k, v]) => v && !['flag', 'latlng'].includes(k))
                  .slice(0, 8)
                  .map(([k, v]) => (
                    <div className="omx-gx-fact" key={k}>
                      <div className="omx-gx-lbl">{k.replace(/_/g, ' ')}</div>
                      <strong>{String(v)}</strong>
                    </div>
                  ))}
              </div>
              <div className="omx-gx-facts" style={{ marginTop: 8 }}>
                {Object.entries(dossier.economy || {}).map(([k, v]: [string, any]) => (
                  <div className="omx-gx-fact" key={k}>
                    <div className="omx-gx-lbl">
                      {v?.label || k}{v?.year ? ` (${v.year})` : ''}
                    </div>
                    <strong>{v?.display ?? String(v?.value ?? '—')}</strong>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  )
}
