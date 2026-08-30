// The network canvas. cosmos.gl on the GPU for the force simulation and the
// points; edges, labels, rings and emphasis drawn by us on a 2D overlay.
//
// Why an overlay at all: the readability rules below are the whole point and no
// renderer makes them for you. They were arrived at by watching the view become
// unreadable without them.
//
//   * label budget scales with zoom, and within the budget the most important
//     nodes win. Rendering every label at once is illegible at any zoom.
//   * a relationship has to say what KIND it is and how much we are willing to
//     assert about it. Class is colour, certainty is line style, strength is
//     width. The GPU link pass can do colour and width but not dashes, so
//     edges are ours whenever there are few enough of them to draw.
//   * selection emphasis is a step change in SIZE plus a ring, never a colour
//     swap, because colour is already spent on object type.
//
// The split with the GPU is by count, not by preference:
//
//     edges <= EDGE_DRAW_LIMIT   we draw every edge, with the full grammar.
//                                cosmos's link colours go to zero alpha.
//     edges >  EDGE_DRAW_LIMIT   cosmos draws the links (colour + width +
//                                arrows, no dashes) and we draw only the ones
//                                under emphasis. Nothing is hidden; the
//                                certainty channel degrades, and it degrades
//                                on exactly the graphs where no reader was
//                                going to trace individual edges anyway.
//
// cosmos.gl is vendored at /static/cosmos.min.js and loaded at runtime rather
// than bundled, exactly as TERRA does it.

import { useEffect, useRef } from 'react'
import { useWorkspace } from '../store/workspace'
import { useGraphUi } from '../store/graphUi'
import { inkNow, type Ink } from '../lib/appearance'
import { Occupancy, textBox } from '../lib/labels'
import {
  FOCUS_ALPHA, FOCUS_EDGE_ALPHA, SHAPE, focusTiers,
  type GraphModel, type RenderEdge,
} from '../lib/graphModel'
import type { GraphPayload } from '../lib/types'

declare global {
  interface Window { Cosmos?: any; __omxCosmos?: Promise<any> }
}

/** Above this, edges move to the GPU. Chosen by what a 2D context can stroke
 *  inside one frame with dash patterns on, measured rather than guessed: at
 *  ~2500 dashed strokes a frame still lands inside the budget on integrated
 *  graphics, and beyond it the canvas starts dropping frames while panning. */
const EDGE_DRAW_LIMIT = 2500

/** Above this, nodes move to the GPU too.
 *
 *  Nodes are drawn on the overlay by default for two reasons, and the second
 *  is not a preference:
 *
 *    * shape, ring, provenance outline and per-node opacity are all wanted at
 *      once, and only a 2D context gives all four.
 *    * **`setPointColors` does not take effect after the first render on the
 *      vendored cosmos build.** Measured, not assumed: calling it from a frame
 *      leaves `getPointColors()` returning the default grey, while
 *      `setPointShapes` from the same frame applies immediately. Focus tiers
 *      need per-node opacity to change on interaction, so they cannot go
 *      through that setter at all.
 *
 *  The server caps a subgraph at 600 objects, so in practice this branch is
 *  always the one taken; the GPU fallback exists for a future that raises it. */
const NODE_DRAW_LIMIT = 2500

/** Below this, the minimap is decoration. */
export const MINIMAP_THRESHOLD = 80

// The vendored build registers `window.Cosmos` (capital C).
function loadCosmos(): Promise<any> {
  if (window.Cosmos) return Promise.resolve(window.Cosmos)
  if (window.__omxCosmos) return window.__omxCosmos
  window.__omxCosmos = new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = '/static/cosmos.min.js'
    s.async = true
    s.onload = () => window.Cosmos
      ? resolve(window.Cosmos)
      : reject(new Error('renderer did not register'))
    s.onerror = () => reject(new Error('cosmos.gl failed to load'))
    document.head.appendChild(s)
  })
  return window.__omxCosmos
}

