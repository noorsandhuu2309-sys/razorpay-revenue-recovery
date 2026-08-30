// The graph's render model.
//
// Everything the canvas needs to draw a node or an edge is derived HERE, once,
// from the GraphPayload the store already holds — never inside a render loop
// and never inside a React component. Two reasons, and both were visible in the
// view this replaces:
//
//   * the overlay ran `nodes.findIndex()` per selected node per frame, and the
//     picker called `spaceToScreenPosition` once per node per mousemove. Both
//     are O(N) work on the hot path, and both vanish once positions and lookups
//     are indexed ahead of time.
//   * the visual rules (what shape is a country, how thick is a strong link,
//     which links are inferred) were scattered across two renderers that
//     disagreed with each other. Orbit resolved edge direction correctly and
//     Network did not, so the same relationship pointed two different ways
//     depending on which mode you were in.
//
// Nothing here fetches. Nothing here mutates the store. Given the same payload
// it returns the same model, which is what makes the canvas cheap to redraw.

import type { GraphPayload, OmxEdge, OmxObject, Provenance } from './types'

// ---------------------------------------------------------------------------
// Semantic classes
// ---------------------------------------------------------------------------

/** What KIND of connection this is, for colour. Four classes, deliberately —
 *  the moment colour carries more than a handful of meanings it stops being
 *  readable and becomes decoration. */
export type RelClass = 'strategic' | 'economic' | 'adversarial' | 'informational'

/** How much the graph is willing to assert about an edge, for line style.
 *  Style rather than colour, because colour is already spent on class and a
 *  reader cannot decode two colour scales at once. */
export type Certainty = 'confirmed' | 'inferred' | 'weak'

/** Relation key → class. Keys come from `/api/ontology`; anything unlisted
 *  falls to `informational`, which is the honest default — an unclassified
 *  relation is exactly a link we can draw but cannot characterise. */
const REL_CLASS: Record<string, RelClass> = {
  // strategic — alignment, membership, influence, the geopolitical spine
  allied_with: 'strategic',
  member_of: 'strategic',
  leads: 'strategic',
  negotiating: 'strategic',
  supports: 'strategic',
  involved_in: 'strategic',
  affected_by: 'strategic',
  affects: 'strategic',
  assigned_to: 'strategic',
  // adversarial — coercion and opposition. `sanctions` lives here rather than
  // under strategic because a sanction is an act against, and reading it as
  // neutral alignment is the single most misleading thing this map could do.
  in_conflict: 'adversarial',
  accuses: 'adversarial',
  sanctions: 'adversarial',
  competes_with: 'adversarial',
  blocks: 'adversarial',
  // economic — flows of goods, money, dependency
  trades_with: 'economic',
  supplies: 'economic',
  invests_in: 'economic',
  produces: 'economic',
  depends_on: 'economic',
  customer_of: 'economic',
  acquired: 'economic',
  subsidiary_of: 'economic',
  imports: 'economic',
  partners_with: 'economic',
  uses: 'economic',
  implements: 'economic',
  // informational — structure, citation, evidence, co-occurrence
  located_in: 'informational',
  co_mentioned: 'informational',
  related_to: 'informational',
  about: 'informational',
  cites: 'informational',
  derived_from: 'informational',
  supported_by: 'informational',
  contradicted_by: 'informational',
  verifies: 'informational',
  contains: 'informational',
  calls: 'informational',
}

export const relClass = (relation: string): RelClass =>
  REL_CLASS[relation] ?? 'informational'

/** Relations that assert nothing beyond "these appeared together". They are
 *  the bulk of a news-derived graph and the reason an unfiltered view looks
 *  like a hairball, so they are always drawn as the weakest thing on screen. */
const INCIDENTAL = new Set(['co_mentioned', 'related_to'])

/** Line style for an edge.
 *
 *  Provenance is the primary signal, observation count the tie-breaker: a
 *  source-backed edge seen once is a single report, and drawing it as solid as
 *  something corroborated twice overstates it. */
