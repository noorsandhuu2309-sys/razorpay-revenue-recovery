// The graph surface: three ways of reading one subgraph, over one selection.
//
// This file is the orchestrator, not the renderer. It owns the pipeline —
//
//     payload → trust lens → canvas filters → render model → canvas
//
// — plus the chrome around the canvas and the empty states. The drawing lives
// in NetworkCanvas (GPU + overlay) and Orbit (SVG); the visual grammar lives in
// lib/graphModel. Keeping those apart is what stops a hover from re-running a
// filter and a filter from rebuilding a simulation.
//
// The three modes answer three different questions, which is the only defence
// against a mode existing because it looked good in a demo:
//
//   ORBIT     what is attached to THIS, and how?      one object at the centre
//   NETWORK   what shape is all of this?              force-directed
//   CLUSTERS  what groups exist here?                 community-seeded force
//
// Selection is shared with the rest of the workspace and is never duplicated
// here. Focus Mode is a lens over the current canvas and is deliberately NOT
// the same thing as the workspace's focus trail, which is navigation: switching
// the lens off must not lose your place, and drilling in must not silently
// change what you are holding.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { passesLens, useWorkspace } from '../store/workspace'
import { filtersActive, useGraphUi } from '../store/graphUi'
import { buildModel, CLASS_COLOR, CLASS_LABEL, relClass } from '../lib/graphModel'
import { NetworkCanvas, type NetworkHandle } from './NetworkCanvas'
import { Orbit } from './Orbit'
import { GraphFilters, GraphToolbar } from '../components/GraphToolbar'
import { GraphFind } from '../components/GraphFind'
import {
  IconChevron, IconClusters, IconEmptyObject, IconFilter, IconFullscreenExit,
  IconNetwork, IconOrbit, IconWarning,
} from '../components/Icons'
import type { GraphPayload } from '../lib/types'
// Imported here rather than from the shell so the graph's own stylesheet
// travels with the view that needs it. workspace.css still owns the tokens.
import '../graph.css'

const DAY = 86400000

/** A stable empty array. A fresh `[]` in the prop would give the canvas a new
 *  identity every render and restart its frame loop on every keystroke
 *  anywhere in the app. */
const EMPTY_COMMUNITIES: GraphPayload['communities'] = []