const hexToRgb01 = (hex: string): [number, number, number] => {
  let h = (hex || '#d3ad55').replace('#', '')
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
  const n = parseInt(h, 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

/** Dash pattern per certainty. Confirmed is solid, inferred is dashed, weak is
 *  dotted — three steps a reader can name without a legend. */
const DASH: Record<string, number[]> = {
  confirmed: [],
  inferred: [5, 4],
  weak: [1.5, 3.5],
}

// The canvas cannot use CSS variables — a 2D context takes literal colours —
// so it reads the live pair out of `lib/appearance`, which resolves the same
// tokens the DOM surfaces are painted with and caches them until the theme
// actually changes. That is the ONLY colour source in this file; anything
// hard-coded here would go stale the moment the accent changed.
//
// Without it the label plates and the minimap stayed near-black in light mode:
// dark boxes scattered over a cream ground, which is the one way a canvas can
// look broken while every DOM surface around it looks right.

/** Trace one object's shape into the current path.
 *
 *  Same vocabulary as cosmos's PointShape enum and as the legend's glyphs, so
 *  the canvas, the key and the filter panel all draw a country the same way.
 *  Radii are adjusted per shape so the forms read as the same visual weight —
 *  a square inscribed in a circle of radius r looks noticeably heavier than
 *  the circle, and a triangle noticeably lighter. */
function shapePath(
  ctx: CanvasRenderingContext2D, shape: string, x: number, y: number, r: number,
) {
  switch (shape) {
    case 'square': {
      const s = r * 0.86
      ctx.rect(x - s, y - s, s * 2, s * 2)
      return
    }
    case 'diamond': {
      const s = r * 1.16
      ctx.moveTo(x, y - s); ctx.lineTo(x + s, y)
      ctx.lineTo(x, y + s); ctx.lineTo(x - s, y)
      ctx.closePath()
      return
    }
    case 'triangle': {
      const s = r * 1.2
      ctx.moveTo(x, y - s)
      ctx.lineTo(x + s * 0.92, y + s * 0.68)
      ctx.lineTo(x - s * 0.92, y + s * 0.68)
      ctx.closePath()
      return
    }
    case 'pentagon':
    case 'hexagon': {
      const sides = shape === 'hexagon' ? 6 : 5
      const s = r * 1.1
      // Flat-top hexagon, point-top pentagon: both read as themselves at 8px,
      // which a shared rotation does not.
      const turn = shape === 'hexagon' ? 0 : -Math.PI / 2
      for (let i = 0; i < sides; i++) {
        const a = turn + (i / sides) * Math.PI * 2
        const px = x + Math.cos(a) * s
        const py = y + Math.sin(a) * s
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
      }
      ctx.closePath()
      return
    }
    default:
      ctx.arc(x, y, r, 0, Math.PI * 2)
  }
}

/** Everything the frame loop needs that is not React state. Held in one ref so
 *  the loop reads a single object rather than closing over a dozen. */
interface Frame {
  G: any
  /** Projected screen positions, x,y interleaved, rebuilt each frame. */
  sx: Float32Array
  /** Node index under the cursor, or -1. Written by the picker, read by the
   *  draw so the two can never disagree about what is hovered. */
  hover: number
  hoverEdge: number
  zoom: number
}

export interface NetworkHandle {
  zoomBy: (factor: number) => void
  fit: () => void
  fitTo: (ids: string[]) => void
}

export function NetworkCanvas({ model, clustered, communities, onReady, onError }: {
  model: GraphModel
  /** Community layout: pull each cluster together with a cluster force. */
  clustered: boolean
  /** The backend's own cluster summaries, named by their most important
   *  members. Only used while `clustered`; a group with no name is a blob. */
  communities: GraphPayload['communities']
  onReady: (h: NetworkHandle | null) => void
  onError: (message: string) => void
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const frameRef = useRef<Frame | null>(null)

  const select = useWorkspace((s) => s.select)
  const toggle = useWorkspace((s) => s.toggle)
  const focusOn = useWorkspace((s) => s.focusOn)

  // -- build / rebuild the simulation ------------------------------------
  // Deliberately depends only on the model and the layout flag. Selection,
  // hover, focus and filters all reach the canvas through the frame loop
  // reading the stores directly, so none of them can rebuild the simulation —
  // which is what stops the layout jumping every time the user clicks.
  useEffect(() => {
    const host = hostRef.current
    if (!host || !model.nodes.length) return
    let disposed = false
    let cleanupPick: (() => void) | null = null
    // Deferred fits have to be cancellable: a mode switch that lands between
    // them would otherwise fit a camera belonging to a destroyed renderer.
    const timers: number[] = []

    loadCosmos().then((Cosmos) => {
      if (disposed || !hostRef.current) return
      const nodes = model.nodes
      const edges = model.edges

      const links: number[] = []
      for (const e of edges) links.push(e.a, e.b)

      // Layout scale, derived from size once so the config, the seed ring and
      // the fit schedule all agree. They have to: seeding far outside the
      // settled size is what makes a graph visibly collapse inward for its
      // first half-minute, and fitting to a camera that no longer matches the
      // layout is what leaves it sitting in a corner.
      const linkDistance = Math.round(130 + Math.min(150, nodes.length * 0.62))
      const repulsion = 1.7 + Math.min(1.7, nodes.length / 190)

      // Every value below is load-bearing and was derived by debugging TERRA.
      // See the cosmos.gl constraints note before changing any of them, and
      // never call setConfig() afterwards: it rebuilds the simulation, and
      // calling it from a render loop collapses the layout into a knot.
      const G = new Cosmos.Graph(hostRef.current, {
        // A string, parsed via d3-color. Transparent so the overlay canvas can
        // sit under the points.
        backgroundColor: 'rgba(0,0,0,0)',
        spaceSize: 4096,
        // Left on even when we draw the edges ourselves: silencing the GPU
        // pass by flipping this would need setConfig. Zero-alpha link colours
        // achieve the same thing without touching the simulation.
        renderLinks: true,
        curvedLinks: false,
        linkArrows: false,
        linkWidthScale: 1,
        pointSizeScale: 1,
        // Constant SCREEN size. With scaling on, zooming in turns a 40-node
        // neighbourhood into overlapping blobs, and the overlay's label
        // collision boxes stop matching what is drawn.
        scalePointsOnZoom: false,
        renderHoveredPointRing: false,
        // GRAVITY MUST BE ZERO. Any real value keeps pulling every point
        // toward the centre for as long as the simulation runs, so the layout
        // never reaches equilibrium — it contracts forever into an unreadable
        // knot and no camera fit can rescue it. Repulsion and link springs
        // balance on their own; cohesion comes from the links.
        simulationGravity: 0,
        // Both of these scale with node count. A constant that reads well at
        // forty nodes packs a hundred and sixty into a knot: the springs pull
        // proportionally harder as degree rises, so repulsion has to rise with
        // it or the core collapses and the periphery is all that stays legible.
        // Clustering adds an inward force, so repulsion has to rise with it or
        // the groups pack tighter than the objects inside them are readable.
        simulationRepulsion: clustered ? repulsion * 1.5 : repulsion,
        simulationRepulsionTheta: 1.4,
        // Sets the settled SIZE of the layout, and must be close to the radius
        // new points are seeded at below — otherwise the graph spends its
        // first half-minute collapsing out of frame.
        simulationLinkDistance: linkDistance,
        simulationLinkSpring: 0.26,
        simulationFriction: 0.88,
        // How long the layout stays alive, in ticks — and it is very nearly a
        // duration, not a rate. Read from the vendored build rather than
        // assumed, because the comment that used to sit here said the opposite
        // and that is why the graph never stopped:
        //
        //     alphaDecay = 1 - ALPHA_MIN^(1/simulationDecay),  ALPHA_MIN = 0.001
        //
        // so alpha reaches the floor after almost exactly `simulationDecay`
        // ticks. At the previous value of 5000 — which is also cosmos's own
        // default — that is 5000 frames, or **83 seconds of continuous motion**
        // at 60fps. Nothing was wrong with the forces; the graph was simply
        // still cooling long after the reader had given up waiting, which is
        // what made a node impossible to click and a name impossible to read.
        //
        // ~7s settles fast enough to work in and slow enough to see the
        // structure resolve. Clusters get longer because the cluster force has
        // to drag whole groups apart before the springs can settle inside them,
        // and cutting it short leaves the groups half-separated.
        simulationDecay: clustered ? 620 : 430,
        // Community layout. Cluster ids are handed over below; without a
        // strength the clusters exist but exert nothing.
        //
        // 0.14, not 0.3: the cluster force fights repulsion directly, and at
        // a third it wins outright — every community collapses to a single
        // overlapping blob and the individual objects inside it become
        // unreadable, which defeats the point of grouping them. Low enough to
        // separate the groups, weak enough to let them breathe.
        ...(clustered ? { simulationCluster: 0.14 } : {}),
        // Picking is ours (see below), so the renderer's drag — which depends
        // on the hit-testing we are not using — is off. Pan and zoom are
        // untouched and still come from the renderer.
        enableDrag: false,
      })

      // ---- static buffers --------------------------------------------------
      // Written once, here, and never from a frame. The colour setter only
      // takes effect before the first render on this build (see
      // NODE_DRAW_LIMIT), so this is the only place it can be trusted.
      const drawNodesHere = nodes.length <= NODE_DRAW_LIMIT
      const shapes = new Float32Array(nodes.length)
      const sizes = new Float32Array(nodes.length)
      const colors = new Float32Array(nodes.length * 4)
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i]
        shapes[i] = SHAPE[n.shape]
        sizes[i] = n.size * 1.9
        if (drawNodesHere) continue          // left at alpha 0: the overlay draws them
        const [r, g, b] = hexToRgb01(n.color)
        // Provenance dims the node. An AI-inferred object must not look as
        // solid as a verified one — "never let generated relationships look
        // like verified facts", expressed in the render.
        colors[i * 4] = r; colors[i * 4 + 1] = g; colors[i * 4 + 2] = b
        colors[i * 4 + 3] = n.o.provenance === 'ai_inferred' ? 0.6
          : n.o.provenance === 'source_backed' ? 0.85 : 1
      }
      try { G.setPointShapes(shapes) } catch { /* older build: circles only */ }
      G.setPointSizes(sizes)
      G.setPointColors(colors)

      if (clustered) {
        const clusters = new Float32Array(nodes.length)
        for (let i = 0; i < nodes.length; i++) clusters[i] = nodes[i].o.community ?? 0
        try { G.setPointClusters(clusters) } catch { /* not supported */ }
      }

      if (links.length) {
        G.setLinks(new Float32Array(links))
        const widths = new Float32Array(edges.length)
        const arrows: boolean[] = new Array(edges.length)
        for (let i = 0; i < edges.length; i++) {
          widths[i] = 0.6 + edges[i].strength * 1.9
          arrows[i] = !edges[i].symmetric
        }
        try { G.setLinkWidths(widths) } catch { /* fixed width */ }
        try { G.setLinkArrows(arrows) } catch { /* no arrows */ }
      }

      // ---- our own picking -------------------------------------------------
      // The renderer's onClick/onPointMouseOver depend on its internal hover
      // pass, which `renderHoveredPointRing: false` takes out. Picking against
      // the same projected positions the overlay draws also guarantees that
      // what is under the cursor is always what is drawn — the two can never
      // disagree, which they otherwise do while the layout is still cooling.
      //
      // It reads the projection the DRAW loop cached rather than recomputing
      // it. The view this replaces called spaceToScreenPosition once per node
      // on every pointer move, inside a try/catch; on a 400-node graph that is
      // 400 guarded calls per mousemove and it was the single most expensive
      // thing on the canvas.
      const pickNode = (mx: number, my: number): number => {
        const f = frameRef.current
        if (!f) return -1
        const sx = f.sx
        let best = -1
        // Generous, and scaled by the node's own radius so a large hub is
        // easier to hit than a peripheral dot — which matches where the
        // reader is aiming.
        let bestD = Infinity
        for (let i = 0; i < nodes.length; i++) {
          const dx = sx[i * 2] - mx
          const dy = sx[i * 2 + 1] - my
          const d = dx * dx + dy * dy
          const r = Math.max(11, nodes[i].size + 7)
          if (d < r * r && d < bestD) { bestD = d; best = i }
        }
        return best
      }

      /** Nearest edge within a few pixels of the cursor, as a segment distance.
       *  Only consulted when no node was hit — a node under the cursor always
       *  wins, because an edge passing behind a node is not what was aimed at. */
      const pickEdge = (mx: number, my: number): number => {
        const f = frameRef.current
        if (!f) return -1
        const sx = f.sx
        let best = -1
        let bestD = 7 * 7
        for (let i = 0; i < edges.length; i++) {
          const e = edges[i]
          const x1 = sx[e.a * 2], y1 = sx[e.a * 2 + 1]
          const x2 = sx[e.b * 2], y2 = sx[e.b * 2 + 1]
          const vx = x2 - x1, vy = y2 - y1
          const len2 = vx * vx + vy * vy
          if (!len2) continue
          let t = ((mx - x1) * vx + (my - y1) * vy) / len2
          t = t < 0 ? 0 : t > 1 ? 1 : t
          const dx = x1 + t * vx - mx
          const dy = y1 + t * vy - my
          const d = dx * dx + dy * dy
          if (d < bestD) { bestD = d; best = i }
        }
        return best
      }

      const local = (ev: MouseEvent): [number, number] => {
        const rect = hostRef.current!.getBoundingClientRect()
        return [ev.clientX - rect.left, ev.clientY - rect.top]
      }

      const onMove = (ev: MouseEvent) => {
        const f = frameRef.current
        if (!f) return
        const [mx, my] = local(ev)
        const n = pickNode(mx, my)
        const e = n === -1 ? pickEdge(mx, my) : -1
        if (n === f.hover && e === f.hoverEdge) return
        f.hover = n
        f.hoverEdge = e
        // The stores are notified so the hover card and the cursor can react.
        // Both writes are no-ops when the value is unchanged, and the graph UI
        // store exists precisely so this does not re-render the application.
        useGraphUi.getState().setHoverNode(n === -1 ? null : nodes[n].id)
        useGraphUi.getState().setHoverEdge(e === -1 ? null : edges[e].key)
        hostRef.current!.style.cursor = n === -1 && e === -1 ? 'default' : 'pointer'
      }

      const onLeave = () => {
        const f = frameRef.current
        if (!f) return
        f.hover = -1
        f.hoverEdge = -1
        useGraphUi.getState().setHoverNode(null)
        useGraphUi.getState().setHoverEdge(null)
      }

      const onClick = (ev: MouseEvent) => {
        const f = frameRef.current
        if (!f) return
        // Prefer what the overlay is currently drawing as hovered over a fresh
        // hit test: by the time the click lands the layout may have drifted
        // past the pick radius, and re-testing then misses on exactly the node
        // the user was looking at.
        const n = f.hover !== -1 ? f.hover : pickNode(...local(ev))
        if (n !== -1) {
          const node = nodes[n]
          // Ctrl/Cmd-click accumulates — this is how the Context Lens fills,
          // and how multi-selection is reached without a mode.
          if (ev.ctrlKey || ev.metaKey) toggle(node.id, 'graph')
          else {
            select(node.id, 'graph')
            useGraphUi.getState().setActiveEdge(null)
          }
          return
        }
        const e = f.hoverEdge !== -1 ? f.hoverEdge : pickEdge(...local(ev))
        if (e !== -1) {
          // Clicking a relationship opens the relationship inspector and puts
          // BOTH endpoints in context, because "why are these two connected"
          // is a question about the pair, not about the line.
          const edge = edges[e]
          useGraphUi.getState().setActiveEdge(edge.key)
          useWorkspace.getState().selectMany(
            [edge.e.source, edge.e.target], 'graph')
          return
        }
        // Clicking empty space clears the edge inspector but keeps the
        // selection: losing what you are holding by missing a node is the
        // most irritating thing a canvas can do.
        useGraphUi.getState().setActiveEdge(null)
      }

      // Double-click enters Focus Mode: the canvas reorganises around the
      // object and the breadcrumb records how we got here. The event is then
      // claimed outright — d3-zoom binds dblclick-to-zoom on the canvas, and
      // letting both run means the camera lurches at the same moment the graph
      // is replaced.
      const onDblClick = (ev: MouseEvent) => {
        const f = frameRef.current
        const n = f && f.hover !== -1 ? f.hover : pickNode(...local(ev))
        if (n === -1) return
        ev.preventDefault()
        ev.stopPropagation()
        useGraphUi.getState().setFocusMode(true)
        void focusOn(nodes[n].o)
      }

      // CAPTURE phase, deliberately. cosmos.gl drives pan/zoom with d3-zoom,
      // which calls stopPropagation on the canvas's own pointer handlers — a
      // bubble-phase listener on the container never sees the click at all.
      host.addEventListener('mousemove', onMove, true)
      host.addEventListener('mouseleave', onLeave, true)
      host.addEventListener('click', onClick, true)
      host.addEventListener('dblclick', onDblClick, true)
      cleanupPick = () => {
        host.removeEventListener('mousemove', onMove, true)
        host.removeEventListener('mouseleave', onLeave, true)
        host.removeEventListener('click', onClick, true)
        host.removeEventListener('dblclick', onDblClick, true)
      }

      // Seed on a ring around the CENTRE OF THE SPACE. cosmos.gl's coordinate
      // space runs 0..spaceSize, so seeding around 0,0 puts every point in the
      // far corner and the camera frames empty space. Radius is deliberately
      // close to the settled size linkDistance implies — seed far outside it
      // and every commit is followed by a long visible collapse inward.
      //
      // Seeded by community when clustering, so the cluster force starts from
      // groups that are already roughly apart rather than untangling them.
      const CENTRE = 4096 / 2
      const seed = new Float32Array(nodes.length * 2)
      const spread = linkDistance * 2.1
      for (let i = 0; i < nodes.length; i++) {
        const base = clustered
          ? ((nodes[i].o.community ?? 0) * 2.39996) // golden angle: even spread
          : (i / Math.max(1, nodes.length)) * Math.PI * 2
        const a = base + Math.random() * 0.7
        const r = spread * 0.66 + Math.random() * spread * 0.5
        seed[i * 2] = CENTRE + Math.cos(a) * r
        seed[i * 2 + 1] = CENTRE + Math.sin(a) * r
      }
      G.setPointPositions(seed)

      // render(alpha) / start(alpha) take a simulation energy. Never pause()
      // or start(0): both stop the frame loop that maintains the camera
      // transform, and the overlay would then project labels at a camera the
      // points are no longer drawn at. Cool it, don't freeze it.
      G.render(1)
      G.start(1)
      // Fit repeatedly while the layout cools, not once. A single fit frames
      // whatever size the graph happened to be at that instant and the springs
      // then keep expanding past it — which is how the old view ended up
      // drawing its graph in a quarter of the canvas with the rest empty.
      // The schedule tracks the decay curve: dense early, then stops, because
      // a camera that keeps moving after the reader has started panning is
      // worse than one that is slightly loose. The last fit lands just after
      // the simulation reaches its floor (see simulationDecay above), so the
      // final framing is of a layout that has stopped moving.
      const settleMs = (clustered ? 620 : 430) * (1000 / 60)
      const fits = [450, 1200, 2600, settleMs + 250].map((ms) => window.setTimeout(
        () => { try { G.fitView(560, 0.2) } catch { /* gone */ } }, ms))
      timers.push(...fits)

      frameRef.current = {
        G, sx: new Float32Array(nodes.length * 2), hover: -1, hoverEdge: -1, zoom: 1,
      }

      onReady({
        zoomBy: (factor) => {
          try {
            const z = G.getZoomLevel?.() ?? 1
            G.setZoomLevel(z * factor, 260)
          } catch { /* renderer gone */ }
        },
        fit: () => { try { G.fitView(500, 0.24) } catch { /* gone */ } },
        fitTo: (ids) => {
          const idx = ids.map((id) => model.byId.get(id)?.i)
            .filter((i): i is number => i !== undefined)
          // Fewer than two points have no extent to fit, and the renderer
          // answers a zero-extent request with maximum zoom. Framing the whole
          // graph is the honest fallback: it is not what was asked for, but it
          // leaves the reader somewhere they can see.
          if (idx.length < 2) { try { G.fitView(500, 0.24) } catch { /* gone */ } return }
          try { G.fitViewByPointIndices(idx, 520, 0.32) }
          catch { try { G.fitView(500, 0.24) } catch { /* gone */ } }
        },
      })

      // Debug handle, same convention as TERRA's __omxTerraX.__gr. The graph is
      // the one part of this app that cannot be inspected from the DOM.
      ;(window as any).__omxGraph = { G, model, pickNode, pickEdge }
    }).catch((e) => onError(String(e?.message || e)))

    return () => {
      disposed = true
      for (const t of timers) window.clearTimeout(t)
      cleanupPick?.()
      onReady(null)
      try { frameRef.current?.G?.destroy?.() } catch { /* already gone */ }
      frameRef.current = null
    }
  }, [model, clustered, select, toggle, focusOn, onReady, onError])

  // -- overlay: edges, rings, labels --------------------------------------
  // One requestAnimationFrame loop, and it reads the stores with getState()
  // rather than subscribing. That is the whole re-render story: selecting,
  // hovering, focusing and filtering all change what is drawn on the next
  // frame without React rendering anything at all.
  useEffect(() => {
    if (!model.nodes.length) return
    const canvas = overlayRef.current
    const host = hostRef.current
    if (!canvas || !host) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const nodes = model.nodes
    const edges = model.edges
    const drawEdgesHere = edges.length <= EDGE_DRAW_LIMIT
    const drawNodesHere = nodes.length <= NODE_DRAW_LIMIT

    // Silence the GPU link pass by zeroing its alpha rather than flipping
    // `renderLinks`, which would need setConfig and rebuild the simulation.
    let linkColorsApplied = false

    let raf = 0
    let lastAnchors = ''
    let tiers: Map<string, 0 | 1 | 2> = new Map()
    // Find matches, recomputed only when the query changes. Scanning 600 names
    // per frame would be waste; the query changes at typing speed.
    let lastQuery = ' '
    let matchSet: Set<string> | null = null

    const draw = () => {
      raf = requestAnimationFrame(draw)
      const f = frameRef.current
      if (!f) return
      const G = f.G

      let pos: Float32Array | null = null
      // Read positions every frame rather than caching from a tick callback:
      // the layout keeps moving while it cools, and a stale cache makes labels
      // drift off their points.
      try { pos = G.getPointPositions() } catch { return }
      if (!pos) return

      const ui = useGraphUi.getState()
      const ws = useWorkspace.getState()
      const ink = inkNow()

      if (!linkColorsApplied && edges.length) {
        // Class colour and certainty alpha on the GPU pass. When we draw the
        // edges ourselves these all go to zero alpha instead.
        const lc = new Float32Array(edges.length * 4)
        for (let i = 0; i < edges.length; i++) {
          if (drawEdgesHere) { lc[i * 4 + 3] = 0; continue }
          const [r, g, b] = hexToRgb01(edges[i].color)
          lc[i * 4] = r; lc[i * 4 + 1] = g; lc[i * 4 + 2] = b
          lc[i * 4 + 3] = edges[i].certainty === 'weak' ? 0.13
            : edges[i].certainty === 'inferred' ? 0.24 : 0.4
        }
        try { G.setLinkColors(lc); linkColorsApplied = true } catch { /* retry next frame */ }
      }

      const dpr = window.devicePixelRatio || 1
      const w = host.clientWidth
      const h = host.clientHeight
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr; canvas.height = h * dpr
        canvas.style.width = `${w}px`; canvas.style.height = `${h}px`
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)

      // ---- project once ---------------------------------------------------
      // cosmos's projection is a uniform scale plus a translation, so two
      // reference points recover it exactly. Deriving the transform costs two
      // calls a frame instead of one per node, which is what makes a 400-node
      // graph cost the same to draw as a 40-node one.
      let ox = 0, oy = 0, kx = 1, ky = 1
      try {
        const p0 = G.spaceToScreenPosition([0, 0])
        const p1 = G.spaceToScreenPosition([1000, 1000])
        ox = p0[0]; oy = p0[1]
        kx = (p1[0] - p0[0]) / 1000
        ky = (p1[1] - p0[1]) / 1000
      } catch { return }
      if (!Number.isFinite(kx) || !Number.isFinite(ky) || kx === 0) return

      const sx = f.sx
      for (let i = 0; i < nodes.length; i++) {
        sx[i * 2] = ox + pos[i * 2] * kx
        sx[i * 2 + 1] = oy + pos[i * 2 + 1] * ky
      }
      f.zoom = (() => { try { return G.getZoomLevel?.() ?? 1 } catch { return 1 } })()

      // ---- emphasis sets ---------------------------------------------------
      const selected = ws.selected
      const primary = ws.primary
      const hoverIdx = f.hover
      const hoverId = hoverIdx === -1 ? null : nodes[hoverIdx].id

      // Focus Mode anchors on the selection; hover borrows the same machinery
      // for a transient version of it. Recomputed only when the anchor set
      // actually changes, because a breadth-first pass every frame is waste.
      const anchors = ui.focusMode && selected.length ? selected
        : hoverId ? [hoverId] : []
      const key = anchors.join(',')
      if (key !== lastAnchors) {
        lastAnchors = key
        tiers = anchors.length
          ? focusTiers(model, anchors) as Map<string, 0 | 1 | 2>
          : new Map()
      }
      const isolating = anchors.length > 0
      const tierOf = (id: string): 0 | 1 | 2 => isolating ? (tiers.get(id) ?? 2) : 0

      const pathSet = ui.path?.length ? new Set(ui.path) : null
      const selSet = new Set(selected)
      const activeEdge = ui.activeEdge

      // ---- find -------------------------------------------------------------
      // A live query re-weights the whole drawing rather than filtering it:
      // matches keep their labels and everything else drops back far enough to
      // read as context. Filtering would remove the surroundings that tell you
      // WHICH "Bank" you found, which is the question the reader is actually
      // asking when they search a graph.
      const q = ui.findOpen ? ui.query.trim().toLowerCase() : ''
      if (q !== lastQuery) {
        lastQuery = q
        if (!q) matchSet = null
        else {
          matchSet = new Set<string>()
          for (const n of nodes) {
            if (n.o.name.toLowerCase().includes(q)) matchSet.add(n.id)
          }
        }
      }
      const searching = !!matchSet && matchSet.size > 0
      const isMatch = (id: string) => !matchSet || matchSet.has(id)
      // Everything that is not a match fades to context while a search is live.
      const findAlpha = (id: string) => (searching && !matchSet!.has(id)) ? 0.12 : 1

      // ---- edges ------------------------------------------------------------
      const drawEdge = (e: RenderEdge, alphaIn: number, width: number, color: string, dash: number[]) => {
        let alpha = alphaIn
        if (searching && !(isMatch(e.e.source) || isMatch(e.e.target))) alpha *= 0.16
        const x1 = sx[e.a * 2], y1 = sx[e.a * 2 + 1]
        const x2 = sx[e.b * 2], y2 = sx[e.b * 2 + 1]
        // Off-screen on both ends: nothing to draw. Cheap and it matters —
        // most of a zoomed-in graph is outside the viewport.
        if ((x1 < -40 && x2 < -40) || (x1 > w + 40 && x2 > w + 40)) return
        if ((y1 < -40 && y2 < -40) || (y1 > h + 40 && y2 > h + 40)) return
        ctx.save()
        ctx.globalAlpha = alpha
        ctx.strokeStyle = color
        ctx.lineWidth = width
        ctx.setLineDash(dash)
        ctx.beginPath()
        ctx.moveTo(x1, y1)
        ctx.lineTo(x2, y2)
        ctx.stroke()
        ctx.setLineDash([])

        // Direction. Drawn as a chevron short of the target rather than as a
        // filled head on the endpoint, so it stays visible where the line
        // meets the node instead of disappearing under it. Symmetric
        // relations get none: an arrow on "allied with" is a false claim.
        if (!e.symmetric && alpha > 0.25) {
          const fx = sx[e.from * 2], fy = sx[e.from * 2 + 1]
          const tx = sx[e.to * 2], ty = sx[e.to * 2 + 1]
          const dx = tx - fx, dy = ty - fy
          const len = Math.hypot(dx, dy)
          if (len > 26) {
            const ux = dx / len, uy = dy / len
            const target = model.nodes[e.to]
            const back = (target?.size ?? 6) + 7
            const hx = tx - ux * back, hy = ty - uy * back
            const s = 4.4
            ctx.beginPath()
            ctx.moveTo(hx - ux * s + -uy * s * 0.62, hy - uy * s + ux * s * 0.62)
            ctx.lineTo(hx, hy)
            ctx.lineTo(hx - ux * s - -uy * s * 0.62, hy - uy * s - ux * s * 0.62)
            ctx.stroke()
          }
        }
        ctx.restore()
      }

      if (drawEdgesHere) {
        for (const e of edges) {
          const t = Math.max(tierOf(e.e.source), tierOf(e.e.target)) as 0 | 1 | 2
          const onPath = pathSet?.has(e.e.source) && pathSet?.has(e.e.target)
          const isActive = e.key === activeEdge
          const isHovered = f.hoverEdge !== -1 && edges[f.hoverEdge] === e
          const touchesSelection = selSet.has(e.e.source) || selSet.has(e.e.target)

          let alpha = FOCUS_EDGE_ALPHA[t] * (e.certainty === 'weak' ? 0.5 : 1)
          let width = 0.7 + e.strength * 1.9
          let color = e.color
          if (touchesSelection) { alpha = Math.max(alpha, 0.62); width += 0.35 }
          if (onPath) { alpha = 0.95; width += 1.1; color = 'var(--path)' }
          if (isActive || isHovered) { alpha = 1; width += 1 }

          drawEdge(
            e, alpha, width,
            color === 'var(--path)' ? ink.aBright : color,
            DASH[e.certainty],
          )
        }
      } else {
        // GPU is drawing the bulk. We draw only what is under emphasis, so the
        // relationship the reader is actually interrogating still gets its
        // dash pattern, its arrow and its full contrast.
        for (const e of edges) {
          const isActive = e.key === activeEdge
          const isHovered = f.hoverEdge !== -1 && edges[f.hoverEdge] === e
          const touchesSelection = selSet.has(e.e.source) || selSet.has(e.e.target)
          const onPath = pathSet?.has(e.e.source) && pathSet?.has(e.e.target)
          if (!isActive && !isHovered && !touchesSelection && !onPath) continue
          drawEdge(
            e, onPath ? 0.95 : 0.8, 1 + e.strength * 2 + (onPath ? 1 : 0),
            onPath ? ink.aBright : e.color, DASH[e.certainty],
          )
        }
      }

      // ---- nodes ------------------------------------------------------------
      // Shape says WHAT it is, size says how central, outline says how well
      // attested, opacity says how relevant to what you are looking at. Four
      // channels, none of them colour, so colour is free to mean type alone.
      if (drawNodesHere) {
        for (const n of nodes) {
          const t = tierOf(n.id)
          const isSel = selSet.has(n.id)
          const onPath = !!pathSet?.has(n.id)
          const x = sx[n.i * 2], y = sx[n.i * 2 + 1]
          if (x < -40 || x > w + 40 || y < -40 || y > h + 40) continue

          const prov = n.o.provenance === 'ai_inferred' ? 0.62
            : n.o.provenance === 'source_backed' ? 0.86 : 1
          const alpha = ((isSel || onPath) ? 1 : prov * FOCUS_ALPHA[t]) * findAlpha(n.id)
          if (alpha < 0.04) continue

          const r = n.size + (isSel ? 2.5 : 0)
          ctx.save()
          ctx.globalAlpha = alpha
          ctx.beginPath()
          shapePath(ctx, n.shape, x, y, r)
          ctx.fillStyle = n.color
          ctx.fill()

          // Outline carries provenance as a second, non-colour channel: an
          // inferred object is drawn with a broken edge, so it reads as
          // provisional even in greyscale.
          ctx.lineWidth = 1
          ctx.setLineDash(n.o.provenance === 'ai_inferred' ? [2.5, 2.5] : [])
          ctx.strokeStyle = ink.nodeEdge
          ctx.stroke()
          ctx.setLineDash([])

          // A search hit is ringed in the accent. It is the one emphasis that
          // outranks the ontology's own ring, because while a query is live
          // "did this match" is the only question on screen.
          if (searching && matchSet!.has(n.id)) {
            ctx.beginPath()
            ctx.arc(x, y, r + 4.5, 0, Math.PI * 2)
            ctx.strokeStyle = ink.a
            ctx.globalAlpha = 1
            ctx.lineWidth = 1.8
            ctx.stroke()
            ctx.globalAlpha = alpha
          }

          // The ontology marks principal actors — countries, conflicts — with
          // a ring. Kept, because it survives at sizes where shape does not.
          if (n.ring) {
            ctx.beginPath()
            ctx.arc(x, y, r + 3.2, 0, Math.PI * 2)
            ctx.strokeStyle = n.color
            ctx.globalAlpha = alpha * 0.45
            ctx.lineWidth = 1
            ctx.stroke()
          }
          ctx.restore()
        }
      }

      // ---- selection rings --------------------------------------------------
      // A ring plus a size step, never a colour swap: colour already carries
      // object type, and overloading it makes both readings unreliable.
      for (const id of selected) {
        const n = model.byId.get(id)
        if (!n) continue
        const x = sx[n.i * 2], y = sx[n.i * 2 + 1]
        const isPrimary = id === primary
        const r = n.size + (isPrimary ? 9 : 6)
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.strokeStyle = isPrimary ? ink.a : ink.accentAt(0.5)
        ctx.lineWidth = isPrimary ? 1.8 : 1.1
        ctx.stroke()
        // The anchor gets a second, wider ring at low opacity. That is the
        // whole "clearly becomes the visual anchor" requirement: two rings
        // read as deliberate at a glance, one reads as a hover state.
        if (isPrimary) {
          ctx.beginPath()
          ctx.arc(x, y, r + 5.5, 0, Math.PI * 2)
          ctx.strokeStyle = ink.accentAt(0.22)
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }

      // Hovered node gets a thin ring too, so the hover card is anchored to
      // something visible rather than floating unattached.
      if (hoverIdx !== -1) {
        const n = nodes[hoverIdx]
        ctx.beginPath()
        ctx.arc(sx[n.i * 2], sx[n.i * 2 + 1], n.size + 5, 0, Math.PI * 2)
        ctx.strokeStyle = ink.hoverRing
        ctx.lineWidth = 1
        ctx.stroke()
      }

      // ---- labels -----------------------------------------------------------
      // Label budget scales with zoom. This single rule is the difference
      // between a readable graph and a wall of overlapping text.
      // In Clusters mode the group names are the reading, so individual names
      // step back — two competing label layers at 10px is what turns a useful
      // grouping into noise. Selected and hovered nodes are forced through
      // regardless, so nothing the reader asked about is ever lost.
      const budget = clustered
        ? Math.max(3, Math.round(f.zoom * 4))
        : Math.max(8, Math.min(48, Math.round(8 + f.zoom * 14)))
      let drawn = 0
      ctx.textAlign = 'center'

      // Occupancy for label placement. Two labels overlapping is worse than one
      // missing label: the overlap makes BOTH unreadable, and the reader cannot
      // tell which node either belongs to. Importance order means the label that
      // survives a collision is the one worth keeping.
      //
      // NODES ARE SEEDED INTO THIS SET, not just labels. Testing labels against
      // each other alone was the single biggest cause of the canvas reading as
      // noise: a name would clear every other name and then land squarely on
      // top of an unrelated node, so the dot lost its shape and the text lost
      // its plate. Every visible node reserves its own footprint first, and
      // names go in the gaps.
const occ = new Occupancy()

      for (const n of nodes) {
        const x = sx[n.i * 2], y = sx[n.i * 2 + 1]
        if (x < -40 || x > w + 40 || y < -40 || y > h + 40) continue
        // The ring, where an ontology principal has one, is part of the mark.
        occ.mark(x, y, n.size + (n.ring ? 3.2 : 0))
      }

      // Importance order, with anything the reader has asked about first.
      const ranked = model.labelOrder
      for (const n of ranked) {
        const isSel = selSet.has(n.id)
        const isHover = hoverIdx === n.i
        const onPath = !!pathSet?.has(n.id)
        // A match is forced through the budget. A search that highlights a node
        // but withholds its name has not answered the question.
        const hit = searching && matchSet!.has(n.id)
        const forced = isSel || isHover || onPath || hit
        if (drawn >= budget && !forced) continue
        if (searching && !hit && !isSel && !isHover) continue

        const t = tierOf(n.id)
        // In focus mode the far field loses its labels entirely rather than
        // fading them: 12%-opacity text is not readable, it is just texture.
        if (isolating && t === 2 && !forced) continue

        const x = sx[n.i * 2], y = sx[n.i * 2 + 1]
        if (x < -60 || x > w + 60 || y < -30 || y > h + 30) continue

        const isAnchor = n.id === primary
        ctx.font = isAnchor
          ? '600 12px ui-monospace, SFMono-Regular, Menlo, monospace'
          : '11px ui-monospace, SFMono-Regular, Menlo, monospace'

        const label = n.o.name.length > 26 ? `${n.o.name.slice(0, 25)}…` : n.o.name
        const tw = ctx.measureText(label).width

        // `top` is the plate's top edge; textBox thinks in centres.
        const boxAt = (topY: number) => textBox(x, topY + 7, tw, 14, 'middle', 4)
        const below = y + n.size + 3
        let top = below

        // Selected, hovered and on-path names are pinned below their node and
        // drawn whatever else is there: the reader asked for them, and moving
        // them around as the layout breathes is worse than a rare overlap.
        // The anchor is pinned for a second reason -- it carries a type line
        // underneath, and flipping the pair upward would put that line back on
        // top of the very node it names.
        if (forced || isAnchor) {
          occ.add(boxAt(below))
        } else {
          // Below, then above. A second candidate costs one more rectangle test
          // and roughly doubles how many names survive a dense field; without
          // it a node loses its label to a neighbour that happens to sit 12px
          // lower, which is not a judgement about importance, only about which
          // way the layout drifted.
          const above = y - n.size - 17
          const i = occ.firstFit([boxAt(below), boxAt(above)])
          if (i < 0) continue
          top = i === 0 ? below : above
        }
        const box = boxAt(top)

        const alpha = forced ? 1 : t === 1 ? 0.6 : 0.85
        // A plate behind the text rather than a stroke around it: a stroked
        // glyph on a dark ground thickens the letterforms and the whole canvas
        // starts to look like a game HUD.
        ctx.fillStyle = forced ? ink.plate : ink.platePlain
        ctx.fillRect(box.x0, box.y0, box.x1 - box.x0, 14)
        ctx.fillStyle = isAnchor ? ink.strong(alpha)
          : isSel ? ink.normal(alpha) : ink.faint(alpha)
        ctx.fillText(label, x, top + 11)

        // The anchor alone carries a second line: its type. Everything else
        // the reader wants about it is in the inspector, and repeating it on
        // the canvas is what turns a graph into a spreadsheet.
        if (isAnchor) {
          ctx.font = '9.5px ui-monospace, SFMono-Regular, Menlo, monospace'
          const sub = n.o.typeLabel.toUpperCase()
          const sw = ctx.measureText(sub).width
          ctx.fillStyle = ink.plate
          ctx.fillRect(x - sw / 2 - 4, top + 14, sw + 8, 12)
          ctx.fillStyle = ink.accentAt(0.9)
          ctx.fillText(sub, x, top + 23)
          occ.add(textBox(x, top + 20, sw, 12, 'middle', 4))
        }
        drawn++
      }

      // ---- hovered relationship ---------------------------------------------
      // The single most important thing the old canvas could not do: say what
      // a line MEANS without opening anything. Only ever one at a time, and
      // only on hover, because permanent edge labels destroy readability.
      if (f.hoverEdge !== -1 && edges[f.hoverEdge]) {
        const e = edges[f.hoverEdge]
        const mx = (sx[e.a * 2] + sx[e.b * 2]) / 2
        const my = (sx[e.a * 2 + 1] + sx[e.b * 2 + 1]) / 2
        const arrow = e.symmetric ? '↔' : '→'
        const text = `${e.label.toUpperCase()} ${arrow}`
        ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
        const tw = ctx.measureText(text).width
        ctx.fillStyle = ink.plate
        ctx.fillRect(mx - tw / 2 - 6, my - 8, tw + 12, 16)
        ctx.strokeStyle = e.color
        ctx.lineWidth = 1
        ctx.strokeRect(mx - tw / 2 - 6, my - 8, tw + 12, 16)
        ctx.fillStyle = ink.strong(0.95)
        ctx.fillText(text, mx, my + 3.5)
      }

      // ---- community names --------------------------------------------------
      // A cluster the reader cannot name is a blob. The backend already names
      // each community by its most important members ("Russia · Ukraine ·
      // Black Sea"), so the label is real rather than a made-up heading, and
      // it is drawn at the group's live centroid so it tracks the layout.
      if (clustered && communities.length) {
        ctx.textAlign = 'center'
        ctx.font = '600 10px ui-monospace, SFMono-Regular, Menlo, monospace'
        for (const c of communities) {
          let cx = 0, cy = 0, n = 0
          for (const id of c.members) {
            const rn = model.byId.get(id)
            if (!rn) continue
            cx += sx[rn.i * 2]; cy += sx[rn.i * 2 + 1]; n++
          }
          // Two members is not a community worth naming, and a label for one
          // would sit on top of the node it duplicates.
          if (n < 3) continue
          cx /= n; cy /= n
          if (cx < 0 || cx > w || cy < 0 || cy > h) continue
          // First two names carry the group; the rest is a count, because a
          // three-name label at 10px is wider than the cluster it sits over.
          const parts = (c.label || '').split(' · ').filter(Boolean)
          const name = (parts.slice(0, 2).join(' · ') || `Group ${c.id}`).toUpperCase()
          const text = `${name}  ${c.size}`
          const tw = ctx.measureText(text).width
          ctx.fillStyle = ink.plate
          ctx.fillRect(cx - tw / 2 - 7, cy - 8, tw + 14, 16)
          ctx.strokeStyle = ink.panelLine
          ctx.lineWidth = 1
          ctx.strokeRect(cx - tw / 2 - 7, cy - 8, tw + 14, 16)
          ctx.fillStyle = ink.accentAt(0.92)
          ctx.fillText(text, cx, cy + 3.5)
        }
      }

      // ---- minimap ----------------------------------------------------------
      if (ui.minimap && nodes.length >= MINIMAP_THRESHOLD) {
        drawMinimap(ctx, w, h, pos, nodes, selSet, primary, ox, oy, kx, ky, ink)
      }
    }

    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [model, clustered, communities])

  return (
    <>
      <div ref={hostRef} style={{ position: 'absolute', inset: 0 }} />
      <canvas
        ref={overlayRef}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
      />
    </>
  )
}

/** A quiet map of where everything is and which part of it you are looking at.
 *
 *  Drawn into the same overlay rather than as its own element: it needs the
 *  simulation's live positions, and a second canvas would need its own copy of
 *  the projection. It is deliberately almost monochrome — it orients, it does
 *  not compete with the graph. */
function drawMinimap(
  ctx: CanvasRenderingContext2D, w: number, h: number,
  pos: Float32Array, nodes: GraphModel['nodes'],
  selected: Set<string>, primary: string | null,
  ox: number, oy: number, kx: number, ky: number, ink: Ink,
) {
  // Sits above where the shell floats the action bar. A fixed offset rather
  // than one that reacts to the selection: a minimap that jumps 80px whenever
  // you click a node is worse than one that is slightly high when nothing is
  // held, because the reader uses its position to find it.
  const MW = 150, MH = 104, PAD = 14, BOTTOM = 92
  const x0 = w - MW - PAD, y0 = h - MH - BOTTOM

  // Bounds of the layout in simulation space.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (let i = 0; i < nodes.length; i++) {
    const x = pos[i * 2], y = pos[i * 2 + 1]
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const spanX = Math.max(1, maxX - minX), spanY = Math.max(1, maxY - minY)
  const s = Math.min((MW - 12) / spanX, (MH - 12) / spanY)
  const mx = (x: number) => x0 + 6 + (x - minX) * s
  const my = (y: number) => y0 + 6 + (y - minY) * s

  ctx.save()
  ctx.fillStyle = ink.plate
  ctx.strokeStyle = ink.panelLine
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.rect(x0, y0, MW, MH)
  ctx.fill()
  ctx.stroke()

  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i]
    const isSel = selected.has(n.id)
    ctx.fillStyle = n.id === primary ? ink.a
      : isSel ? ink.accentAt(0.75)
        : ink.minimapDot
    const r = n.id === primary ? 2.4 : isSel ? 1.8 : 1
    ctx.beginPath()
    ctx.arc(mx(pos[i * 2]), my(pos[i * 2 + 1]), r, 0, Math.PI * 2)
    ctx.fill()
  }

  // The viewport, expressed back in simulation space through the inverse of
  // the projection we already derived.
  const vx0 = mx((0 - ox) / kx), vy0 = my((0 - oy) / ky)
  const vx1 = mx((w - ox) / kx), vy1 = my((h - oy) / ky)
  ctx.strokeStyle = ink.hoverRing
  ctx.lineWidth = 1
  ctx.strokeRect(
    Math.max(x0 + 1, Math.min(vx0, vx1)),
    Math.max(y0 + 1, Math.min(vy0, vy1)),
    Math.min(MW - 2, Math.abs(vx1 - vx0)),
    Math.min(MH - 2, Math.abs(vy1 - vy0)),
  )
  ctx.restore()
}