export function certaintyOf(e: Pick<OmxEdge, 'relation' | 'provenance' | 'count' | 'weight'>): Certainty {
  if (INCIDENTAL.has(e.relation)) return 'weak'
  if (e.provenance === 'ai_inferred') return 'inferred'
  if (e.provenance === 'source_backed' && (e.count ?? 1) < 2) return 'inferred'
  if ((e.weight ?? 0) < 0.4) return 'weak'
  return 'confirmed'
}

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

const hexToRgb = (hex: string): [number, number, number] => {
  let h = (hex || '#d3ad55').replace('#', '')
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
  const n = parseInt(h, 16)
  if (!Number.isFinite(n)) return [211, 173, 85]
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const rgbToHex = (r: number, g: number, b: number): string =>
  '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v)))
    .toString(16).padStart(2, '0')).join('')

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h = 0
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return [h, s, l]
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) return [l * 255, l * 255, l * 255]
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const f = (t: number): number => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255]
}

/** Pull an ontology colour into OMNIX's range without changing what it means.
 *
 *  The ontology's palette is built for a legend, not for a dark canvas: at
 *  `#4ade80` / `#c084fc` / `#f0abfc` a graph of six families reads as a toy.
 *  Clamping saturation and lightness keeps every hue distinguishable — cyan is
 *  still cyan, gold is still gold, so type identity survives — while dropping
 *  the neon. It is applied on the CANVAS ONLY: the Map and TERRA views keep the
 *  colours they have always had, because those were not what the user asked to
 *  change.
 *
 *  Memoised because it runs per node per rebuild and the input domain is the
 *  ~18 family colours. */
const toneCache = new Map<string, string>()
export function restrain(hex: string): string {
  const hit = toneCache.get(hex)
  if (hit) return hit
  const [r, g, b] = hexToRgb(hex)
  const [h, s, l] = rgbToHsl(r, g, b)
  // Saturation is the neon channel and gets the harder clamp; lightness is
  // kept in a narrow band so no family shouts and none disappears into the
  // #060606 ground. Tuned against the live Space, not in the abstract: at 0.46
  // the event pink still read as a highlighter against forty gold diamonds.
  const out = rgbToHex(...hslToRgb(h, Math.min(s, 0.4), Math.max(0.42, Math.min(l, 0.62))))
  toneCache.set(hex, out)
  return out
}

/** Edge colour by semantic class. Restrained on purpose: three of the four are
 *  close to the interface's own line colour, and only `adversarial` is allowed
 *  to read as a warning — if every class shouted, none would. */
export const CLASS_COLOR: Record<RelClass, string> = {
  // gold, the house colour, for the geopolitical spine
  strategic: '#b08d43',
  // steel cyan for flows of goods and money
  economic: '#4a8fa8',
  // the only colour on the canvas allowed to read as alarm, and muted even so
  adversarial: '#a8555f',
  // barely above the interface's own line colour: co-mentions are the bulk of
  // a news-derived graph and must not compete with anything that means more
  informational: '#6a6559',
}

export const CLASS_LABEL: Record<RelClass, string> = {
  strategic: 'Strategic',
  economic: 'Economic',
  adversarial: 'Adversarial',
  informational: 'Informational',
}

// ---------------------------------------------------------------------------
// Shape
// ---------------------------------------------------------------------------

/** cosmos.gl's PointShape enum, read out of the vendored build. Kept as a
 *  local constant rather than imported because cosmos is loaded at runtime from
 *  /static and there is no module to import from. */
export const SHAPE = {
  circle: 0, square: 1, triangle: 2, diamond: 3,
  pentagon: 4, hexagon: 5, star: 6, cross: 7, none: 8,
} as const
export type ShapeName = keyof typeof SHAPE

/** Type → shape, falling back to family → shape.
 *
 *  Keyed on type first because the brief distinguishes Company from
 *  Organization and Claim from Source, and those pairs share a family. */