export function GraphView() {
  const rawGraph = useWorkspace((s) => s.graph)
  const floor = useWorkspace((s) => s.provenanceFloor)
  const loading = useWorkspace((s) => s.graphLoading)
  const selected = useWorkspace((s) => s.selected)
  const primary = useWorkspace((s) => s.primary)
  const ontology = useWorkspace((s) => s.ontology)
  const select = useWorkspace((s) => s.select)

  const mode = useGraphUi((s) => s.mode)
  const setMode = useGraphUi((s) => s.setMode)
  const layout = useGraphUi((s) => s.layout)
  const filters = useGraphUi((s) => s.filters)
  const focusMode = useGraphUi((s) => s.focusMode)
  const setFocusMode = useGraphUi((s) => s.setFocusMode)
  const fullscreen = useGraphUi((s) => s.fullscreen)
  const setFullscreen = useGraphUi((s) => s.setFullscreen)
  const setFiltersOpen = useGraphUi((s) => s.setFiltersOpen)
  const path = useGraphUi((s) => s.path)
  const pathLabel = useGraphUi((s) => s.pathLabel)
  const setPath = useGraphUi((s) => s.setPath)
  const legendOpen = useGraphUi((s) => s.legendOpen)
  const toggleLegend = useGraphUi((s) => s.toggleLegend)
  const setFindOpen = useGraphUi((s) => s.setFindOpen)

  // The renderer failing to load is a real state and the only one the canvas
  // cannot recover from on its own, so it is surfaced rather than swallowed.
  const [err, setErr] = useState('')
  // One camera slot, filled by whichever mode is mounted. Orbit and the network
  // canvas expose the same three verbs, so the toolbar drives both without
  // knowing which is showing — and Orbit's zoom is no longer dead.
  const handleRef = useRef<NetworkHandle | null>(null)
  const onReady = useCallback((h: NetworkHandle | null) => { handleRef.current = h }, [])

  // -- pipeline ----------------------------------------------------------
  // Both filter stages run BEFORE the simulation is built rather than dimming
  // afterwards: a node the user has excluded should not pull on the layout, or
  // the picture keeps a shape driven by evidence they said they did not want to
  // see. Edges whose endpoints are gone go with them, since cosmos.gl would
  // otherwise index past the end of the node array.
  const graph: GraphPayload | null = useMemo(() => {
    if (!rawGraph) return null
    const cutoff = filters.recencyDays === null
      ? null : Date.now() - filters.recencyDays * DAY

    const nodes = rawGraph.nodes.filter((n) => {
      if (!passesLens(n, floor)) return false
      if (filters.hiddenFamilies.includes(n.family)) return false
      if (filters.trackedOnly && !n.tracked) return false
      if (filters.evidenceOnly && n.provenance === 'ai_inferred') return false
      if (cutoff !== null) {
        const t = new Date(n.lastSeen).getTime()
        if (Number.isFinite(t) && t < cutoff) return false
      }
      // A root the user explicitly navigated to is never filtered away — a
      // canvas that hides the thing you just selected reads as broken, and the
      // filter chip is right there to explain the rest.
      return true
    })

    const keep = new Set(nodes.map((n) => n.id))
    const edges = rawGraph.edges.filter((e) => {
      if (!keep.has(e.source) || !keep.has(e.target)) return false
      if (!passesLens(e, floor)) return false
      if (filters.hiddenClasses.includes(relClass(e.relation))) return false
      if (filters.minStrength > 0) {
        const s = Math.min(1, Math.log1p(Math.max(0, e.weight || 0)) / Math.log1p(8))
        if (s < filters.minStrength) return false
      }
      return true
    })

    return { ...rawGraph, nodes, edges }
  }, [rawGraph, floor, filters])

  /** Symmetry is a property of the relation TYPE and the ontology is the only
   *  authority on it. It cannot be inferred from the payload: edges are
   *  deduplicated on an unordered {pair, relation} key, so a reciprocal edge is
   *  never present to detect. The ontology lands after the first paint, which
   *  is why this is a dependency rather than a default — without it every
   *  mutual relation renders with a one-way arrow, forever. */
  const symmetricRelations = useMemo(() => {
    const set = new Set<string>()
    for (const r of ontology?.relations ?? []) if (r.symmetric) set.add(r.key)
    return set
  }, [ontology])

  const model = useMemo(
    () => graph ? buildModel(graph, symmetricRelations) : null,
    [graph, symmetricRelations])

  const hiddenCount = (rawGraph?.nodes.length ?? 0) - (graph?.nodes.length ?? 0)

  // Where the orbit is centred. Kept here rather than in a store because it is
  // a property of this view's camera, not of the workspace selection —
  // recentring must not clear what the user is holding.
  const [orbitCentre, setOrbitCentre] = useState<string | null>(null)
  const [orbitTrail, setOrbitTrail] = useState<string[]>([])

  // The orbit needs a centre before the user has picked one. Falling back to
  // the most important node opens on the most connected thing in the Space,
  // which is nearly always the right first question.
  const centreId = useMemo(() => {
    if (!model?.nodes.length) return null
    const wanted = orbitCentre ?? primary ?? selected[0]
    if (wanted && model.byId.has(wanted)) return wanted
    return model.labelOrder[0]?.id ?? null
  }, [model, orbitCentre, primary, selected])

  // Selecting elsewhere in the workspace should move the orbit, or the two
  // surfaces disagree about what is being looked at. Recentring here does not
  // push onto the trail: the user did not navigate, something else did.
  useEffect(() => {
    if (primary && model?.byId.has(primary)) setOrbitCentre(primary)
  }, [primary, model])

  /** Recentring pushes onto the trail; revisiting something already on it pops
   *  back to that point instead, so A › B › A cannot grow a trail that lies
   *  about depth. */
  const recentre = (id: string) => {
    setOrbitCentre(id)
    setOrbitTrail((t) => {
      const at = t.indexOf(id)
      return at >= 0 ? t.slice(0, at + 1) : [...t, id]
    })
    select(id, 'graph')
  }

  // Fullscreen is applied to the BODY, because the element that has to
  // out-rank the inspector and the NOVA bar is `.omx-canvas` — a z-index set
  // inside it can never escape it. The class is removed on unmount as well as
  // on toggle: leaving the graph while fullscreen would otherwise strand every
  // other view under a fixed, empty canvas.
  useEffect(() => {
    const cls = 'omx-graph-fullscreen'
    document.body.classList.toggle(cls, fullscreen)
    return () => document.body.classList.remove(cls)
  }, [fullscreen])

  // `/` and Ctrl+F are the two gestures everyone already has for "find". Both
  // are claimed only while the graph is showing, and `/` defers to any text
  // field so it cannot swallow a slash someone is typing into NOVA.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault(); setFindOpen(true); return
      }
      if (e.key === '/' && !typing && !useWorkspace.getState().paletteOpen) {
        e.preventDefault(); setFindOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setFindOpen])

  // Escape leaves fullscreen, and claims the key while it is on. The shell
  // binds Escape to popping the focus trail; without stopping propagation both
  // would fire and the one gesture would both exit fullscreen and navigate.
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (useWorkspace.getState().paletteOpen) return
      e.preventDefault()
      e.stopPropagation()
      useGraphUi.getState().setFullscreen(false)
    }
    // Capture, so it runs before the shell's own window listener.
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [fullscreen])

  // The canvas changes size when it takes the window. cosmos reads its host's
  // bounds on resize, but the camera keeps the framing it had for the old box,
  // so the graph ends up off-centre in the new one until something refits it.
  useEffect(() => {
    if (mode === 'orbit') return
    const t = window.setTimeout(() => handleRef.current?.fit(), 260)
    return () => window.clearTimeout(t)
  }, [fullscreen, mode])

  // A new selection invalidates a highlighted path: it was an answer about the
  // pair that WAS held, and leaving it lit over a different selection is a lie.
  //
  // Tested on the ENDPOINTS only. A path's intermediate hops were never
  // selected — that is the whole point of tracing one — so requiring every
  // member to be held would clear the path on the frame it was drawn.
  useEffect(() => {
    const p = useGraphUi.getState().path
    if (!p || p.length < 2) return
    const held = new Set(selected)
    if (!held.has(p[0]) || !held.has(p[p.length - 1])) setPath(null)
  }, [selected, setPath])

  // -- empty and error states --------------------------------------------
  if (err) {
    return (
      <div className="omx-empty">
        <div className="glyph"><IconWarning size={34} /></div>
        <h3>Graph renderer unavailable</h3>
        <p>{err}</p>
      </div>
    )
  }

  if (!rawGraph || rawGraph.nodes.length === 0) {
    return (
      <div className="omx-empty">
        <div className="glyph"><IconEmptyObject size={34} /></div>
        <h3>{loading ? 'Building graph…' : 'No intelligence yet'}</h3>
        <p>
          Research a topic or import information to build your intelligence
          graph. Everything ORACLE finds becomes objects and relationships here.
        </p>
        {!loading && (
          <div className="acts">
            <button className="omx-btn" onClick={() => useWorkspace.getState().setPalette(true)}>
              Open command palette
            </button>
          </div>
        )}
      </div>
    )
  }

  // Filtered to nothing. The worst thing this view can do is show blank space
  // with no explanation, so it names the cause and offers the way out.
  if (model && model.nodes.length === 0) {
    return (
      <div className="omx-empty">
        <div className="glyph"><IconFilter size={34} /></div>
        <h3>Nothing matches the current filters</h3>
        <p>
          All {rawGraph.nodes.length} objects in this view were excluded.
          Try widening the trust lens, clearing a filter, or lowering the
          strength threshold.
        </p>
        <div className="acts">
          <button className="omx-btn" onClick={() => useGraphUi.getState().resetFilters()}>
            Clear filters
          </button>
          <button className="omx-btn" onClick={() => setFiltersOpen(true)}>
            Open filters
          </button>
        </div>
      </div>
    )
  }

  const noEdges = !!model && model.nodes.length > 1 && model.edges.length === 0

  return (
    <div className={`omx-graph-stage ${fullscreen ? 'fullscreen' : ''}`}>
      {model && (mode === 'network' || mode === 'clusters') && (
        <NetworkCanvas
          // Remounting on layout change is deliberate: the cluster force can
          // only be handed to cosmos at construction, and setConfig() would
          // rebuild the simulation anyway — with the added risk of collapsing
          // the layout into a knot if it is ever called from a frame.
          key={`${mode}-${layout}`}
          model={model}
          clustered={mode === 'clusters' || layout === 'community'}
          communities={graph?.communities ?? EMPTY_COMMUNITIES}
          onReady={onReady}
          onError={setErr}
        />
      )}
      {model && mode === 'orbit' && centreId && (
        <Orbit
          graph={graph!} centreId={centreId} onRecentre={recentre}
          onReady={onReady}
        />
      )}

      {/* The way out. Present only in fullscreen, above everything the stage
          draws, and it names the keyboard route as well — the bug this
          replaces left the reader with no visible exit at all. */}
      {fullscreen && (
        <div className="omx-graph-exit">
          <span className="omx-label">Fullscreen</span>
          <button className="omx-btn" onClick={() => setFullscreen(false)}>
            <IconFullscreenExit size={12} /> Exit
          </button>
          <kbd>Esc</kbd>
        </div>
      )}

      {/* Mode. Centred at the top because it governs the whole canvas rather
          than any one corner of it, and each tab says what question it answers
          rather than what it draws. */}
      <div className="omx-graph-modes">
        <button
          className={`omx-mode-tab ${mode === 'orbit' ? 'on' : ''}`}
          onClick={() => setMode('orbit')}
          title="What is attached to this object, and how?"
        ><IconOrbit size={13} /> Orbit</button>
        <button
          className={`omx-mode-tab ${mode === 'network' ? 'on' : ''}`}
          onClick={() => setMode('network')}
          title="What shape is the whole neighbourhood?"
        ><IconNetwork size={13} /> Network</button>
        <button
          className={`omx-mode-tab ${mode === 'clusters' ? 'on' : ''}`}
          onClick={() => setMode('clusters')}
          title="What groups exist in this Space?"
        ><IconClusters size={13} /> Clusters</button>
      </div>

      <GraphToolbar
        onZoom={(f) => handleRef.current?.zoomBy(f)}
        onFit={() => handleRef.current?.fit()}
        // Fit to the selection AND its neighbours, never to the selection
        // alone. One point has no extent, so fitting to it drives the camera
        // to maximum zoom and the reader lands on an empty field with their
        // object somewhere off the edge — which is exactly what entering Focus
        // Mode on a single node did before this.
        onFitSelection={() => {
          if (!model || !selected.length) return
          const ids = new Set(selected)
          for (const id of selected) {
            for (const nb of model.adjacency.get(id) ?? []) ids.add(nb)
          }
          handleRef.current?.fitTo([...ids])
        }}
      />

      {model && <GraphFilters model={model} hiddenCount={hiddenCount} />}
      {model && (
        <GraphFind
          model={model}
          onReveal={(ids) => handleRef.current?.fitTo(ids)}
        />
      )}

      {/* The key. Families first — that is the reader's primary question about
          a dot — then relationship classes, because a coloured line means
          nothing until you are told what the colours are. Built from what is
          actually on screen: a legend listing families with no members teaches
          the reader nothing and costs a corner of the canvas. */}
      {model && (
        <div className={`omx-graph-legend ${
          legendOpen === true ? 'open' : legendOpen === false ? 'closed' : ''}`}>
          {/* The header is the fold. On a narrow stage the key would otherwise
              sit on top of the drawing it explains — see the container query in
              graph.css — so it folds itself away and says how to get it back. */}
          <button
            className="lhead"
            onClick={toggleLegend}
            aria-expanded={legendOpen !== false}
            title={legendOpen === false ? 'Show the key' : 'Hide the key'}
          >
            <span className="omx-label">Key</span>
            <span className="chev" aria-hidden="true"><IconChevron size={10} /></span>
          </button>
          <div className="lbody">
          <div className="omx-label">Objects</div>
          <div className="items">
            {model.legend.slice(0, 8).map((f) => (
              <div className="it" key={f.family}>
                <span className="g" style={{ color: f.color }} aria-hidden="true">
                  {f.shape === 'diamond' ? '◆' : f.shape === 'square' ? '■'
                    : f.shape === 'triangle' ? '▲' : f.shape === 'hexagon' ? '⬢'
                      : f.shape === 'pentagon' ? '⬟' : '●'}
                </span>
                <span className="l">{f.label}</span>
                <span className="n omx-mono">{f.count}</span>
              </div>
            ))}
          </div>
          {model.classes.length > 0 && (
            <>
              <div className="omx-label" style={{ marginTop: 9 }}>Links</div>
              <div className="items">
                {model.classes.map(({ cls, count }) => (
                  <div className="it" key={cls}>
                    <span className="ln" style={{ background: CLASS_COLOR[cls] }} aria-hidden="true" />
                    <span className="l">{CLASS_LABEL[cls]}</span>
                    <span className="n omx-mono">{count}</span>
                  </div>
                ))}
              </div>
              {/* Line style is a second, non-colour channel and has to be
                  spelled out once or it is invisible. */}
              <div className="omx-graph-key">
                <span><i className="k solid" /> corroborated</span>
                <span><i className="k dashed" /> inferred</span>
                <span><i className="k dotted" /> incidental</span>
              </div>
            </>
          )}
          </div>
        </div>
      )}

      {/* Scale and state, in one line. `hidden` is shown because a filter that
          silently shrinks the graph is indistinguishable from an empty Space. */}
      <div className="omx-graph-stat">
        <span className="omx-label">
          {model?.nodes.length ?? 0} objects · {model?.edges.length ?? 0} links
          {hiddenCount > 0 && ` · ${hiddenCount} hidden`}
        </span>
        {focusMode && (
          <button className="omx-chip sm on" onClick={() => setFocusMode(false)}
                  title="Leave focus mode — your selection is kept">
            Focus on
          </button>
        )}
        {filtersActive(filters) && (
          <button className="omx-chip sm" onClick={() => setFiltersOpen(true)}>
            Filtered
          </button>
        )}
        {loading && <span className="omx-spin" />}
      </div>

      {/* A highlighted evidence or connection path, and how to put it away. */}
      {path && path.length > 1 && (
        <div className="omx-graph-path">
          <span className="omx-label">Path</span>
          <span className="pl">{pathLabel || `${path.length} steps`}</span>
          <button className="omx-btn icon" onClick={() => setPath(null)}
                  aria-label="Clear path">×</button>
        </div>
      )}

      {/* Connected objects but no links between them — a real state, and one
          the old view rendered as an unexplained scatter of dots. */}
      {noEdges && (
        <div className="omx-graph-note">
          <strong>No relationships between these objects.</strong>
          <span>
            They are in the Space but nothing connects them at this depth.
            Try Expand, raise density, or lower the strength threshold.
          </span>
        </div>
      )}

      {/* The orbit path. Same contract as the focus trail: every step is
          clickable, so drilling in is never a one-way door. */}
      {mode === 'orbit' && orbitTrail.length > 1 && (
        <div className="omx-orbit-path">
          <span className="omx-label">Orbit path</span>
          {orbitTrail.map((id, i) => {
            const o = model?.byId.get(id)?.o
            return (
              <span key={id} className="step">
                <button
                  className={`omx-crumb ${i === orbitTrail.length - 1 ? 'on' : ''}`}
                  onClick={() => {
                    setOrbitCentre(id)
                    setOrbitTrail((t) => t.slice(0, i + 1))
                  }}
                >
                  <span className="g" style={{ color: o?.color }}>{o?.glyph ?? '○'}</span>
                  {o?.name ?? id.slice(0, 8)}
                </button>
                {i < orbitTrail.length - 1 && <span className="sep">›</span>}
              </span>
            )
          })}
        </div>
      )}

      <HoverCard />
    </div>
  )
}

