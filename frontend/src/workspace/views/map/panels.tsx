// The sidebar's panels.
//
// These replace TERRA Live's, which were flat label/value lists — accurate and
// dull. The changes here are not decoration; each one answers a question the
// old version made the user do in their head:
//
//   * A **forecast strip** answers "will this last?", which a single current
//     temperature cannot. It is a real chart, not a row of numbers.
//   * **Gauges** for AQI and UV put a number on its scale. "UV 8" means
//     nothing without knowing that 11 is the top and 8 is where sunburn starts.
//   * **Route comparison** shows the alternatives against each other with the
//     trade-off named, instead of three similar-looking rows.
//   * An **elevation profile** was already in the API and nothing rendered it.
//   * POI cards carry the **ranking breakdown**, so "why is this first" has an
//     answer rather than a shrug.
//
// Hovering any result highlights it on the map, and vice versa: `onHover` is
// threaded through every list. That link is what makes a list beside a map feel
// like one instrument rather than two panes.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  geo, humanDistance, humanDuration, freshnessLabel, ageLabel,
  type Freshness, type GeoConfig, type GeoPlace, type GeoRoute,
  type Geofence, type HourlyPoint, type SavedPlace,
} from '../../lib/geo'

export interface Point { lat: number; lon: number; label?: string }

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------
export function Fresh({ freshness, ageS, provider }: {
  freshness?: Freshness; ageS?: number | null; provider?: string
}) {
  if (!freshness) return null
  const { text, tone } = freshnessLabel(freshness)
  const age = ageLabel(ageS)
  return (
    <span className="omx-gx-fresh" data-tone={tone}
          title={[provider && `via ${provider}`, age].filter(Boolean).join(' · ')}>
      {text}{age ? ` · ${age}` : ''}
    </span>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="omx-gx-empty">{children}</p>
}

function Spinner({ label }: { label: string }) {
  return (
    <p className="omx-gx-empty" aria-live="polite">
      <span className="omx-gx-pulse" /> {label}…
    </p>
  )
}

function Fact({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="omx-gx-fact" data-tone={tone}>
      <dt>{k}</dt><dd>{v}</dd>
    </div>
  )
}

const num = (v: number | null | undefined, unit: string) =>
  v === null || v === undefined ? '—' : `${Math.round(v * 10) / 10}${unit}`

/** A value on its scale.
 *
 *  The whole point is the RANGE. An AQI of 41 and a UV of 8 are both "a
 *  number" until you can see that one sits near the bottom of its scale and
 *  the other near the top. The band label comes from the backend so the
 *  wording matches the scale actually used (US AQI and European AQI disagree
 *  about what 55 means).
 */
function Gauge({ label, value, max, band, tone, suffix = '' }: {
  label: string; value: number | null | undefined; max: number
  band?: string; tone: string; suffix?: string
}) {
  const pct = value === null || value === undefined
    ? 0 : Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="omx-gx-gauge" data-tone={tone}>
      <div className="omx-gx-gauge-head">
        <span className="omx-gx-gauge-label">{label}</span>
        <span className="omx-gx-gauge-value">
          {value === null || value === undefined
            ? '—' : `${Math.round(value)}${suffix}`}
          {band && <em>{band}</em>}
        </span>
      </div>
      <div className="omx-gx-gauge-track">
        <span className="omx-gx-gauge-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

/** Which tone an AQI value earns, on the scale it was measured against. */
function aqiTone(index: number | null | undefined, scale: string): string {
  if (index === null || index === undefined) return 'muted'
  const limits = scale === 'european_aqi' ? [20, 40, 60, 80] : [50, 100, 150, 200]
  if (index <= limits[0]) return 'ok'
  if (index <= limits[1]) return 'fair'
  if (index <= limits[2]) return 'warn'
  return 'bad'
}

function uvTone(uv: number | null | undefined): string {
  if (uv === null || uv === undefined) return 'muted'
  if (uv < 3) return 'ok'
  if (uv < 6) return 'fair'
  if (uv < 8) return 'warn'
  return 'bad'
}

// ---------------------------------------------------------------------------
// Forecast strip
// ---------------------------------------------------------------------------
/** 24 hours of temperature and rain chance as one small chart.
 *
 *  Temperature is a line because it is continuous; rain probability is bars
 *  because each hour is its own independent chance. Drawing rain as a second
 *  line implied a trend between hours that the data does not claim.
 *
 *  Inline SVG rather than a chart library: it is 40 points, and adding a
 *  charting dependency to a bundle that already carries MapLibre would be
 *  absurd.
 */
function ForecastStrip({ hours }: { hours: HourlyPoint[] }) {
  const [hover, setHover] = useState<number | null>(null)
  if (!hours.length) return null

  const W = 320, H = 74, PAD = 6
  const temps = hours.map((h) => h.temperatureC ?? 0)
  const lo = Math.min(...temps), hi = Math.max(...temps)
  const span = Math.max(1, hi - lo)
  const x = (i: number) => PAD + (i / Math.max(1, hours.length - 1)) * (W - PAD * 2)
  const y = (t: number) => H - 22 - ((t - lo) / span) * (H - 40)

  const line = hours
    .map((h, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(h.temperatureC ?? lo).toFixed(1)}`)
    .join(' ')
  const active = hover === null ? null : hours[hover]

  return (
    <div className="omx-gx-strip-chart">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}
           role="img" aria-label="24 hour forecast"
           onMouseLeave={() => setHover(null)}>
        {hours.map((h, i) => {
          const p = h.precipitationProbabilityPct ?? 0
          const bh = (p / 100) * (H - 34)
          return (
            <rect key={`r${i}`} x={x(i) - 4.5} y={H - 16 - bh} width={9} height={bh}
                  className="omx-gx-rainbar" rx={1.5} />
          )
        })}
        <path d={line} className="omx-gx-templine" fill="none" />
        {hours.map((h, i) => (
          <circle key={`c${i}`} cx={x(i)} cy={y(h.temperatureC ?? lo)} r={2}
                  className="omx-gx-tempdot" data-on={hover === i} />
        ))}
        {/* Full-height hit targets. Hovering a 2px dot is a precision task
            nobody should be asked to perform. */}
        {hours.map((_, i) => (
          <rect key={`h${i}`} x={x(i) - 5} y={0} width={10} height={H}
                fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        {hours.map((h, i) => (
          i % 4 === 0 ? (
            <text key={`t${i}`} x={x(i)} y={H - 3} className="omx-gx-axis"
                  textAnchor="middle">{String(h.hour).padStart(2, '0')}</text>
          ) : null
        ))}
      </svg>
      <div className="omx-gx-strip-read">
        {active ? (
          <>
            <strong>{String(active.hour).padStart(2, '0')}:00</strong>
            <span>{active.emoji} {active.description}</span>
            <span>{num(active.temperatureC, '°C')}</span>
            <span>{num(active.precipitationProbabilityPct, '% rain')}</span>
          </>
        ) : (
          <span className="dim">
            {Math.round(lo)}–{Math.round(hi)}°C over the next {hours.length} h ·
            hover for detail
          </span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
const QUICK = ['cafe', 'restaurant', 'pharmacy', 'hospital', 'atm', 'fuel',
               'supermarket', 'park', 'library', 'transit', 'hotel', 'gym']

export function SearchPanel({ workspaceId, focus, onPick, onSearch, busy }: {
  workspaceId: string; focus: Point | null
  onPick: (p: Point) => void
  onSearch: (o: { category?: string; q?: string; radius?: number
                  openNow?: boolean }) => void
  busy: boolean
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<GeoPlace[]>([])
  const [meta, setMeta] = useState<{ freshness?: Freshness; provider?: string }>({})
  const [searching, setSearching] = useState(false)
  const [radius, setRadius] = useState(2000)
  const [openNow, setOpenNow] = useState(false)
  const [ask, setAsk] = useState('')
  const [askOut, setAskOut] = useState('')
  const seq = useRef(0)

  // Debounced autocomplete. 350ms because Nominatim is rate-limited to 1 req/s
  // and a keystroke-per-request would spend the whole budget on prefixes of a
  // word. `seq` discards out-of-order responses — without it a slow early
  // request lands after a fast later one and the list shows results for text
  // the user has already replaced.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 3 || !workspaceId) { setResults([]); return }
    const mine = ++seq.current
    const timer = setTimeout(() => {
      setSearching(true)
      geo.geocode(workspaceId, q, focus ?? undefined)
        .then((r) => {
          if (mine !== seq.current) return
          setResults(r.results)
          setMeta({ freshness: r.freshness, provider: r.provider })
        })
        .catch(() => { if (mine === seq.current) setResults([]) })
        .finally(() => { if (mine === seq.current) setSearching(false) })
    }, 350)
    return () => clearTimeout(timer)
  }, [query, workspaceId, focus])

  const runAsk = async () => {
    if (!ask.trim() || !workspaceId) return
    setAskOut('working…')
    try {
      const r = await geo.ask(workspaceId, ask.trim(),
                              focus ? { lat: focus.lat, lon: focus.lon } : undefined)
      if (!r.matched) { setAskOut(r.note || 'No TERRA tool fits that.'); return }
      setAskOut(`${r.tool} · resolved by ${r.path}`)
      const out = r.result as { places?: GeoPlace[] } | undefined
      if (out?.places?.length) {
        const first = out.places[0]
        onPick({ lat: first.lat, lon: first.lon, label: first.name })
      }
    } catch (e) {
      setAskOut(e instanceof Error ? e.message : 'failed')
    }
  }

  return (
    <>
      <section className="omx-gx-sec">
        <div className="omx-gx-metaline">
          <h3 className="omx-gx-h">Find a place</h3>
          {searching ? <span className="omx-gx-pulse" />
                     : <Fresh {...meta} />}
        </div>
        <input className="omx-gx-input wide" value={query}
               placeholder="Address, landmark or city"
               onChange={(e) => setQuery(e.target.value)} />
        {query.trim().length > 0 && query.trim().length < 3 && (
          <p className="omx-gx-sub dim">Keep typing…</p>
        )}
        <ul className="omx-gx-list">
          {results.map((r, i) => (
            <li key={`${r.externalId}-${i}`}>
              <button className="omx-gx-item"
                      onClick={() => onPick({ lat: r.lat, lon: r.lon, label: r.name })}>
                <strong>{r.name}</strong>
                <span className="omx-gx-sub">{r.address || r.category}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">What's around</h3>
        {!focus && <Empty>Click the map, or use your location, to set a point.</Empty>}
        <div className="omx-gx-chips">
          {QUICK.map((c) => (
            <button key={c} className="omx-chip" disabled={!focus || busy}
                    onClick={() => onSearch({ category: c, radius, openNow })}>
              {c}
            </button>
          ))}
        </div>
        <div className="omx-gx-row">
          <label className="omx-gx-lbl">
            Radius
            <select className="omx-gx-input" value={radius}
                    onChange={(e) => setRadius(Number(e.target.value))}>
              <option value={500}>500 m</option>
              <option value={1000}>1 km</option>
              <option value={2000}>2 km</option>
              <option value={5000}>5 km</option>
              <option value={10000}>10 km</option>
            </select>
          </label>
          <label className="omx-gx-check">
            <input type="checkbox" checked={openNow}
                   onChange={(e) => setOpenNow(e.target.checked)} />
            Open now
          </label>
        </div>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Ask TERRA</h3>
        <div className="omx-gx-row">
          <input className="omx-gx-input" value={ask}
                 placeholder="Nearest pharmacy · somewhere quiet to work"
                 onChange={(e) => setAsk(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && runAsk()} />
          <button className="omx-btn" onClick={runAsk}>Ask</button>
        </div>
        {askOut && <p className="omx-gx-sub">{askOut}</p>}
      </section>
    </>
  )
}

// ---------------------------------------------------------------------------
// Places
// ---------------------------------------------------------------------------
const SCORE_LABEL: Record<string, string> = {
  distance: 'how close it is', rating: 'its rating',
  open: 'being open', quiet: 'how quiet it is', amenity: 'how complete the record is',
}

export function PlacesPanel({ places, meta, busy, hovered, onHover, onPick,
                              onRoute, onSave }: {
  places: GeoPlace[]
  meta: { freshness?: Freshness; ageS?: number | null; provider?: string
          attempted?: string[] }
  busy: boolean
  hovered: string | null
  onHover: (id: string | null) => void
  onPick: (p: Point) => void
  onRoute: (p: Point) => void
  onSave: (p: Point & { label: string }) => void
}) {
  const truncated = (meta.attempted || []).some((a) => a.includes('truncated'))
  if (busy) return <Spinner label="Searching" />
  if (!places.length) {
    return <Empty>Nothing found yet — pick a category from Search.</Empty>
  }
  return (
    <section className="omx-gx-sec">
      <div className="omx-gx-metaline">
        <span className="omx-gx-count">{places.length} places</span>
        <Fresh {...meta} />
      </div>
      {truncated && (
        <p className="omx-gx-warn">
          The provider capped this response, so it covers part of the radius
          rather than all of it. Narrow the radius for complete coverage.
        </p>
      )}
      <ul className="omx-gx-list">
        {places.map((p, i) => {
          const id = p.externalId || `${p.lat},${p.lon}`
          const score = p.tags?._score
          const parts = Object.entries(p.tags || {})
            .filter(([k, v]) => k.startsWith('_score_') && Math.abs(Number(v)) > 0.01)
            .sort((a, b) => Number(b[1]) - Number(a[1]))
          return (
            <li key={`${id}-${i}`} className="omx-gx-card" data-on={hovered === id}
                onMouseEnter={() => onHover(id)}
                onMouseLeave={() => onHover(null)}>
              <button className="omx-gx-item" onClick={() =>
                onPick({ lat: p.lat, lon: p.lon, label: p.name })}>
                <div className="omx-gx-card-head">
                  <strong>{p.name}</strong>
                  {p.distanceM !== null && (
                    <span className="omx-gx-dist">{humanDistance(p.distanceM)}</span>
                  )}
                </div>
                <div className="omx-gx-card-meta">
                  {p.category && <span className="omx-gx-tag">{p.category}</span>}
                  {p.rating !== null && (
                    <span className="omx-gx-rate">
                      ★ {p.rating}
                      {p.ratingCount ? <em>({p.ratingCount})</em> : null}
                    </span>
                  )}
                  {p.openNow === true && <span className="omx-gx-open">open</span>}
                  {p.openNow === false && <span className="omx-gx-shut">closed</span>}
                  {p.wheelchair === 'yes' && <span className="omx-gx-tag">♿</span>}
                  {p.priceLevel !== null && p.priceLevel !== undefined && (
                    <span className="omx-gx-tag">{'₹'.repeat(Math.max(1, p.priceLevel))}</span>
                  )}
                </div>
                {p.address && <span className="omx-gx-sub dim">{p.address}</span>}
                {p.openingHours && (
                  <span className="omx-gx-sub dim">{p.openingHours.slice(0, 70)}</span>
                )}
              </button>
              <div className="omx-gx-acts">
                <button className="omx-gx-mini" onClick={() =>
                  onRoute({ lat: p.lat, lon: p.lon, label: p.name })}>Route</button>
                <button className="omx-gx-mini" onClick={() =>
                  onSave({ lat: p.lat, lon: p.lon, label: p.name })}>Save</button>
                {p.phone && <a className="omx-gx-mini" href={`tel:${p.phone}`}>Call</a>}
                {p.website && (
                  <a className="omx-gx-mini" href={p.website} target="_blank"
                     rel="noreferrer noopener">Site</a>
                )}
                {/* The ranking is inspectable. A list that claims an order
                    should be able to justify it. */}
                {score && parts.length > 0 && (
                  <details className="omx-gx-why">
                    <summary>why</summary>
                    <ul>
                      {parts.slice(0, 4).map(([k, v]) => (
                        <li key={k}>
                          {SCORE_LABEL[k.replace('_score_', '')] ?? k}
                          <span>{Number(v) > 0 ? '+' : ''}{Number(v).toFixed(2)}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Route
// ---------------------------------------------------------------------------
const MODES = [
  { id: 'driving', label: 'Drive' },
  { id: 'walking', label: 'Walk' },
  { id: 'cycling', label: 'Cycle' },
]

export function RoutePanel({ routes, meta, active, busy, saved, focus, me,
                             onSelect, onChoose, onRun, onStepHover }: {
  routes: GeoRoute[]
  meta: { freshness?: Freshness; provider?: string; explanations?: string[]
          origin?: Point; destination?: Point; mode?: string; error?: string }
  active: number; busy: boolean; saved: SavedPlace[]
  focus: Point | null; me: Point | null
  onSelect: (i: number) => void
  onChoose: (i: number) => void
  onRun: (o: Point | string, d: Point | string, mode: string, prefer?: string) => void
  onStepHover: (coord: [number, number] | null) => void
}) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [travel, setTravel] = useState('driving')
  const [prefer, setPrefer] = useState('score')
  const [profile, setProfile] = useState<{
    points: { elevationM: number }[]; gainM: number; lossM: number
    minM: number | null; maxM: number | null
  } | null>(null)
  const [profileBusy, setProfileBusy] = useState(false)

  const origin: Point | string = from.trim() || me || focus || ''
  const destination: Point | string = to.trim() || focus || ''
  const current = routes[active]

  // The elevation profile was built into the API and nothing ever asked for
  // it. Fetched only for the ACTIVE route and only on demand, because it is a
  // real provider call and most journeys are flat enough not to care.
  const loadProfile = useCallback(async () => {
    if (!current?.geometry?.length) return
    setProfileBusy(true)
    try {
      const r = await geo.elevationProfile(current.geometry)
      setProfile(r.profile)
    } catch { setProfile(null) } finally { setProfileBusy(false) }
  }, [current])

  useEffect(() => { setProfile(null) }, [active, routes])

  const best = useMemo(() => {
    if (routes.length < 2) return null
    const fastest = routes.reduce((a, b) =>
      (a.durationTrafficS ?? a.durationS) <= (b.durationTrafficS ?? b.durationS) ? a : b)
    const shortest = routes.reduce((a, b) => a.distanceM <= b.distanceM ? a : b)
    return { fastest, shortest }
  }, [routes])

  return (
    <>
      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Plan a route</h3>
        <input className="omx-gx-input wide" value={from}
               placeholder={me ? 'From — blank uses your position' : 'From'}
               onChange={(e) => setFrom(e.target.value)} />
        <input className="omx-gx-input wide" value={to}
               placeholder="To — blank uses the selected point"
               onChange={(e) => setTo(e.target.value)} />
        {saved.length > 0 && (
          <div className="omx-gx-chips">
            {saved.slice(0, 6).map((s) => (
              <button key={s.id} className="omx-chip"
                      onClick={() => setTo(s.label)}>{s.label}</button>
            ))}
          </div>
        )}
        <div className="omx-gx-row">
          {MODES.map((m) => (
            <button key={m.id} className="omx-chip" data-on={travel === m.id}
                    onClick={() => setTravel(m.id)}>{m.label}</button>
          ))}
        </div>
        <div className="omx-gx-row">
          <label className="omx-gx-lbl">
            Rank by
            <select className="omx-gx-input" value={prefer}
                    onChange={(e) => setPrefer(e.target.value)}>
              <option value="score">Your preferences</option>
              <option value="fastest">Fastest</option>
              <option value="shortest">Shortest</option>
            </select>
          </label>
          <button className="omx-btn" disabled={busy || !destination}
                  onClick={() => onRun(origin, destination, travel, prefer)}>
            {busy ? '…' : 'Find routes'}
          </button>
        </div>
      </section>

      {busy && <Spinner label="Routing" />}

      {!busy && routes.length > 0 && (
        <section className="omx-gx-sec">
          <div className="omx-gx-metaline">
            <span className="omx-gx-count">
              {meta.origin?.label} → {meta.destination?.label}
            </span>
            <Fresh freshness={meta.freshness} provider={meta.provider} />
          </div>
          {meta.freshness === 'estimated' && (
            <p className="omx-gx-warn">
              No router is configured for {meta.mode}, so this follows roads at
              an assumed speed. It is an estimate, not a route.
            </p>
          )}

          <ul className="omx-gx-list">
            {routes.map((r, i) => {
              const dur = r.durationTrafficS ?? r.durationS
              const delay = r.durationTrafficS
                ? r.durationTrafficS - r.durationS : 0
              return (
                <li key={i} className="omx-gx-route" data-on={i === active}>
                  <button className="omx-gx-item" onClick={() => onSelect(i)}>
                    <div className="omx-gx-card-head">
                      <strong>{humanDuration(dur)}</strong>
                      <span className="omx-gx-dist">{humanDistance(r.distanceM)}</span>
                    </div>
                    <div className="omx-gx-card-meta">
                      {best?.fastest === r && <span className="omx-gx-open">fastest</span>}
                      {best?.shortest === r && <span className="omx-gx-tag">shortest</span>}
                      {delay > 60 && (
                        <span className="omx-gx-shut">
                          +{humanDuration(delay)} traffic
                        </span>
                      )}
                      {r.tolls && <span className="omx-gx-tag">tolls</span>}
                    </div>
                    {r.summary && <span className="omx-gx-sub">{r.summary}</span>}
                    {meta.explanations?.[i] && (
                      <span className="omx-gx-sub dim">{meta.explanations[i]}</span>
                    )}
                  </button>
                  {i === active && (
                    <div className="omx-gx-acts">
                      <button className="omx-gx-mini"
                              onClick={() => onChoose(i)}>Take this route</button>
                      <button className="omx-gx-mini" onClick={loadProfile}
                              disabled={profileBusy}>
                        {profileBusy ? '…' : 'Elevation'}
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>

          {profile && profile.points.length > 1 && (
            <ElevationProfile profile={profile} />
          )}

          {current?.steps?.length > 0 && (
            <details className="omx-gx-steps" open>
              <summary>{current.steps.length} directions</summary>
              <ol onMouseLeave={() => onStepHover(null)}>
                {current.steps.map((s, i) => (
                  <li key={i}
                      onMouseEnter={() => onStepHover(
                        s.coord ? [s.coord.lon, s.coord.lat] : null)}>
                    {s.instruction || 'Continue'}
                    <span className="omx-gx-sub dim">
                      {humanDistance(s.distanceM)}
                    </span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </section>
      )}
    </>
  )
}

/** The route's height profile. Area chart, because the thing being read is
 *  "how much climbing", which is an accumulation, not a series of readings. */
function ElevationProfile({ profile }: {
  profile: { points: { elevationM: number }[]; gainM: number; lossM: number
             minM: number | null; maxM: number | null }
}) {
  const W = 320, H = 64
  const pts = profile.points.map((p) => p.elevationM)
  const lo = profile.minM ?? Math.min(...pts)
  const hi = profile.maxM ?? Math.max(...pts)
  const span = Math.max(1, hi - lo)
  const x = (i: number) => (i / Math.max(1, pts.length - 1)) * W
  const y = (v: number) => H - 14 - ((v - lo) / span) * (H - 22)
  const line = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${W},${H - 14} L0,${H - 14} Z`
  return (
    <div className="omx-gx-elev">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img"
           aria-label="Elevation profile">
        <path d={area} className="omx-gx-elev-area" />
        <path d={line} className="omx-gx-elev-line" fill="none" />
      </svg>
      <div className="omx-gx-strip-read">
        <span>▲ {Math.round(profile.gainM)} m</span>
        <span>▼ {Math.round(profile.lossM)} m</span>
        <span className="dim">{Math.round(lo)}–{Math.round(hi)} m</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Conditions
// ---------------------------------------------------------------------------
export function ConditionsPanel({ focus }: { focus: Point | null }) {
  const [env, setEnv] = useState<Awaited<ReturnType<typeof geo.environment>> | null>(null)
  const [hours, setHours] = useState<HourlyPoint[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!focus) { setEnv(null); setHours([]); return }
    let live = true
    // Clicking a second point before the first answers is ordinary use, and
    // these are two live network calls deep. Without the guard the slower pair
    // lands last and the panel describes a place the user is no longer on.
    setLoading(true)
    Promise.all([
      geo.environment(focus.lat, focus.lon).catch(() => null),
      geo.forecast(focus.lat, focus.lon, 24).catch(() => null),
    ]).then(([e, f]) => {
      if (!live) return
      setEnv(e)
      setHours(f?.forecast?.hours ?? [])
    }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [focus])

  if (!focus) return <Empty>Pick a point to read conditions there.</Empty>
  if (loading) return <Spinner label="Reading conditions" />
  if (!env) return <Empty>Conditions are unavailable for that point.</Empty>

  const w = env.weather
  const aq = env.airQuality
  const status = env.dataStatus || {}

  return (
    <>
      {w && (
        <section className="omx-gx-sec">
          <div className="omx-gx-metaline">
            <h3 className="omx-gx-h">Now</h3>
            <Fresh freshness={status.weather?.freshness}
                   provider={status.weather?.provider} />
          </div>
          <div className="omx-gx-now">
            <span className="omx-gx-now-icon">{w.emoji}</span>
            <div>
              <strong className="omx-gx-now-temp">{num(w.temperatureC, '°')}</strong>
              <span className="omx-gx-sub">{w.description}</span>
              {w.feelsLikeC !== null && (
                <span className="omx-gx-sub dim">
                  feels like {num(w.feelsLikeC, '°C')}
                </span>
              )}
            </div>
          </div>
          {hours.length > 0 && <ForecastStrip hours={hours} />}
          <dl className="omx-gx-facts">
            <Fact k="Rain chance" v={num(w.precipitationProbabilityPct, '%')} />
            <Fact k="Humidity" v={num(w.humidityPct, '%')} />
            <Fact k="Wind" v={num(w.windKph, ' km/h')} />
            <Fact k="Cloud" v={num(w.cloudCoverPct, '%')} />
          </dl>
        </section>
      )}

      <section className="omx-gx-sec">
        <div className="omx-gx-metaline">
          <h3 className="omx-gx-h">Exposure</h3>
          <Fresh freshness={status.airQuality?.freshness}
                 provider={status.airQuality?.provider} />
        </div>
        {aq?.index !== null && aq?.index !== undefined && (
          <Gauge label={`Air quality · ${aq.scale}`} value={aq.index}
                 max={aq.scale === 'european_aqi' ? 100 : 300}
                 band={aq.band} tone={aqiTone(aq.index, aq.scale)} />
        )}
        {w?.uvIndex !== null && w?.uvIndex !== undefined && (
          <Gauge label="UV index" value={w.uvIndex} max={11}
                 band={w.uvIndex >= 8 ? 'very high' : w.uvIndex >= 6 ? 'high'
                       : w.uvIndex >= 3 ? 'moderate' : 'low'}
                 tone={uvTone(w.uvIndex)} />
        )}
        {aq && (
          <dl className="omx-gx-facts">
            <Fact k="PM2.5" v={num(aq.pm25, ' µg/m³')} />
            <Fact k="PM10" v={num(aq.pm10, ' µg/m³')} />
            <Fact k="Ozone" v={num(aq.ozone, ' µg/m³')} />
            {aq.dominant && <Fact k="Driven by" v={aq.dominant} />}
          </dl>
        )}
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Daylight</h3>
        <dl className="omx-gx-facts">
          <Fact k="Sunrise" v={env.sun.sunrise || '—'} />
          <Fact k="Sunset" v={env.sun.sunset || '—'} />
        </dl>
        {env.sun.note && <p className="omx-gx-sub">{env.sun.note}</p>}
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Being outside</h3>
        {/* Facts only. The recommendation is NOVA's — `outdoor_signals` has no
            verdict field on purpose, and putting one here would hide the
            reasoning behind a badge. */}
        {env.signals.favourable.length > 0 && (
          <ul className="omx-gx-sig" data-tone="ok">
            {env.signals.favourable.map((s) => <li key={s}>{s}</li>)}
          </ul>
        )}
        {env.signals.concerns.length > 0 && (
          <ul className="omx-gx-sig" data-tone="warn">
            {env.signals.concerns.map((s) => <li key={s}>{s}</li>)}
          </ul>
        )}
        <p className="omx-gx-sub dim">
          TERRA reports the conditions. Ask OMNIX for the judgement.
        </p>
      </section>
    </>
  )
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------
export function MemoryPanel({ workspaceId, saved, focus, onRefresh, onPick,
                              onNotice, hovered, onHover }: {
  workspaceId: string; saved: SavedPlace[]; focus: Point | null
  onRefresh: () => void; onPick: (p: Point) => void
  onNotice: (s: string) => void
  hovered: string | null; onHover: (id: string | null) => void
}) {
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState('saved')
  const [history, setHistory] = useState<Awaited<ReturnType<typeof geo.history>> | null>(null)

  const loadHistory = useCallback(() => {
    if (!workspaceId) return
    geo.history(workspaceId, 30).then(setHistory).catch(() => setHistory(null))
  }, [workspaceId])

  useEffect(() => { loadHistory() }, [loadHistory])

  const save = async () => {
    if (!label.trim() || !focus || !workspaceId) return
    await geo.savePlace({ workspace: workspaceId, label: label.trim(),
                          lat: focus.lat, lon: focus.lon, kind })
    setLabel('')
    onRefresh()
    onNotice(`Saved "${label.trim()}" — it will never be geocoded again.`)
  }

  return (
    <>
      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Save this point</h3>
        {!focus && <Empty>Click the map to choose a point first.</Empty>}
        <div className="omx-gx-row">
          <input className="omx-gx-input" value={label} placeholder="Home, College…"
                 disabled={!focus}
                 onChange={(e) => setLabel(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && save()} />
          <select className="omx-gx-input" value={kind}
                  onChange={(e) => setKind(e.target.value)}>
            <option value="home">Home</option>
            <option value="work">Work</option>
            <option value="study">Study</option>
            <option value="saved">Saved</option>
          </select>
          <button className="omx-btn" onClick={save}
                  disabled={!focus || !label.trim()}>Save</button>
        </div>
        <p className="omx-gx-sub dim">
          Saved places resolve from the database, so questions naming them cost
          no API calls and work offline.
        </p>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Known locations</h3>
        {!saved.length && <Empty>Nothing saved yet.</Empty>}
        <ul className="omx-gx-list">
          {saved.map((s) => (
            <li key={s.id} className="omx-gx-card" data-on={hovered === s.id}
                onMouseEnter={() => onHover(s.id)}
                onMouseLeave={() => onHover(null)}>
              <button className="omx-gx-item"
                      onClick={() => onPick({ lat: s.lat, lon: s.lon, label: s.label })}>
                <div className="omx-gx-card-head">
                  <strong>{s.label}</strong>
                  {s.visitCount > 0 && (
                    <span className="omx-gx-dist">{s.visitCount} visits</span>
                  )}
                </div>
                <span className="omx-gx-sub">{s.kind}</span>
              </button>
              <div className="omx-gx-acts">
                <button className="omx-gx-mini" onClick={async () => {
                  await geo.deletePlace(workspaceId, s.id); onRefresh()
                }}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="omx-gx-sec">
        <div className="omx-gx-metaline">
          <h3 className="omx-gx-h">Location history</h3>
          {history && (
            <span className="omx-gx-fresh"
                  data-tone={history.privacyMode ? 'warn' : 'muted'}>
              {history.privacyMode ? 'Privacy mode'
                : history.enabled ? `kept ${history.retentionDays} days`
                : 'disabled'}
            </span>
          )}
        </div>
        {!history?.history.length && <Empty>No history recorded.</Empty>}
        <ul className="omx-gx-list">
          {(history?.history || []).slice(0, 10).map((h) => (
            <li key={h.id}>
              <button className="omx-gx-item"
                      onClick={() => onPick({ lat: h.lat, lon: h.lon })}>
                <strong>{h.label || `${h.lat.toFixed(3)}, ${h.lon.toFixed(3)}`}</strong>
                <span className="omx-gx-sub">
                  {new Date(h.arrivedAt).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="omx-gx-row">
          <button className="omx-btn" onClick={async () => {
            if (!workspaceId) return
            const r = await geo.forgetHistory(workspaceId)
            loadHistory(); onNotice(`Deleted ${r.deleted} history records.`)
          }}>Delete all history</button>
          <button className="omx-btn" onClick={async () => {
            const r = await geo.privacy({ privacyMode: !history?.privacyMode })
            loadHistory(); onNotice(r.note)
          }}>
            {history?.privacyMode ? 'Turn off privacy mode' : 'Privacy mode'}
          </button>
        </div>
      </section>
    </>
  )
}

// ---------------------------------------------------------------------------
// Geofences
// ---------------------------------------------------------------------------
export function FencePanel({ workspaceId, fences, focus, drawRadius,
                             setDrawRadius, onRefresh, onPick, onNotice,
                             hovered, onHover }: {
  workspaceId: string; fences: Geofence[]; focus: Point | null
  drawRadius: number; setDrawRadius: (n: number) => void
  onRefresh: () => void; onPick: (p: Point) => void
  onNotice: (s: string) => void
  hovered: string | null; onHover: (id: string | null) => void
}) {
  const [label, setLabel] = useState('')
  const [trigger, setTrigger] = useState('both')
  const [events, setEvents] = useState<Awaited<ReturnType<typeof geo.geofenceEvents>> | null>(null)

  useEffect(() => {
    if (!workspaceId) return
    geo.geofenceEvents(workspaceId, 12).then(setEvents).catch(() => {})
  }, [workspaceId, fences])

  const create = async () => {
    if (!label.trim() || !focus || !workspaceId) return
    await geo.createGeofence({ workspace: workspaceId, label: label.trim(),
                               lat: focus.lat, lon: focus.lon,
                               radiusM: drawRadius, trigger })
    setLabel('')
    onRefresh()
    onNotice(`Watching "${label.trim()}".`)
  }

  return (
    <>
      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Watch an area</h3>
        {!focus
          ? <Empty>Click the map to place the centre — the ring previews live.</Empty>
          : <p className="omx-gx-sub dim">
              The dashed ring on the map is this fence. Drag the radius to size it.
            </p>}
        <input className="omx-gx-input wide" value={label} disabled={!focus}
               placeholder="College gate" onChange={(e) => setLabel(e.target.value)} />
        <div className="omx-gx-row">
          <label className="omx-gx-lbl wide">
            Radius
            {/* A slider, not a select: the ring redraws as it moves, so the
                control and the thing it controls are the same gesture. 50m is
                the backend's floor — below consumer GPS error a fence flaps
                between states while the user stands still. */}
            <input type="range" className="omx-gx-range" min={50} max={5000}
                   step={50} value={drawRadius}
                   onChange={(e) => setDrawRadius(Number(e.target.value))} />
            <span className="omx-gx-sub">{humanDistance(drawRadius)}</span>
          </label>
        </div>
        <div className="omx-gx-row">
          <label className="omx-gx-lbl">
            Fires on
            <select className="omx-gx-input" value={trigger}
                    onChange={(e) => setTrigger(e.target.value)}>
              <option value="enter">Arriving</option>
              <option value="exit">Leaving</option>
              <option value="both">Both</option>
            </select>
          </label>
          <button className="omx-btn" onClick={create}
                  disabled={!focus || !label.trim()}>Create</button>
        </div>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Geofences</h3>
        {!fences.length && <Empty>None yet.</Empty>}
        <ul className="omx-gx-list">
          {fences.map((f) => (
            <li key={f.id} className="omx-gx-card" data-on={hovered === f.id}
                onMouseEnter={() => onHover(f.id)}
                onMouseLeave={() => onHover(null)}>
              <button className="omx-gx-item"
                      onClick={() => onPick({ lat: f.lat, lon: f.lon, label: f.label })}>
                <div className="omx-gx-card-head">
                  <strong>{f.label}</strong>
                  {f.inside && <span className="omx-gx-open">inside</span>}
                </div>
                <span className="omx-gx-sub">
                  {humanDistance(f.radiusM)} · fires on {f.trigger}
                  {f.active ? '' : ' · paused'}
                </span>
              </button>
              <div className="omx-gx-acts">
                <button className="omx-gx-mini" onClick={async () => {
                  await geo.toggleGeofence(workspaceId, f.id, !f.active); onRefresh()
                }}>{f.active ? 'Pause' : 'Resume'}</button>
                <button className="omx-gx-mini" onClick={async () => {
                  await geo.deleteGeofence(workspaceId, f.id); onRefresh()
                }}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Recent crossings</h3>
        {!events?.events.length && <Empty>No crossings recorded.</Empty>}
        <ul className="omx-gx-list">
          {(events?.events || []).map((e) => (
            <li key={e.id} className="omx-gx-evt">
              <strong>
                {e.transition === 'enter' ? 'Arrived at' : 'Left'} {e.label}
              </strong>
              <span className="omx-gx-sub">
                {new Date(e.createdAt).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
export function DataPanel({ config, onReload }: {
  config: GeoConfig | null; onReload: () => void
}) {
  if (!config) return <Empty>Configuration unavailable.</Empty>
  const totals = config.usage.totals
  const saved = totals.hits + totals.misses
  const pct = saved ? Math.round((totals.hits / saved) * 100) : 0
  return (
    <>
      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">API usage</h3>
        {/* The number the caching layer exists to produce. Rendering it makes
            the cost discipline auditable instead of asserted. */}
        <Gauge label="Requests answered from cache" value={pct} max={100}
               band={`${totals.callsAvoided} calls avoided`}
               tone={pct > 50 ? 'ok' : pct > 20 ? 'fair' : 'muted'} suffix="%" />
        <dl className="omx-gx-facts">
          <Fact k="Provider calls" v={String(totals.calls)} />
          <Fact k="Cache hits" v={String(totals.hits)} />
          <Fact k="Errors" v={String(totals.errors)}
                tone={totals.errors ? 'warn' : undefined} />
          <Fact k="Cached entries" v={String(config.usage.memoryEntries)} />
        </dl>
        <div className="omx-gx-row">
          <button className="omx-btn" onClick={onReload}>Refresh</button>
          <button className="omx-btn" onClick={async () => {
            await geo.clearCache(); onReload()
          }}>Clear cache</button>
        </div>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Capabilities</h3>
        <p className="omx-gx-sub dim">
          Which providers answer each question, in fallback order.
        </p>
        <dl className="omx-gx-facts">
          {Object.entries(config.capabilities).map(([k, v]) => (
            <Fact key={k} k={k} v={v.length ? v.join(' → ') : 'none configured'}
                  tone={v.length ? undefined : 'warn'} />
          ))}
        </dl>
      </section>

      <section className="omx-gx-sec">
        <h3 className="omx-gx-h">Providers</h3>
        <ul className="omx-gx-list">
          {Object.entries(config.providers).map(([name, p]) => (
            <li key={name} className="omx-gx-prov">
              <strong>{name}</strong>
              <span className="omx-gx-fresh"
                    data-tone={p.circuitOpen ? 'bad' : p.available ? 'ok' : 'muted'}>
                {p.circuitOpen ? 'circuit open'
                  : p.available ? 'available' : 'not configured'}
              </span>
              <span className="omx-gx-sub dim">
                {p.usage?.calls ?? 0} calls · {p.usage?.hits ?? 0} hits
                {p.failures ? ` · ${p.failures} failures` : ''}
              </span>
            </li>
          ))}
        </ul>
        <p className="omx-gx-sub dim">
          Keys are read from the environment and never leave the server. Add
          GOOGLE_MAPS_API_KEY or GRAPHHOPPER_API_KEY to .env to extend these.
        </p>
      </section>
    </>
  )
}