const TYPE_SHAPE: Record<string, ShapeName> = {
  country: 'diamond',
  organization: 'circle',
  government: 'circle',
  company: 'square',
  person: 'circle',
  event: 'hexagon',
  conflict: 'triangle',
  location: 'hexagon',
  infrastructure: 'hexagon',
  claim: 'diamond',
  source: 'square',
  hypothesis: 'diamond',
  recommendation: 'diamond',
}

const FAMILY_SHAPE: Record<string, ShapeName> = {
  country: 'diamond',
  person: 'circle',
  org: 'circle',
  company: 'square',
  event: 'hexagon',
  conflict: 'triangle',
  place: 'hexagon',
  story: 'square',
  economic: 'square',
  tech: 'pentagon',
  product: 'square',
  work: 'circle',
  code: 'pentagon',
  doc: 'square',
  evidence: 'diamond',
  risk: 'triangle',
  dataset: 'square',
  thing: 'circle',
}

export const shapeOf = (o: Pick<OmxObject, 'type' | 'family'>): ShapeName =>
  TYPE_SHAPE[o.type] ?? FAMILY_SHAPE[o.family] ?? 'circle'

// ---------------------------------------------------------------------------
// The model
// ---------------------------------------------------------------------------

export interface RenderNode {
  /** Index into the arrays cosmos.gl is given. Position i in every buffer. */
  i: number
  o: OmxObject
  id: string
  /** Links touching this node, within the loaded subgraph. */
  degree: number
  /** 0..1 bounded importance — degree, salience and ontology weight combined. */
  importance: number
  /** Screen radius in CSS pixels, already bounded. */
  size: number
  shape: ShapeName
  /** Restrained ontology colour. */
  color: string
  /** People and countries carry an outer ring in the ontology; kept because it
   *  is a second, non-colour channel for "this is a principal actor". */
  ring: boolean
}

/** What an edge MEANS, independent of where it is drawn.
 *
 *  Split out from `RenderEdge` so the relationship inspector can describe an
 *  edge without owning buffer indices — it is opened from a key and needs the
 *  semantics, not the geometry. */
export interface EdgeMeta {
  /** Stable key even when the backend leaves `id` null, which it does for
   *  edges synthesised during traversal. */
  key: string
  e: OmxEdge
  /** True when the relation type is symmetric and no arrow should be drawn. */
  symmetric: boolean
  relClass: RelClass
  certainty: Certainty
  /** Display label with the "(inbound)" bookkeeping suffix stripped. */
  label: string
  /** True when the stored direction runs target → source. */
  inbound: boolean
  /** 0..1, bounded. Drives line width. */
  strength: number
  color: string
}

export interface RenderEdge extends EdgeMeta {
  /** Buffer indices, source-first in TRAVERSAL order — that is what cosmos
   *  wants, and it is not the semantic direction. */
  a: number
  b: number
  /** Semantic direction, resolved from the label convention. `from` → `to` is
   *  what the relationship actually asserts. */
  from: number
  to: number
}

/** Describe one edge. The single place the visual and semantic rules for a
 *  relationship are applied, so the canvas and the inspector can never
 *  disagree about what a line is claiming. */
export function describeEdge(e: OmxEdge, symmetricRelations: Set<string>): EdgeMeta {
  const { label, inbound } = readLabel(e)
  const cls = relClass(e.relation)
  return {
    key: e.id ?? `${e.source}|${e.target}|${e.relation}`,
    e,
    symmetric: symmetricRelations.has(e.relation),
    relClass: cls,
    certainty: certaintyOf(e),
    label,
    inbound,
    // log1p keeps one heavily-attested link from being ten times the width of
    // everything else, the same squash the backend uses for salience.
    strength: Math.min(1, Math.log1p(Math.max(0, e.weight || 0)) / Math.log1p(8)),
    color: CLASS_COLOR[cls],
  }
}