/** The hover readout, isolated in its own component on purpose.
 *
 *  Hover changes on every pointer move that crosses a node. Subscribing to it
 *  here means THIS re-renders and nothing else does — the canvas, the toolbar,
 *  the legend and the rest of the application are untouched. Putting hover in
 *  the parent is what made the old view re-render its whole canvas component
 *  on every mouse move. */
function HoverCard() {
  const hoverNode = useGraphUi((s) => s.hoverNode)
  const hoverEdge = useGraphUi((s) => s.hoverEdge)
  const graph = useWorkspace((s) => s.graph)

  if (hoverEdge && !hoverNode) {
    // The edge's own readout is drawn on the canvas at the line's midpoint,
    // where the reader is already looking. Repeating it in a corner would be
    // two labels for one thing.
    return null
  }
  if (!hoverNode) return null
  const o = graph?.nodes.find((n) => n.id === hoverNode)
  if (!o) return null

  return (
    <div className="omx-graph-hover" role="status">
      <div className="hh">
        <span className="g" style={{ color: o.color }}>{o.glyph}</span>
        <strong>{o.name}</strong>
      </div>
      <div className="omx-label">
        {o.typeLabel} · {o.provenanceLabel}
        {o.tracked && ' · tracked'}
      </div>
      {o.description && <div className="hd">{o.description.slice(0, 140)}</div>}
    </div>
  )
}
