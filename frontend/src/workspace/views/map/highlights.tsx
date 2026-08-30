// Highlights — the landing panel of the World Map.
//
// This is the first surface a user sees when OMNIX opens, so it has two jobs:
// say hello, and say what is happening. Both are answered from
// `/api/terra/events`, TERRA's ranked cluster feed — the same data the World
// News view reads, presented as a ranked column rather than a page of cards.
//
// Three decisions worth keeping:
//
//   * **The panel does not fetch.** MapView owns the highlights request,
//     because the same array drives the map pins, and two fetches would let
//     the pins and the list disagree about what is happening. The panel is
//     presentational; hovering a row and hovering its pin are the same state.
//
//   * **Severity is the only colour.** Every row carries a 2px rail tinted by
//     `severity`, so the feed can be ranked at a glance without reading it.
//     Nothing else in the row is coloured, or the rail stops meaning anything.
//
//   * **The greeting states what it knows and nothing more.** OMNIX has no
//     account system yet, so there is no name to use — greeting a user as
//     "Karthik" from a hostname or a guess is exactly the kind of confident
//     fabrication the rest of this product exists to prevent. Time of day and
//     the active Space are both true.

import { useMemo } from 'react'

/** A ranked cluster from TERRA's ingest.
 *
 *  Only the fields this panel and the pins use are typed. The endpoint returns
 *  ~22 per event; typing all of them here would make this file the second
 *  definition of a shape that belongs to the backend. */
export interface Highlight {
  id: string
  title: string
  url?: string
  size: number
  sources?: Record<string, number>
  source_count?: number
  countries?: string[]
  domains?: string[]
  keywords?: string[]
  severity?: number
  sentiment?: number
  score?: number
  when?: string
  velocity?: number
  status?: { state?: string; label?: string }
}

export interface HighlightsPayload {
  events: Highlight[]
  total: number
}

/** Severity bands, as CSS variable references rather than literal colours.
 *
 *  `--omx-sev` is consumed only by this view's stylesheet, never read back by
 *  a canvas renderer through getComputedStyle, so it is safe for it to hold a
 *  `var()` reference — and that is what keeps the pins correct across all ten
 *  theme x accent combinations instead of freezing one palette's hex. */
export function severityVar(severity = 0): string {
  if (severity >= 0.72) return 'var(--omx-neg)'
  if (severity >= 0.5) return 'var(--omx-warn)'
  if (severity >= 0.3) return 'var(--omx-info)'
  return 'var(--omx-text-faint)'
}

export function severityLabel(severity = 0): string {
  if (severity >= 0.72) return 'critical'
  if (severity >= 0.5) return 'elevated'
  if (severity >= 0.3) return 'notable'
  return 'routine'
}

/** Time-of-day greeting, from the viewer's own clock. */
export function greetingFor(d = new Date()): string {
  const h = d.getHours()
  if (h < 5) return 'Still up'
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  if (h < 22) return 'Good evening'
  return 'Good evening'
}

const DATE_FMT: Intl.DateTimeFormatOptions = {
  weekday: 'long', day: 'numeric', month: 'long',
}