export interface GraphModel {
  nodes: RenderNode[]
  edges: RenderEdge[]
  /** id → RenderNode. Replaces the `findIndex` that ran inside the frame loop. */
  byId: Map<string, RenderNode>
  /** Nodes by importance, descending. Sorted once per rebuild rather than once
   *  per frame — the label pass walks this in order and stops at its budget,
   *  so the labels that survive are always the ones worth keeping. */
  labelOrder: RenderNode[]
  /** id → neighbouring ids, for focus tiers and hover isolation. */
  adjacency: Map<string, Set<string>>
  /** Distinct families present, commonest first — the legend draws from this
   *  rather than from the full ontology, because a legend listing families with
   *  no members on screen teaches the reader nothing. */
  legend: { family: string; label: string; color: string; shape: ShapeName; count: number }[]
  /** Relation classes actually present, for the edge key. */
  classes: { cls: RelClass; count: number }[]
}

/** Strip the traversal bookkeeping the graph layer appends to inbound labels. */
const INBOUND = '(inbound)'
export function readLabel(e: Pick<OmxEdge, 'label' | 'relation'>): { label: string; inbound: boolean } {
  const raw = e.label || (e.relation || '').replace(/_/g, ' ')
  const inbound = raw.endsWith(INBOUND)
  return {
    label: inbound ? raw.slice(0, -INBOUND.length).trim() : raw,
    inbound,
  }
}

/** Build everything the canvas needs.
 *
 *  `symmetricRelations` comes from the ontology and cannot be inferred from the
 *  payload: edges are deduplicated on an unordered {pair, relation} key, so a
 *  reciprocal edge is never present to detect. Passing an empty set produces
 *  one-way arrows on mutual relations, which is why the caller must wait for
 *  the ontology rather than defaulting it away. */
export function buildModel(
  graph: GraphPayload,
  symmetricRelations: Set<string>,
): GraphModel {
  const nodes: RenderNode[] = []
  const byId = new Map<string, RenderNode>()

  graph.nodes.forEach((o, i) => {
    const n: RenderNode = {
      i, o, id: o.id,
      degree: 0,
      importance: 0,
      size: 0,
      shape: shapeOf(o),
      color: restrain(o.color),
      ring: !!o.ring,
    }
    nodes.push(n)
    byId.set(o.id, n)
  })

  // Degree from the loaded subgraph. The API reports `degree: null` on every
  // subgraph node, so sizing by it silently sized everything the same — this is
  // the measurement that makes hierarchy possible at all.
  const adjacency = new Map<string, Set<string>>()
  const edges: RenderEdge[] = []
  const seenKeys = new Set<string>()

  for (const e of graph.edges) {
    const a = byId.get(e.source)
    const b = byId.get(e.target)
    // An edge pointing at a node the cap excluded would index past the end of
    // the cosmos buffers. The backend already filters these; belt and braces.
    if (!a || !b) continue

    const meta = describeEdge(e, symmetricRelations)
    if (seenKeys.has(meta.key)) continue
    seenKeys.add(meta.key)

    a.degree++
    b.degree++
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set())
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set())
    adjacency.get(e.source)!.add(e.target)
    adjacency.get(e.target)!.add(e.source)

    edges.push({
      ...meta,
      a: a.i,
      b: b.i,
      // Direction is the label's business, not the traversal's. Reading it off
      // source/target produces arrows that contradict their own labels.
      from: meta.inbound ? b.i : a.i,
      to: meta.inbound ? a.i : b.i,
    })
  }

  // Importance and size. Bounded on BOTH ends: the brief asks for hierarchy,
  // not for one hub that dwarfs the map. A 4px floor keeps the smallest node
  // clickable; a 14px ceiling keeps the largest from swallowing its neighbours.
  const maxDeg = Math.max(1, ...nodes.map((n) => n.degree))
  for (const n of nodes) {
    const deg = Math.log1p(n.degree) / Math.log1p(maxDeg)
    const importance =
      deg * 0.5
      + (n.o.salience || 0) * 0.28
      + Math.min(1, ((n.o.vweight || 1) - 0.85) / 0.6) * 0.12
      + (n.o.root ? 0.1 : 0)
    n.importance = Math.max(0, Math.min(1, importance))
    n.size = 4.2 + n.importance * 9.8
  }

  const famCount = new Map<string, { family: string; label: string; color: string; shape: ShapeName; count: number }>()
  for (const n of nodes) {
    const hit = famCount.get(n.o.family)
    if (hit) { hit.count++; continue }
    famCount.set(n.o.family, {
      family: n.o.family, label: n.o.familyLabel,
      color: n.color, shape: n.shape, count: 1,
    })
  }

  const clsCount = new Map<RelClass, number>()
  for (const e of edges) clsCount.set(e.relClass, (clsCount.get(e.relClass) ?? 0) + 1)

  return {
    nodes,
    edges,
    byId,
    labelOrder: [...nodes].sort((a, b) => b.importance - a.importance),
    adjacency,
    legend: [...famCount.values()].sort((a, b) => b.count - a.count),
    classes: [...clsCount.entries()]
      .map(([cls, count]) => ({ cls, count }))
      .sort((a, b) => b.count - a.count),
  }
}

