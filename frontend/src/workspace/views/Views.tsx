// The non-graph representations of the same workspace data. Every one of them
// selects through the shared store, so clicking a row here moves the graph, the
// inspector and the Context Lens together.

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useWorkspace } from '../store/workspace'
import { useVirtualRows } from '../lib/useVirtualRows'
import { ProvDot } from '../components/Provenance'
import {
  SkeletonCards, SkeletonSources, SkeletonTable, SkeletonTimeline,
} from '../components/Skeleton'
import {
  IconBrief, IconSortAsc, IconSortDesc, IconTable,
} from '../components/Icons'
import type { Brief, OmxClaim, OmxEvent, OmxObject, OmxSource } from '../lib/types'

/** Height of one `.omx-table` row, in px. Mirrors the explicit `height` in
 *  workspace.css — the windowing maths in `useVirtualRows` is only correct
 *  while these two agree, so they change together. */
const TABLE_ROW_H = 36

/** Hostname for a source with no publisher recorded. `new URL` throws on a
 *  relative or malformed href, and a source library must not blank out because
 *  one row has a bad URL. */
function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url || 'unknown' }
}

/** `glyph` takes a node rather than a character so empty states draw from the
 *  same stroke-only icon set as the rail, instead of whichever face the
 *  machine happens to have for `◈`. */
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

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------
export function TimelineView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const selected = useWorkspace((s) => s.selected)
  const select = useWorkspace((s) => s.select)
  const ctxObjects = useWorkspace((s) => s.contextObjects)
  const [events, setEvents] = useState<OmxEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [onlySelected, setOnlySelected] = useState(false)

  useEffect(() => {
    if (!ws) return
    let live = true
    // `live` discards a reply that lost the race. Switching Space starts a new
    // request without cancelling the old one, and the slower reply lands last:
    // without this the table fills with the Space the user just left while the
    // header names the one they chose.
    setLoading(true)
    api.timeline(ws, {
      objects: onlySelected ? selected.join(',') : undefined,
      limit: 300,
    })
      .then((r) => { if (live) setEvents(r.events) })
      .catch(() => { if (live) setEvents([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws, onlySelected, selected])

  if (loading) return <div className="omx-scroll"><SkeletonTimeline /></div>
  if (!events.length) {
    return <Empty
      glyph="◷"
      title="No events yet"
      body="Events appear when research finds dated developments, or when a tracked object changes."
    />
  }

  const groups = new Map<string, OmxEvent[]>()
  for (const e of events) {
    const day = new Date(e.occurredAt).toLocaleDateString(undefined,
      { year: 'numeric', month: 'short', day: 'numeric' })
    if (!groups.has(day)) groups.set(day, [])
    groups.get(day)!.push(e)
  }

  return (
    <div className="omx-scroll">
      <div style={{ display: 'flex', gap: 7, marginBottom: 16 }}>
        <button
          className={`omx-btn ${onlySelected ? 'on' : ''}`}
          onClick={() => setOnlySelected((v) => !v)}
          disabled={!selected.length}
        >Selected only</button>
        <span className="omx-label" style={{ alignSelf: 'center' }}>
          {events.length} events
          {/* The request caps at 300. Saying so is the difference between a
              complete timeline and one that quietly stops at a date. */}
          {events.length >= 300 && ' · most recent 300'}
        </span>
      </div>

      {[...groups.entries()].map(([day, evs]) => (
        <div key={day} style={{ marginBottom: 22 }}>
          <div className="omx-label" style={{ marginBottom: 9 }}>{day}</div>
          <div className="omx-tl">
            {evs.map((e) => (
              <div className="omx-tl-item" data-r={e.relevance} key={e.id}>
                <div
                  style={{ fontSize: 13, cursor: e.objectId ? 'pointer' : 'default' }}
                  onClick={() => e.objectId && select(e.objectId, 'timeline')}
                >{e.title}</div>
                <div className="omx-label" style={{ marginTop: 3 }}>
                  {e.objectId && ctxObjects[e.objectId]?.name} · {e.relevance} relevance
                  {e.properties?.publisher ? ` · ${String(e.properties.publisher)}` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------
type SortKey =
  | 'name' | 'familyLabel' | 'provenance' | 'confidence' | 'degree' | 'tracked'

export function TableView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  const toggle = useWorkspace((s) => s.toggle)
  const selected = useWorkspace((s) => s.selected)
  const [rows, setRows] = useState<OmxObject[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortKey>('degree')
  const [dir, setDir] = useState<1 | -1>(-1)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    if (!ws) return
    let live = true
    // `live` discards a reply that lost the race. Switching Space starts a new
    // request without cancelling the old one, and the slower reply lands last:
    // without this the table fills with the Space the user just left while the
    // header names the one they chose.
    setLoading(true)
    api.objects(ws, { limit: 800 })
      .then((r) => { if (live) setRows(r.objects) })
      .catch(() => { if (live) setRows([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws])

  const view = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const out = rows.filter((r) =>
      !needle || r.name.toLowerCase().includes(needle) ||
      r.typeLabel.toLowerCase().includes(needle))
    out.sort((a, b) => {
      const av = a[sort], bv = b[sort]
      // `confidence` and `degree` are nullable and null means NOT MEASURED.
      // Coercing it to 0 would sort unmeasured objects in among genuinely
      // low-confidence ones, which is exactly the conflation the product
      // forbids. Unmeasured always sorts last, whichever way the column runs.
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir
      if (typeof av === 'boolean' && typeof bv === 'boolean') {
        return (Number(av) - Number(bv)) * dir
      }
      return String(av).localeCompare(String(bv)) * dir
    })
    return out
  }, [rows, sort, dir, filter])

  // Every row that survives the filter is rendered — windowed, not truncated.
  const { scrollRef, bodyRef, window: win, scrollToTop } =
    useVirtualRows(view.length, TABLE_ROW_H)

  // A re-sort or a new filter makes the row under the cursor meaningless, so
  // go back to the top rather than leaving the reader parked mid-list.
  useEffect(() => { scrollToTop() }, [sort, dir, filter, scrollToTop])

  if (loading) return <div className="omx-scroll"><SkeletonTable /></div>
  if (!rows.length) {
    return <Empty glyph={<IconTable size={34} />} title="No objects" body="Research something, or sync TERRA, to populate this workspace." />
  }

  const head = (k: SortKey, label: string, num = false) => (
    <th
      className={num ? 'num' : undefined}
      onClick={() => { setSort(k); setDir(sort === k ? (dir === 1 ? -1 : 1) : -1) }}
      aria-sort={sort === k ? (dir === 1 ? 'ascending' : 'descending') : undefined}
    >
      {label}
      {sort === k && (
        <span className="sortdir">
          {dir === 1 ? <IconSortAsc size={11} /> : <IconSortDesc size={11} />}
        </span>
      )}
    </th>
  )

  return (
    <div className="omx-scroll" ref={scrollRef}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 13 }}>
        <input
          className="omx-input"
          style={{ maxWidth: 280 }}
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter objects by name or type"
        />
        <span className="omx-label" style={{ alignSelf: 'center' }}>
          {view.length} of {rows.length}
          {/* The fetch caps at 800 objects. If it came back full, the workspace
              may hold more — say so rather than presenting 800 as the total. */}
          {rows.length >= 800 && ' loaded (first 800)'}
        </span>
      </div>
      <table className="omx-table">
        <thead>
          <tr>
            <th className="sel-col" aria-label="Selected" />
            {head('name', 'Name')}
            {head('familyLabel', 'Family')}
            {head('provenance', 'Provenance')}
            {head('confidence', 'Confidence', true)}
            {head('degree', 'Degree', true)}
            {head('tracked', 'Tracked', true)}
          </tr>
        </thead>
        <tbody ref={bodyRef}>
          {/* Spacer rows carry the height of the rows that are not drawn, so
              the scrollbar reflects the whole result set. `aria-hidden` keeps
              them out of the row count a screen reader announces. */}
          {win.padTop > 0 && (
            <tr aria-hidden="true" className="omx-table-pad" style={{ height: win.padTop }} />
          )}
          {view.slice(win.start, win.end).map((o) => (
            <tr
              key={o.id}
              className={selected.includes(o.id) ? 'sel' : ''}
              onClick={(e) => (e.ctrlKey || e.metaKey) ? toggle(o.id, 'table') : select(o.id, 'table')}
            >
              <td className="sel-col">
                <span className={`omx-sel-dot ${selected.includes(o.id) ? 'on' : ''}`} />
              </td>
              <td>
                <span style={{ color: o.color, marginRight: 9 }}>{o.glyph}</span>
                {o.name}
              </td>
              <td style={{ color: 'var(--omx-text-dim)' }} className="omx-mono">
                {o.familyLabel}
              </td>
              <td>
                <span className="omx-prov" data-p={o.provenance}>
                  <ProvDot p={o.provenance} />{o.provenanceLabel}
                </span>
              </td>
              {/* null confidence is NOT MEASURED and must never render as 0%. */}
              <td className="omx-mono num">
                {o.confidence == null
                  ? <span className="omx-null">not measured</span>
                  : `${Math.round(o.confidence * 100)}%`}
              </td>
              <td className="omx-mono num" style={{ color: 'var(--omx-text-dim)' }}>
                {o.degree ?? '—'}
              </td>
              <td className="num">
                {o.tracked
                  ? <span className="omx-pill on">tracked</span>
                  : <span className="omx-text-faint">—</span>}
              </td>
            </tr>
          ))}
          {win.padBottom > 0 && (
            <tr aria-hidden="true" className="omx-table-pad" style={{ height: win.padBottom }} />
          )}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Intelligence Brief
// ---------------------------------------------------------------------------
export function BriefView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  const [brief, setBrief] = useState<Brief | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!ws) return
    let live = true
    // `live` discards a reply that lost the race. Switching Space starts a new
    // request without cancelling the old one, and the slower reply lands last:
    // without this the table fills with the Space the user just left while the
    // header names the one they chose.
    setLoading(true)
    api.brief(ws)
      .then((b) => { if (live) setBrief(b) })
      .catch(() => { if (live) setBrief(null) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws])

  if (loading) return <div className="omx-scroll"><SkeletonCards /></div>
  if (!brief || brief.eventCount === 0) {
    return <Empty
      glyph={<IconBrief size={34} />}
      title="Nothing has changed yet"
      body="Track objects you care about and OMNIX will report meaningful developments here."
      action={
        <button className="omx-btn" onClick={async () => {
          await api.syncTracking(ws)
          setBrief(await api.brief(ws))
        }}>Check tracked objects</button>
      }
    />
  }

  const band = (label: string, entries: Brief['high'], tone: string) => entries.length > 0 && (
    <div style={{ marginBottom: 26 }}>
      <div className="omx-label" style={{ color: tone, marginBottom: 10 }}>{label}</div>
      <div style={{ display: 'grid', gap: 9 }}>
        {entries.map((e) => (
          <div className="omx-card click" key={e.object.id} onClick={() => select(e.object.id, 'graph')}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: e.object.color }}>{e.object.glyph}</span>
              <strong style={{ fontSize: 13 }}>{e.object.name}</strong>
              <span className="omx-label" style={{ marginLeft: 'auto' }}>
                {e.eventCount} update{e.eventCount > 1 ? 's' : ''}
              </span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--omx-text-dim)', marginTop: 6 }}>
              {e.latest.title}
            </div>
          </div>
        ))}
      </div>
    </div>
  )

  return (
    <div className="omx-scroll">
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: 17, fontWeight: 500 }}>
          {brief.trackedChanged} of {brief.trackedCount} tracked objects changed
        </h2>
        <div className="omx-label" style={{ marginTop: 5 }}>
          {brief.eventCount} events in the last {Math.round(brief.windowHours / 24)} days
        </div>
      </div>
      {band('High relevance', brief.high, 'var(--omx-gold)')}
      {band('Medium relevance', brief.medium, 'var(--omx-info)')}
      {band('Low relevance', brief.low, 'var(--omx-text-faint)')}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Claim Ledger
// ---------------------------------------------------------------------------
export function ClaimsView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const [claims, setClaims] = useState<OmxClaim[]>([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<OmxSource[]>([])

  useEffect(() => {
    if (!ws) return
    let live = true
    // `live` discards a reply that lost the race. Switching Space starts a new
    // request without cancelling the old one, and the slower reply lands last:
    // without this the table fills with the Space the user just left while the
    // header names the one they chose.
    setLoading(true)
    api.claims(ws)
      .then((r) => { if (live) setClaims(r.claims) })
      .catch(() => { if (live) setClaims([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws])

  const openClaim = async (id: string) => {
    if (open === id) { setOpen(null); return }
    setOpen(id)
    try {
      const r = await api.claimEvidence(ws, id)
      setEvidence(r.sources)
    } catch { setEvidence([]) }
  }

  if (loading) return <div className="omx-scroll"><SkeletonCards rows={6} /></div>
  if (!claims.length) {
    return <Empty
      glyph="❖"
      title="No claims yet"
      body="Run research and ORACLE will extract factual claims, verify each against its sources, and record the verdict here."
    />
  }

  return (
    <div className="omx-scroll">
      <div className="omx-label" style={{ marginBottom: 14 }}>
        {/* Corroborated and single-source are counted apart on purpose. A
            claim checked only against the source it was extracted from is
            nearly always going to pass, so collapsing the two into one
            "supported" number is what made this line meaningless. */}
        {claims.length} claims · {claims.filter((c) => c.verdict === 'verified').length} corroborated
        {' · '}{claims.filter((c) => c.verdict === 'single_source').length} single-source
      </div>
      <div className="omx-claims">
        {claims.map((c) => (
          <div
            className="omx-claim"
            key={c.id}
            data-verdict={c.verdict}
            onClick={() => void openClaim(c.id)}
          >
            <div className="head">
              <span className="omx-verdict" data-verdict={c.verdict}>{c.status}</span>
              {/* A claim's own trust level: a verdict reached from cited
                  sources is not the same object as one a model asserted, and
                  the header has to say which. */}
              <span className="omx-prov" data-p={c.supportedBy.length ? 'source_backed' : 'ai_inferred'}>
                {c.supportedBy.length ? 'Sourced' : 'AI'}
              </span>
              {/* Claim confidence is stored as a 0-100 integer (schema.Claim
                  .confidence is an Integer; object confidence is the 0-1 float
                  and the two must not be formatted with the same code). */}
              <span className="conf omx-mono">
                {c.confidence > 0
                  ? `confidence ${Math.round(c.confidence)}%`
                  : <span className="omx-null">confidence not measured</span>}
              </span>
            </div>
            <div className="text">{c.text}</div>
            <div className="foot">
              <span className="ev sup">▲ {c.supportedBy.length} supporting</span>
              <span className="ev con">▼ {c.contradictedBy.length} contradicting</span>
              {c.executionId && (
                <span className="exec omx-mono">{c.executionId.slice(0, 12)}</span>
              )}
            </div>
            {open === c.id && (
              <div className="evidence">
                <div className="omx-label" style={{ marginBottom: 7 }}>Evidence</div>
                {evidence.length === 0 && <span className="omx-label">No sources linked</span>}
                {evidence.map((s) => (
                  <a key={s.id} href={s.url} target="_blank" rel="noreferrer noopener"
                     onClick={(e) => e.stopPropagation()}
                     style={{ display: 'block', padding: '4px 0', color: 'var(--omx-info)', fontSize: 12 }}>
                    {s.title || s.url}
                    <span className="omx-label" style={{ marginLeft: 8 }}>{s.tierLabel}</span>
                  </a>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Source Library
// ---------------------------------------------------------------------------
export function SourcesView() {
  const ws = useWorkspace((s) => s.workspaceId)
  const [sources, setSources] = useState<OmxSource[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [tier, setTier] = useState<string | null>(null)

  useEffect(() => {
    if (!ws) return
    let live = true
    // `live` discards a reply that lost the race. Switching Space starts a new
    // request without cancelling the old one, and the slower reply lands last:
    // without this the table fills with the Space the user just left while the
    // header names the one they chose.
    setLoading(true)
    api.sources(ws)
      .then((r) => { if (live) setSources(r.sources) })
      .catch(() => { if (live) setSources([]) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [ws])

  // Tier is the one quality signal here that is measured rather than guessed
  // (oracle_evidence classifies it deterministically), so it is what the
  // facets are built from. Counts come from the data, never from a fixed list
  // of tiers — a tier with no sources should not offer a button that finds
  // nothing.
  const tiers = useMemo(() => {
    const seen = new Map<string, { label: string; n: number }>()
    for (const s of sources) {
      if (!s.tier) continue
      const e = seen.get(s.tier)
      if (e) e.n += 1
      else seen.set(s.tier, { label: s.tierLabel || s.tier, n: 1 })
    }
    return [...seen.entries()].sort((a, b) => b[1].n - a[1].n)
  }, [sources])

  const view = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return sources.filter((s) => {
      if (tier && s.tier !== tier) return false
      if (!needle) return true
      // Publisher is the field people actually search by ("reuters"), and it
      // falls back to the hostname when unrecorded — so match what the row
      // displays, not only what the payload stores.
      return (s.title || '').toLowerCase().includes(needle)
        || (s.publisher || '').toLowerCase().includes(needle)
        || hostOf(s.url).toLowerCase().includes(needle)
    })
  }, [sources, filter, tier])

  if (loading) return <div className="omx-scroll"><SkeletonSources /></div>
  if (!sources.length) {
    return <Empty glyph="▤" title="No sources yet" body="Sources are recorded whenever research retrieves a page or TERRA cites an article." />
  }

  return (
    <div className="omx-scroll">
      <div className="omx-src-bar">
        <input
          className="omx-input"
          style={{ maxWidth: 280 }}
          placeholder="Filter by title or publisher…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter sources by title or publisher"
        />
        <div className="facets" role="group" aria-label="Filter by source tier">
          {tiers.map(([key, t]) => (
            <button
              key={key}
              className={`omx-tier click ${tier === key ? 'on' : ''}`}
              data-tier={key}
              aria-pressed={tier === key}
              onClick={() => setTier((c) => (c === key ? null : key))}
            >{t.label} <span className="n omx-mono">{t.n}</span></button>
          ))}
        </div>
        <span className="omx-label count">
          {view.length === sources.length
            ? `${sources.length} sources`
            : `${view.length} of ${sources.length}`}
          {/* 1000 is the endpoint's hard ceiling. If it came back full there
              are probably more, and the count above is a floor, not a total. */}
          {sources.length >= 1000 && ' (first 1000)'}
        </span>
      </div>
      {view.length === 0 && (
        <p className="omx-empty-line">No source matches that filter.</p>
      )}
      <div className="omx-sources">
        {view.map((s) => (
          <a key={s.id} href={s.url} target="_blank" rel="noreferrer noopener" className="omx-source">
            {/* Tier is classified deterministically by oracle_evidence — it is
                the one quality signal here that is measured rather than
                guessed, so it leads the row. */}
            <div className="tier">
              <span className="omx-tier" data-tier={s.tier}>{s.tierLabel || s.tier}</span>
            </div>
            <div className="body">
              <div className="t">{s.title || s.url}</div>
              <div className="pub omx-mono">
                {s.publisher || hostOf(s.url)}{s.year ? ` · ${s.year}` : ''}
              </div>
              {(s.excerpt || s.snippet) && (
                <div className="ex">{s.excerpt || s.snippet}</div>
              )}
            </div>
            <div className="cred">
              <div className="omx-label">credibility</div>
              {/* Credibility is optional on the payload. When the backend did
                  not score it there is no bar — an empty track would read as
                  "scored zero". */}
              {/* Credibility is a 0-100 integer from oracle_evidence, clamped
                  to 5..99 — not a fraction. */}
              {typeof s.credibility === 'number' ? (
                <>
                  <div className="track">
                    <div className="fill" style={{ width: `${Math.max(0, Math.min(100, s.credibility))}%` }} />
                  </div>
                  <div className="v omx-mono">{Math.round(s.credibility)}%</div>
                </>
              ) : (
                <div className="omx-null">not scored</div>
              )}
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
