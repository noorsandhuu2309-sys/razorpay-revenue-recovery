// Orbit — the radial focus graph.
//
// A force layout answers "what does this whole neighbourhood look like". It is
// bad at the question an analyst actually asks, which is "what is attached to
// THIS, and how". In a force layout the relation types are scattered around the
// node in whatever order the simulation settled, so reading "who does the US
// sanction" means tracing individual edges and squinting at labels.
//
// Orbit answers that question directly: one object at the centre, its
// neighbours on a ring, and — the part that matters — the ring is *grouped by
// relation*, with each group given an arc and a label. Every "sanctions" edge
// is one contiguous wedge. Direction is in the label (→ ← ↔) rather than in
// arrowheads that vanish at this scale.
//
// It renders from the GraphPayload already in the store, so switching modes
// costs no request. It is SVG rather than GPU on purpose: an orbit is bounded
// at roughly 40 nodes by construction, which is exactly the size where SVG's
// crisp text and per-node event handling beat a point cloud. The Network mode
// next door is still cosmos.gl and still owns the 600-node case.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { passesLens, useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import {
  anchorForCos, Occupancy, ray, textBox, type Anchor, type Box,
} from '../lib/labels'
import {
  CLASS_COLOR, describeEdge, relClass, restrain, shapeOf, type ShapeName,
} from '../lib/graphModel'
import type { GraphPayload, OmxObject, Provenance } from '../lib/types'

const CX = 500, CY = 330, R1 = 185, R2 = 305

/** The dial's own coordinate space. The camera moves over it; the layout never
 *  changes, so panning and zooming cost nothing and cannot disturb the rings. */
const BASE_VB = { x: 0, y: 0, w: 1000, h: 660 } as const
/** Zoom limits, as multiples of the base framing. Beyond 8x the labels are
 *  larger than the nodes; below 0.35x the dial is a speck. */
const MIN_W = BASE_VB.w / 8
const MAX_W = BASE_VB.w / 0.35

interface ViewBox { x: number; y: number; w: number; h: number }

/** The camera contract the toolbar drives. Deliberately the same shape as the
 *  network canvas's, so GraphView routes zoom, fit and fit-to-selection to
 *  whichever mode is showing without knowing which one that is. */
export interface OrbitHandle {
  zoomBy: (factor: number) => void
  fit: () => void
  fitTo: (ids: string[]) => void
}

/** Spoke colour. Relation CLASS leads, because it is the same grammar the
 *  network canvas uses and the two modes must not teach different vocabularies
 *  for the same link. Sentiment is folded in only where it disagrees with the
 *  class — a nominally economic relation carrying real hostility earns the
 *  adversarial colour rather than the neutral one. */
const spokeColor = (relation: string, sentiment: number): string => {
  const cls = relClass(relation)
  if (cls !== 'adversarial' && sentiment < -0.3) return CLASS_COLOR.adversarial
  return CLASS_COLOR[cls]
}

/** Text width, for deciding whether two labels touch.
 *
 *  SVG cannot measure text without laying it out: `getComputedTextLength()`
 *  forces a synchronous reflow per call, and this pass measures up to seventy
 *  strings per render. A detached 2D context set to the same face answers for
 *  free, and the answer only has to be good enough to decide whether two
 *  rectangles overlap.
 *
 *  Memoised on (weight, size, text). The domain is the visible object names, so
 *  it is bounded by the graph; the cap is only there so a long session that
 *  walks thousands of objects cannot grow it without limit. */
const measureCache = new Map<string, number>()
let measureCtx: CanvasRenderingContext2D | null | undefined
function textWidth(text: string, size: number, weight: number): number {
  const key = `${weight}|${size}|${text}`
  const hit = measureCache.get(key)
  if (hit !== undefined) return hit
  if (measureCtx === undefined) {
    measureCtx = document.createElement('canvas').getContext('2d')
  }
  // No 2D context (jsdom): fall back to an average advance width. Placement
  // degrades to approximate rather than throwing, which matters because this
  // runs during render.
  if (!measureCtx) return text.length * size * 0.55
  measureCtx.font = `${weight} ${size}px "Geist Variable", Inter, system-ui, sans-serif`
  const w = measureCtx.measureText(text).width
  if (measureCache.size > 4000) measureCache.clear()
  measureCache.set(key, w)
  return w
}

const PROV_VAR: Record<string, string> = {
  user_created: 'var(--omx-prov-user)',
  verified: 'var(--omx-prov-verified)',
  source_backed: 'var(--omx-prov-source)',
  ai_inferred: 'var(--omx-prov-ai)',
}