// ---------------------------------------------------------------------------
export function HighlightsPanel({
  events, total, loading, error, domain, domains, spaceName,
  hovered, expanded, onDomain, onHover, onExpand, onPick, onRefresh, refreshing,
}: {
  events: Highlight[]
  total: number
  loading: boolean
  error: string
  domain: string
  domains: string[]
  spaceName: string
  hovered: string | null
  expanded: string | null
  onDomain: (d: string) => void
  onHover: (id: string | null) => void
  onExpand: (id: string | null) => void
  /** Fly the map to a story's country. */
  onPick: (h: Highlight) => void
  onRefresh: () => void
  refreshing: boolean
}) {
  const today = useMemo(
    () => new Date().toLocaleDateString(undefined, DATE_FMT), [])

  // The severity of the top story is what the greeting summarises. Averaging
  // the whole feed would smear a single critical story into "routine", which
  // is the one summary that must never be wrong.
  const lead = events[0]
  const critical = events.filter((e) => (e.severity ?? 0) >= 0.72).length

  return (
    <div className="omx-gx-hi">
      <header className="omx-gx-greet">
        <h2 className="omx-gx-greet-hello">{greetingFor()}.</h2>
        <p className="omx-gx-greet-sub">
          {loading && !events.length
            ? 'Reading the world feed…'
            : error
              ? 'The world feed is unreachable — the map still works.'
              : events.length === 0
                ? 'Nothing clustered in the feed yet.'
                : <>
                    {total} stories are moving in <strong>{spaceName}</strong>
                    {critical > 0 && <>, {critical} of them critical</>}.
                    {lead && <> The largest is out of{' '}
                      {lead.countries?.length
                        ? countryName(lead.countries[0]) : 'no fixed location'}.</>}
                  </>}
        </p>
        <div className="omx-gx-greet-date">{today}</div>
      </header>

      <div className="omx-gx-metaline">
        <span className="omx-gx-live" data-live={!error && events.length > 0}>
          <i /> {error ? 'offline' : 'live'}
        </span>
        <button className="omx-btn" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      <div className="omx-gx-hi-filters">
        <button className={`omx-chip ${!domain ? 'on' : ''}`}
                onClick={() => onDomain('')}>all</button>
        {domains.map((d) => (
          <button key={d} className={`omx-chip ${domain === d ? 'on' : ''}`}
                  onClick={() => onDomain(d)}>{d}</button>
        ))}
      </div>

      {error && <p className="omx-gx-empty">{error}</p>}
      {!error && !events.length && !loading && (
        <p className="omx-gx-empty">
          No stories in this domain. TERRA clusters articles as it ingests them;
          try “all”, or refresh.
        </p>
      )}

      <div className="omx-gx-hi-list">
        {events.map((h, i) => {
          const sev = h.severity ?? 0
          const open = expanded === h.id
          const srcs = h.source_count ?? Object.keys(h.sources || {}).length
          return (
            <div key={h.id}>
              <button
                className="omx-gx-story"
                style={{ ['--omx-sev' as string]: severityVar(sev) }}
                data-hover={hovered === h.id}
                data-on={open}
                onMouseEnter={() => onHover(h.id)}
                onMouseLeave={() => onHover(null)}
                onClick={() => { onExpand(open ? null : h.id); onPick(h) }}
              >
                <span className="omx-gx-story-rank">{i + 1}</span>
                <span className="omx-gx-story-body">
                  <span className="omx-gx-story-title">{h.title}</span>
                  <span className="omx-gx-story-meta">
                    <span className="sev">{severityLabel(sev)}</span>
                    {h.when && <span>{h.when}</span>}
                    <span>{srcs} source{srcs === 1 ? '' : 's'}</span>
                    {(h.countries || []).slice(0, 3).map((c) => (
                      <span className="omx-gx-flag" key={c}>{c}</span>
                    ))}
                    {(h.domains || []).slice(0, 2).map((d) => (
                      <span key={d}>{d}</span>
                    ))}
                  </span>
                </span>
              </button>

              {open && (
                <div className="omx-gx-story-detail">
                  {h.keywords?.length ? (
                    <p className="omx-gx-sub">{h.keywords.slice(0, 8).join(' · ')}</p>
                  ) : null}
                  {h.status?.label && (
                    <p className="omx-gx-sub dim">{h.status.label}</p>
                  )}
                  <div className="omx-gx-srcs">
                    {Object.keys(h.sources || {}).slice(0, 8).map((s) => (
                      <span className="omx-gx-tag" key={s}>{s}</span>
                    ))}
                  </div>
                  {h.url && (
                    <p style={{ marginTop: 8 }}>
                      {/* noreferrer as well as noopener: these are third-party
                          news domains and the referrer is the user's own
                          localhost workspace. */}
                      <a href={h.url} target="_blank" rel="noopener noreferrer">
                        Open the lead article ↗
                      </a>
                    </p>
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

/** ISO-3166 alpha-2 to a display name, via the platform's own table.
 *
 *  `Intl.DisplayNames` means no gazetteer ships for this — and it localises
 *  for free. It throws on a malformed code rather than returning undefined,
 *  hence the guard. */
let names: Intl.DisplayNames | null = null
export function countryName(iso: string): string {
  if (!iso) return 'an unknown country'
  try {
    names ??= new Intl.DisplayNames(undefined, { type: 'region' })
    return names.of(iso.toUpperCase()) || iso
  } catch {
    return iso
  }
}