// ---------------------------------------------------------------------------
// Focus tiers
// ---------------------------------------------------------------------------

/** How prominent a node is while Focus Mode is on.
 *  0 = the anchor and its direct neighbours, 1 = second degree, 2 = the rest. */
export type FocusTier = 0 | 1 | 2

export const FOCUS_ALPHA: Record<FocusTier, number> = { 0: 1, 1: 0.5, 2: 0.12 }
export const FOCUS_EDGE_ALPHA: Record<FocusTier, number> = { 0: 0.75, 1: 0.3, 2: 0.06 }

/** Tier every node relative to a set of anchors.
 *
 *  Breadth-first over the adjacency built above rather than repeated scans of
 *  `graph.edges` — the old hover isolation walked every edge on every frame to
 *  answer the same question. */
export function focusTiers(
  model: GraphModel, anchors: string[],
): Map<string, FocusTier> {
  const tier = new Map<string, FocusTier>()
  if (!anchors.length) return tier
  for (const id of anchors) {
    if (!model.byId.has(id)) continue
    tier.set(id, 0)
  }
  for (const id of [...tier.keys()]) {
    for (const nb of model.adjacency.get(id) ?? []) {
      if (!tier.has(nb)) tier.set(nb, 0)
    }
  }
  const firstRing = [...tier.keys()]
  for (const id of firstRing) {
    for (const nb of model.adjacency.get(id) ?? []) {
      if (!tier.has(nb)) tier.set(nb, 1)
    }
  }
  for (const n of model.nodes) if (!tier.has(n.id)) tier.set(n.id, 2)
  return tier
}

// ---------------------------------------------------------------------------
// Formatting helpers shared by the inspectors
// ---------------------------------------------------------------------------

/** Confidence is nullable across this schema and IS null on every relationship
 *  in the current dataset. Rendering that as 0% would claim the model was
 *  certain it was wrong; rendering it as a plausible number would be a lie. */
export const confidenceText = (c: number | null | undefined): string =>
  c == null ? 'not measured' : `${Math.round(c * 100)}%`

export const strengthText = (s: number): string =>
  s >= 0.66 ? 'High' : s >= 0.33 ? 'Moderate' : 'Low'

export const CERTAINTY_LABEL: Record<Certainty, string> = {
  confirmed: 'Corroborated',
  inferred: 'Inferred',
  weak: 'Incidental',
}

export const PROV_LABEL: Record<Provenance, string> = {
  user_created: 'You created this',
  verified: 'Verified',
  source_backed: 'Source-backed',
  ai_inferred: 'AI-inferred',
}

export const shortDate = (iso: string): string => {
  const t = new Date(iso)
  if (!Number.isFinite(t.getTime())) return '—'
  return t.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}