/** Object shape. Fill carries the type, stroke carries provenance — so a node
 *  says what it is and how much to trust it without a legend lookup.
 *
 *  The vocabulary is `shapeOf`'s, shared with the network canvas: a country is
 *  a diamond in both modes, a company a square in both. Two modes that drew the
 *  same object differently would make the reader relearn the map every time
 *  they switched. */
function NodeShape({ shape, r, fill, stroke, dashed }: {
  shape: ShapeName; r: number; fill: string; stroke: string; dashed: boolean
}) {
  const common = {
    fill, stroke, strokeWidth: 1.8,
    strokeDasharray: dashed ? '3 2' : undefined,
  }
  if (shape === 'square') {
    return <rect {...common} x={-r * 0.86} y={-r * 0.86} width={r * 1.72} height={r * 1.72} rx={2} />
  }
  if (shape === 'diamond') {
    const s = r * 1.16
    return <polygon {...common} points={`0,${-s} ${s},0 0,${s} ${-s},0`} />
  }
  if (shape === 'triangle') {
    return <polygon {...common} points={`0,${-r * 1.2} ${r * 1.1},${r * 0.82} ${-r * 1.1},${r * 0.82}`} />
  }
  if (shape === 'hexagon' || shape === 'pentagon') {
    const sides = shape === 'hexagon' ? 6 : 5
    const s = r * 1.1
    const turn = shape === 'hexagon' ? 0 : -Math.PI / 2
    const pts = Array.from({ length: sides }, (_, i) => {
      const a = turn + (i / sides) * Math.PI * 2
      return `${(Math.cos(a) * s).toFixed(2)},${(Math.sin(a) * s).toFixed(2)}`
    }).join(' ')
    return <polygon {...common} points={pts} />
  }
  return <circle {...common} r={r} />
}

interface Ring1 {
  id: string
  /** The edge's stable key, so a spoke can open the relationship inspector —
   *  the same one the network canvas opens, from the same click. */
  key: string
  relation: string
  label: string
  sentiment: number
  provenance: Provenance
  outgoing: boolean
  symmetric: boolean
  /** Decayed edge weight, used to decide who earns a place on the ring. */
  weight: number
  angle: number
  x: number
  y: number
}

/** Orbit bounds its own rings, independent of how much the density control
 *  pulled down.
 *
 *  It used to be bounded by construction — the payload was never more than a
 *  few dozen objects, so "every neighbour of the centre" was always readable.
 *  Once density started returning 160+ objects that stopped being true: the
 *  United States has ninety direct relationships in this Space, and drawing
 *  all of them puts ninety overlapping shapes on one circle with their labels
 *  stacked on top of each other.
 *
 *  So the ring takes the strongest and SAYS how many it left out. A partial
 *  ring the reader knows is partial is useful; a complete ring they cannot
 *  read is not. Network mode is one click away for the whole picture. */
/** How far a name may slide out along its own spoke before it is given up on.
 *  Four rungs covers the worst pole crowding this ring can produce at
 *  RING1_MAX; a fifth never fired in practice. */
const LADDER = [0, 21, 42, 63] as const

const RING1_MAX = 26
const RING2_MAX = 44

interface RelGroup {
  relation: string
  label: string
  sentiment: number
  outgoing: boolean
  symmetric: boolean
  items: Ring1[]
  mid: number
}

export function Orbit({ graph, centreId, onRecentre, onReady }: {
  graph: GraphPayload
  centreId: string
  onRecentre: (id: string) => void
  /** Hands the camera to the toolbar. Optional so the component stays usable
   *  on its own — in tests, and anywhere a dial is wanted without controls. */
  onReady?: (h: OrbitHandle | null) => void
}) {
  const floor = useWorkspace((s) => s.provenanceFloor)
  const selected = useWorkspace((s) => s.selected)
  const toggle = useWorkspace((s) => s.toggle)
  const focusOn = useWorkspace((s) => s.focusOn)
  const ontology = useWorkspace((s) => s.ontology)
  const selectMany = useWorkspace((s) => s.selectMany)
  const activeEdge = useGraphUi((s) => s.activeEdge)
  const findOpen = useGraphUi((s) => s.findOpen)
  const query = useGraphUi((s) => s.query)
  const setActiveEdge = useGraphUi((s) => s.setActiveEdge)
  const [hover, setHover] = useState<string | null>(null)

  // -- camera ------------------------------------------------------------
  // Orbit had none: a fixed viewBox with no wheel or drag handling, which is
  // why it read as frozen next to Network. The dial is static and the camera
  // moves over it, so none of this can perturb the layout.
  const svgRef = useRef<SVGSVGElement>(null)
  const [vb, setVb] = useState<ViewBox>({ ...BASE_VB })
  /** Live copy, so the wheel and drag handlers can compose without waiting for
   *  React to commit — two wheel ticks in one frame would otherwise both apply
   *  to the same stale box and the second would undo the first. */
  const vbRef = useRef<ViewBox>(vb)
  const setView = useCallback((next: ViewBox) => {
    vbRef.current = next
    setVb(next)
  }, [])

  /** Pointer position in dial coordinates.
   *
   *  `preserveAspectRatio="xMidYMid meet"` letterboxes the viewBox inside the
   *  element, so the mapping is not a plain rect ratio — ignoring the offset
   *  makes the graph slide sideways as you zoom, which reads as the camera
   *  fighting you. */
  const toDial = useCallback((clientX: number, clientY: number) => {
    const el = svgRef.current
    const box = vbRef.current
    if (!el) return { x: CX, y: CY, s: 1 }
    const r = el.getBoundingClientRect()
    const s = Math.min(r.width / box.w, r.height / box.h) || 1
    const offX = (r.width - box.w * s) / 2
    const offY = (r.height - box.h * s) / 2
    return {
      x: box.x + (clientX - r.left - offX) / s,
      y: box.y + (clientY - r.top - offY) / s,
      s,
    }
  }, [])

  /** Zoom about a fixed point, so whatever is under the cursor stays there. */
  const zoomAbout = useCallback((factor: number, ax: number, ay: number) => {
    const box = vbRef.current
    const wanted = box.w / factor
    const w = Math.max(MIN_W, Math.min(MAX_W, wanted))
    const k = w / box.w
    setView({
      x: ax - (ax - box.x) * k,
      y: ay - (ay - box.y) * k,
      w,
      h: box.h * k,
    })
  }, [setView])

  // Wheel has to be bound imperatively: React's onWheel is passive, and a
  // passive listener cannot preventDefault, so the page scrolls behind the
  // graph while the graph also zooms.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const p = toDial(e.clientX, e.clientY)
      // deltaMode 1 is lines, 2 is pages — a trackpad reports pixels and a
      // mouse wheel often does not, and treating a 3-line tick as 3 pixels
      // makes the wheel feel dead on exactly the hardware most people use.
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1
      zoomAbout(Math.exp(-e.deltaY * unit * 0.0015), p.x, p.y)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [toDial, zoomAbout])

  /** Drag to pan. `moved` is what stops a pan from also selecting whatever
   *  node the drag happened to start on. */
  const dragRef = useRef<
    { px: number; py: number; box: ViewBox; scale: number; moved: boolean } | null
  >(null)
  const pannedRef = useRef(false)

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    const p = toDial(e.clientX, e.clientY)
    dragRef.current = {
      px: e.clientX, py: e.clientY, box: { ...vbRef.current }, scale: p.s, moved: false,
    }
    pannedRef.current = false
    // Capture keeps the pan alive when the pointer leaves the dial mid-drag.
    // Guarded: it throws NotFoundError for a pointer id the element does not
    // own, which is every synthetic event and some real ones during a fast
    // gesture — and an exception here would abort the drag before it starts.
    try { svgRef.current?.setPointerCapture(e.pointerId) } catch { /* uncaptured */ }
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = dragRef.current
    if (!d) return
    const dx = e.clientX - d.px
    const dy = e.clientY - d.py
    if (!d.moved && Math.hypot(dx, dy) < 4) return
    d.moved = true
    pannedRef.current = true
    setView({ ...d.box, x: d.box.x - dx / d.scale, y: d.box.y - dy / d.scale })
  }

  const endDrag = (e: React.PointerEvent<SVGSVGElement>) => {
    if (dragRef.current) {
      try { svgRef.current?.releasePointerCapture(e.pointerId) } catch { /* never held */ }
    }
    dragRef.current = null
    // Cleared after the click that follows pointerup has been dispatched, so
    // the node handlers can still see that a pan happened.
    if (pannedRef.current) window.setTimeout(() => { pannedRef.current = false }, 0)
  }

  /** Symmetry is a property of the relation type, and the ontology is the only
   *  authority on it. Inferring it from the payload does not work: edges are
   *  deduplicated on an unordered {pair, relation} key, so a reciprocal edge is
   *  never present to detect. */
  const symmetricRelations = useMemo(() => {
    const set = new Set<string>()
    for (const r of ontology?.relations ?? []) if (r.symmetric) set.add(r.key)
    return set
  }, [ontology])

  const byId = useMemo(() => {
    const m: Record<string, OmxObject> = {}
    for (const n of graph.nodes) m[n.id] = n
    return m
  }, [graph])

  const layout = useMemo(() => {
    const centre = byId[centreId]
    if (!centre) return null

    // Ring 1: every distinct neighbour of the centre, carrying the relation
    // that connected it. A node reached by two relations is placed once, under
    // the first — duplicating it would double-count the ring and make the
    // group spans lie about how much of the neighbourhood each relation owns.
    const seen = new Set([centreId])
    const ring1: Ring1[] = []
    for (const e of graph.edges) {
      if (e.source !== centreId && e.target !== centreId) continue
      const other = e.source === centreId ? e.target : e.source
      if (seen.has(other) || !byId[other]) continue
      seen.add(other)
      // `source`/`target` on a graph edge are the BFS traversal order, NOT the
      // stored relationship direction — the engine emits `source: nid` for
      // whichever node it walked from. The real direction is in the label,
      // which the graph layer suffixes with "(inbound)" when the edge is being
      // read backwards. Reading direction off `source` gives arrows that
      // contradict their own labels. `describeEdge` is the single place that
      // rule lives, shared with the network canvas.
      const meta = describeEdge(e, symmetricRelations)
      ring1.push({
        id: other,
        key: meta.key,
        relation: e.relation,
        label: meta.label,
        sentiment: e.sentiment ?? 0,
        provenance: e.provenance,
        outgoing: !meta.inbound,
        symmetric: meta.symmetric,
        weight: e.weight ?? 0,
        angle: 0, x: 0, y: 0,
      })
    }
    if (!ring1.length) {
      return { centre, ring1: [], ring2: [], groups: [] as RelGroup[], hidden: 0 }
    }

    // Keep the strongest, and remember how many were dropped so the view can
    // say so. Salience breaks ties: between two links of equal weight, the more
    // central object is the more useful one to have on the ring.
    const total = ring1.length
    let hidden = 0
    if (total > RING1_MAX) {
      const keepIds = new Set(
        [...ring1]
          .sort((a, b) => (b.weight - a.weight)
            || ((byId[b.id]?.salience ?? 0) - (byId[a.id]?.salience ?? 0)))
          .slice(0, RING1_MAX)
          .map((k) => k.id))
      // Anything dropped stops being "seen", or ring 2 would refuse to place it
      // and the outer ring would develop holes wherever the inner one was cut.
      for (const n of ring1) if (!keepIds.has(n.id)) seen.delete(n.id)
      // Filtered in place, in DISCOVERY order — the relation groups below rely
      // on same-relation entries staying contiguous, and the strength sort
      // interleaves them. Spliced rather than reassigned because `ring1` is the
      // array the rest of this block writes angles into.
      const keptInOrder = ring1.filter((n) => keepIds.has(n.id))
      hidden = total - keptInOrder.length
      ring1.splice(0, ring1.length, ...keptInOrder)
    }

    // Group by relation, then hand each group a slice of the circle in
    // proportion to its size, separated by a fixed gap. Proportional rather
    // than equal because a relation holding nine neighbours should visibly own
    // more of the ring than one holding a single neighbour.
    // Grouped by relation AND direction: "whom do I sanction" and "who
    // sanctions me" are different questions and must not share a wedge.
    const groups: RelGroup[] = []
    for (const n of ring1) {
      let g = groups.find((x) => x.relation === n.relation && x.outgoing === n.outgoing)
      if (!g) {
        g = {
          relation: n.relation, label: n.label, sentiment: n.sentiment,
          outgoing: n.outgoing, symmetric: n.symmetric, items: [], mid: 0,
        }
        groups.push(g)
      }
      g.items.push(n)
    }

    const GAP = 0.22
    const avail = Math.PI * 2 - GAP * groups.length
    let cur = -Math.PI / 2
    for (const g of groups) {
      const span = Math.max(avail * (g.items.length / ring1.length), 0.02)
      g.mid = cur + span / 2
      g.items.forEach((n, i) => {
        const t = g.items.length === 1 ? 0.5 : i / (g.items.length - 1)
        n.angle = cur + span * t
        n.x = CX + Math.cos(n.angle) * R1
        n.y = CY + Math.sin(n.angle) * R1
      })
      cur += span + GAP
    }

    // Ring 2: one hop further, fanned around the parent's angle so the second
    // ring reads as belonging to the first rather than as a second cloud.
    //
    // Budgeted per parent rather than globally, so one hub does not consume the
    // whole outer ring and leave every other branch with nothing. A ring that
    // shows second-degree context for three of twenty-six neighbours is worse
    // than one that shows a little for all of them.
    const ring2: { id: string; parent: Ring1; x: number; y: number }[] = []
    const perParent = Math.max(1, Math.floor(RING2_MAX / Math.max(1, ring1.length)))
    for (const parent of ring1) {
      const out = graph.edges.filter((e) => e.source === parent.id || e.target === parent.id)
      const fresh = out
        .map((e) => (e.source === parent.id ? e.target : e.source))
        .filter((id) => !seen.has(id) && byId[id])
      const uniq = Array.from(new Set(fresh))
        .sort((a, b) => (byId[b]?.salience ?? 0) - (byId[a]?.salience ?? 0))
        .slice(0, perParent)
      uniq.forEach((id, k) => {
        seen.add(id)
        const off = (uniq.length <= 1 ? 0 : (k - (uniq.length - 1) / 2)) * 0.24
        const ang = parent.angle + off
        ring2.push({ id, parent, x: CX + Math.cos(ang) * R2, y: CY + Math.sin(ang) * R2 })
      })
    }

    return { centre, ring1, ring2, groups, hidden }
    // `symmetricRelations` belongs here: the ontology is fetched during init
    // and can land after the first paint. Without it the layout keeps the
    // empty set it was built with and every symmetric relation renders with a
    // one-way arrow forever.
  }, [graph, byId, centreId, symmetricRelations])

  /** Where each drawn node sits on the dial, for fit-to-selection. */
  const placed = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>()
    if (!layout) return m
    m.set(layout.centre.id, { x: CX, y: CY })
    for (const n of layout.ring1) m.set(n.id, { x: n.x, y: n.y })
    for (const n of layout.ring2) m.set(n.id, { x: n.x, y: n.y })
    return m
  }, [layout])

  // Hand the camera to the toolbar. Same contract as the network canvas, so
  // the zoom, fit and focus buttons behave identically in all three modes
  // rather than going dead in this one.
  useEffect(() => {
    if (!onReady) return
    onReady({
      zoomBy: (factor) => {
        const box = vbRef.current
        zoomAbout(factor, box.x + box.w / 2, box.y + box.h / 2)
      },
      fit: () => setView({ ...BASE_VB }),
      fitTo: (ids) => {
        const pts = ids.map((id) => placed.get(id)).filter(Boolean) as { x: number; y: number }[]
        // One point has no extent, and framing it would drive the camera to
        // maximum zoom on an empty field. Reframing the whole dial is the
        // honest fallback — the same rule the network canvas follows.
        if (pts.length < 2) { setView({ ...BASE_VB }); return }
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
        for (const p of pts) {
          x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y)
          x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y)
        }
        // Padding leaves room for the labels, which are drawn below the nodes
        // and would otherwise be cropped by a bounding box that is exact.
        const pad = 70
        const w = Math.max(MIN_W, (x1 - x0) + pad * 2)
        const h = w * (BASE_VB.h / BASE_VB.w)
        setView({
          x: (x0 + x1) / 2 - w / 2,
          y: (y0 + y1) / 2 - h / 2,
          w,
          h,
        })
      },
    })
    return () => onReady(null)
  }, [onReady, placed, zoomAbout, setView])

  // Recentring is a new subject, so it gets a fresh framing. Without this the
  // camera stays wherever the last drill-down left it and the new centre can
  // land off-screen, which is indistinguishable from the view being broken.
  useEffect(() => { setView({ ...BASE_VB }) }, [centreId, setView])

  if (!layout) {
    return (
      <div className="omx-empty">
        <div className="glyph">◎</div>
        <h3>Nothing to orbit</h3>
        <p>Select an object to put it at the centre.</p>
      </div>
    )
  }

  const { centre, ring1, ring2, groups, hidden } = layout

  // Adjacency drives hover isolation: hovering anything drops everything not
  // attached to it toward the background.
  const adjacent = (id: string): boolean => {
    if (!hover || hover === id) return true
    return graph.edges.some(
      (e) => (e.source === hover && e.target === id) || (e.target === hover && e.source === id))
  }
  // A live find query re-weights the dial exactly as it does the network
  // canvas: matches keep full strength and their names, everything else drops
  // back to context. Same rule as `findMatches` — a raw substring on the name.
  const q = findOpen ? query.trim().toLowerCase() : ''
  const searching = !!q
  const matched = (id: string): boolean =>
    !searching || (byId[id]?.name ?? '').toLowerCase().includes(q)

  const dimmed = (id: string): boolean => {
    const o = byId[id]
    if (o && !passesLens(o, floor)) return true
    if (searching && !matched(id)) return true
    return !!hover && !adjacent(id)
  }

  const node = (id: string, x: number, y: number, r: number, opts: {
    big?: boolean
  } = {}) => {
    const o = byId[id]
    if (!o) return null
    const isSel = selected.includes(id)
    const dim = dimmed(id)
    const ai = o.provenance === 'ai_inferred'
    // The same restrained tone the network canvas uses. Applied here rather
    // than in the payload so the Map and TERRA views keep the ontology's own
    // colours — the calming is a property of the graph, not of the data.
    const tone = restrain(o.color)

    return (
      <g
        key={id}
        transform={`translate(${x} ${y})`}
        className="omx-orbit-node"
        style={{ opacity: dim ? 0.12 : 1 }}
        onMouseEnter={() => setHover(id)}
        onMouseLeave={() => setHover(null)}
        // A pan that happened to start on a node must not also recentre on it.
        onClick={(e) => {
          if (pannedRef.current) return
          if (e.metaKey || e.ctrlKey) toggle(id, 'graph'); else onRecentre(id)
        }}
        onDoubleClick={(e) => { e.stopPropagation(); void focusOn(o) }}
      >
        <title>{`${o.name} — ${o.typeLabel} · ${o.provenanceLabel}`}</title>
        {opts.big && <circle r={r + 18} fill={tone} opacity={0.09} />}
        {isSel && !opts.big && (
          <circle className="omx-halo-ring" r={r + 7} fill="none"
                  stroke="var(--omx-gold)" strokeWidth={1.5} />
        )}
        {/* A search hit, ringed in the accent — the same mark the network
            canvas uses, so the two modes answer a find identically. */}
        {searching && matched(id) && (
          <circle r={r + 4.5} fill="none" stroke="var(--omx-gold)" strokeWidth={1.8} />
        )}
        {o.ring && !opts.big && (
          <circle r={r + 4} fill="none" stroke={tone} strokeWidth={1} opacity={0.4} />
        )}
        <NodeShape
          shape={shapeOf(o)}
          r={r}
          fill={tone}
          stroke={opts.big ? 'var(--omx-gold)' : (PROV_VAR[o.provenance] ?? 'var(--omx-prov-ai)')}
          dashed={ai && !opts.big}
        />
        {/* The centre gets its provenance as a full ring rather than an
            outline, because at 27px the outline is too subtle to carry the
            most important claim on the screen. */}
        {opts.big && (
          <circle
            r={r + 9} fill="none"
            stroke={PROV_VAR[o.provenance] ?? 'var(--omx-prov-ai)'}
            strokeWidth={2}
            strokeDasharray={ai ? '4 4' : undefined}
            opacity={0.75}
          />
        )}
      </g>
    )
  }

  // ---- labels -------------------------------------------------------------
  // Planned in one pass and drawn ABOVE every mark.
  //
  // They used to live inside each node's own <g>, which caused the two failures
  // that made this view look broken. A node drawn later painted over an earlier
  // node's name — "Microsoft" was sliced in half by the Google dot — because
  // SVG paints in document order and the nodes are emitted in ring order.
  // And nothing tested one name against another, so around the top and bottom
  // of the ring, where adjacent nodes differ by a few pixels of y, three and
  // four names piled into the same strip: BlackRock, ExxonMobil and Lockheed
  // Martin were one illegible smear.
  //
  // Two rules fix both. Names radiate OUTWARD along their own spoke and anchor
  // by side, so the ring opens them into a fan instead of stacking them into a
  // column; and every name is tested against every mark and every name already
  // placed, in importance order, so the one that survives a collision is the
  // one worth keeping. Marks are seeded first, which is what stops a name
  // landing on a dot that is not its own.
  interface Lab {
    key: string; text: string; x: number; y: number
    anchor: Anchor
    size: number; weight: number; cls: string; fill?: string; opacity: number
  }

  const occ = new Occupancy()
  const r1of = (id: string) => 13 + (byId[id]?.salience ?? 0) * 7
  const r2of = (id: string) => 8 + (byId[id]?.salience ?? 0) * 5

  // Marks are reserved TIGHT. These boxes are axis-aligned and the marks are
  // round, so a square of half-size r already over-reserves by 41% along each
  // diagonal; padding it further pushed every label on a diagonal spoke into a
  // collision with its own node and the ring lost half its names.
  // Marks first, so a name can never land on a dot that is not its own.
  occ.mark(CX, CY, 30)
  for (const n of ring1) occ.mark(n.x, n.y, r1of(n.id))
  for (const n of ring2) occ.mark(n.x, n.y, r2of(n.id))

  const labels: Lab[] = []
  const boxFor = (l: Lab): Box =>
    textBox(l.x, l.y, textWidth(l.text, l.size, l.weight), l.size + 4, l.anchor)

  /** `force` is for the things the reader has asked about by name — the
   *  subject, the hovered node, the selection. They are drawn wherever they
   *  land, because losing the label you are pointing at is worse than an
   *  overlap you caused yourself. */
  const place = (l: Lab, force = false) => {
    const box = boxFor(l)
    if (!force && !occ.fits(box)) return
    occ.add(box)
    labels.push(l)
  }
  /** Place along a spoke, sliding outward until it fits. */
  const placeRadial = (l: Lab, angle: number, from: number, force = false) => {
    const pts = ray(CX, CY, angle, from, LADDER)
    const cands = pts.map((p) => boxFor({ ...l, x: p.x, y: p.y }))
    const i = occ.firstFit(cands)
    if (i >= 0) { labels.push({ ...l, x: pts[i].x, y: pts[i].y }); return }
    if (!force) return
    const at = { ...l, x: pts[0].x, y: pts[0].y }
    occ.add(boxFor(at))
    labels.push(at)
  }

  place({
    key: 'centre', text: centre.name, x: CX, y: CY + 53,
    anchor: 'middle', size: 14.5, weight: 600, cls: 'omx-orbit-label', opacity: 1,
  }, true)

  // The wedge names sit outside the ring and are placed before the node names:
  // "these six are all sanctions" is the reading the ring exists to give, and
  // an individual dot is recoverable from hover in a way the grouping is not.
  for (const g of groups) {
    placeRadial({
      key: `rel-${g.relation}-${g.outgoing ? 'out' : 'in'}`,
      text: (g.symmetric ? '↔ ' : g.outgoing ? '→ ' : '← ') + g.label.toUpperCase(),
      x: 0, y: 0,
      anchor: anchorForCos(Math.cos(g.mid)), size: 9.5, weight: 500, cls: 'omx-orbit-rel',
      fill: spokeColor(g.relation, g.sentiment),
      opacity: hover ? 0.35 : 0.9,
    }, g.mid, R1 + 92)
  }

  for (const n of [...ring1].sort(
    (a, b) => (byId[b.id]?.salience ?? 0) - (byId[a.id]?.salience ?? 0))) {
    const o = byId[n.id]
    if (!o) continue
    placeRadial({
      key: `l1-${n.id}`, text: o.name, x: 0, y: 0,
      anchor: anchorForCos(Math.cos(n.angle)), size: 11.5, weight: 500,
      cls: 'omx-orbit-label', opacity: dimmed(n.id) ? 0.12 : 1,
    }, n.angle, R1 + r1of(n.id) + 15,
      hover === n.id || selected.includes(n.id) || (searching && matched(n.id)))
  }

  // The outer ring is context, not subject: it is named only when pointed at,
  // or the dial carries seventy names and reads as a word cloud.
  for (const n of ring2) {
    // A find reaches the outer ring too, or searching in Orbit would silently
    // only look at the inner one.
    const hit = searching && matched(n.id)
    if (!hit && hover !== n.id && !selected.includes(n.id)) continue
    const o = byId[n.id]
    if (!o) continue
    place({
      key: `l2-${n.id}`, text: o.name, x: n.x, y: n.y + r2of(n.id) + 12,
      anchor: 'middle', size: 10.5, weight: 500, cls: 'omx-orbit-label', opacity: 1,
    }, true)
  }

  return (
    <>
    {/* Say what was left out. A ring that silently shows 26 of 91 links reads
        as a complete answer to "what is attached to this", and a wrong answer
        stated confidently is the one failure this view cannot afford. */}
    {hidden > 0 && (
      <div className="omx-orbit-trim">
        <span className="omx-label">
          Strongest {ring1.length} of {ring1.length + hidden} direct links
        </span>
        <button className="omx-chip sm"
                onClick={() => useGraphUi.getState().setMode('network')}>
          See all in Network
        </button>
      </div>
    )}
    {!ring1.length && (
      <div className="omx-graph-note">
        <strong>No relationships at this depth.</strong>
        <span>
          Nothing in the loaded graph connects to {centre.name}. Try Expand,
          raise density, or widen the trust lens.
        </span>
      </div>
    )}
    <svg
      ref={svgRef}
      viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
      preserveAspectRatio="xMidYMid meet"
      className="omx-orbit-svg"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      // Double-click reframes, the same gesture a map gives you. It is also the
      // way back from a zoom that went too far without hunting for the toolbar.
      onDoubleClick={(e) => {
        if (e.target === svgRef.current) setView({ ...BASE_VB })
      }}
    >
      {/* guide rings */}
      {[R1, R2].map((r) => (
        <circle key={r} cx={CX} cy={CY} r={r} fill="none"
                stroke="var(--omx-line)" strokeWidth={1}
                strokeDasharray="2 7" opacity={0.55} />
      ))}

      {/* One arc per relation group, plus its label. The arc is what turns a
          ring of dots into "these six are all the same kind of link". */}
      {groups.map((g, i) => {
        const col = spokeColor(g.relation, g.sentiment)
        const first = g.items[0], last = g.items[g.items.length - 1]
        const arc = g.items.length > 1 ? (() => {
          const x0 = CX + Math.cos(first.angle) * R1, y0 = CY + Math.sin(first.angle) * R1
          const x1 = CX + Math.cos(last.angle) * R1, y1 = CY + Math.sin(last.angle) * R1
          const large = (last.angle - first.angle) > Math.PI ? 1 : 0
          return `M${x0},${y0} A${R1},${R1} 0 ${large} 1 ${x1},${y1}`
        })() : null
        // The wedge's NAME is not drawn here — it is planned with every other
        // label below, so it competes for space on equal terms instead of
        // being painted over by whatever node happens to sit at that angle.
        return (
          <g key={`${g.relation}-${g.outgoing ? 'out' : 'in'}-${i}`}
             style={{ opacity: hover ? 0.35 : 1 }}>
            {arc && (
              <path d={arc} fill="none" stroke={col} strokeWidth={2.5}
                    opacity={0.3} strokeLinecap="round" />
            )}
          </g>
        )
      })}

      {/* Spokes: centre → ring 1. Width carries salience, dash carries
          inference, colour carries relation class.

          Each one is clickable and opens the SAME relationship inspector the
          network canvas opens. A mode where the lines are inert would make
          Orbit the place evidence is unreachable from, which is the opposite
          of what it is for. A transparent overlay stroke widens the hit area
          to something a hand can actually land on without thickening the line
          that is drawn. */}
      {ring1.map((n) => {
        const col = spokeColor(n.relation, n.sentiment)
        const active = activeEdge === n.key
        return (
          <g key={`s1-${n.id}`} className="omx-orbit-spoke">
            <line
              x1={CX} y1={CY} x2={n.x} y2={n.y}
              stroke={col}
              strokeWidth={(1.2 + (byId[n.id]?.salience ?? 0) * 1.6) + (active ? 1.6 : 0)}
              strokeDasharray={n.provenance === 'ai_inferred' ? '5 4' : undefined}
              strokeLinecap="round"
              opacity={dimmed(n.id) ? 0.05 : active ? 0.95 : 0.42}
            />
            <line
              x1={CX} y1={CY} x2={n.x} y2={n.y}
              stroke="transparent" strokeWidth={12}
              style={{ cursor: 'pointer' }}
              onClick={(ev) => {
                if (pannedRef.current) return
                ev.stopPropagation()
                setActiveEdge(n.key)
                selectMany([centreId, n.id], 'graph')
              }}
            >
              <title>
                {`${(n.symmetric ? '↔ ' : n.outgoing ? '→ ' : '← ')}${n.label} — click to inspect`}
              </title>
            </line>
          </g>
        )
      })}
      {ring2.map((n) => (
        <line
          key={`s2-${n.id}`}
          x1={n.parent.x} y1={n.parent.y} x2={n.x} y2={n.y}
          stroke="var(--omx-line-strong)" strokeWidth={0.9}
          opacity={dimmed(n.id) ? 0.04 : 0.24}
        />
      ))}

      {ring2.map((n) => node(n.id, n.x, n.y, r2of(n.id)))}
      {ring1.map((n) => node(n.id, n.x, n.y, r1of(n.id)))}
      {node(centre.id, CX, CY, 27, { big: true })}

      {/* Every name, last. Document order IS z-order in SVG, so this is what
          guarantees no mark can ever cover a label.
          Each one is wrapped in its own translated <g> rather than positioned
          by x/y, so it inherits the same 550ms transform transition the nodes
          use and glides with its dot when the dial recentres. Positioned text
          would snap while the marks moved, and the pairing would break for the
          whole animation — which is the one moment the reader is tracking a
          name across the screen. */}
      <g className="omx-orbit-labels">
        {labels.map((l) => (
          <g key={l.key} className="omx-orbit-label-g"
             transform={`translate(${l.x} ${l.y})`} opacity={l.opacity}>
            <text
              className={l.cls}
              textAnchor={l.anchor}
              dominantBaseline="middle"
              fontSize={l.size}
              fontWeight={l.weight}
              fill={l.fill}
            >{l.text}</text>
          </g>
        ))}
      </g>
    </svg>
    </>
  )
}
