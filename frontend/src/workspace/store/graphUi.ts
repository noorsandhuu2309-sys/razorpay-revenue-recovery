// Graph-only UI state, deliberately in its own store.
//
// This exists for one reason: hovering a node must not re-render the
// application. In the view this replaces, hover lived in `useState` inside
// GraphView, so every pointer move that crossed a node re-rendered the whole
// canvas component — and anything that subscribes to the workspace store would
// have been dragged along with it had hover lived there instead.
//
// The split is by LIFETIME, not by subject:
//
//   workspace store — what the user is holding and where they are. Survives a
//                     view switch, is read by the Inspector, NOVA, Activity.
//   this store      — how this canvas is currently drawn. Meaningless outside
//                     the graph, and cheap to throw away.
//
// Selection is NOT duplicated here. There is one selection in OMNIX and it
// lives in the workspace store; putting a second copy on the canvas is how the
// two drift apart.

import { create } from 'zustand'
import type { RelClass } from '../lib/graphModel'

export type GraphMode = 'orbit' | 'network' | 'clusters'
export type GraphLayout = 'force' | 'community'

/** How much of the Space to pull down. This is a DATA parameter — it changes
 *  what is fetched, not just what is drawn — but it is a property of the
 *  canvas rather than of the workspace, so it lives here and the fetch reads
 *  it. See `DENSITY` below for what each level actually asks for. */
export type Density = 'low' | 'medium' | 'high'

/** The request shape behind each density.
 *
 *  The view this replaces asked for `hops:1, per_node:8` and got NINE nodes out
 *  of 644 — a graph that looked like a demo because it was showing 1.4% of the
 *  Space. One hop from a single seed can never be more than `per_node + 1`
 *  nodes, so no amount of raising `max_nodes` alone would have fixed it. Hops
 *  is the lever that matters.
 *
 *  `maxNodes` is clamped at 600 server-side; asking for more is not an error,
 *  it is just ignored, so `high` sits under the ceiling on purpose. */
export const DENSITY: Record<Density, { hops: number; perNode: number; maxNodes: number; label: string; hint: string }> = {
  low: {
    hops: 2, perNode: 16, maxNodes: 60,
    label: 'Low', hint: 'Immediate neighbourhood — the strongest links only',
  },
  medium: {
    hops: 3, perNode: 22, maxNodes: 180,
    label: 'Medium', hint: 'The working picture — the Space as a map',
  },
  high: {
    hops: 4, perNode: 26, maxNodes: 500,
    label: 'High', hint: 'Everything reachable in four hops',
  },
}

export interface GraphFilters {
  /** Families the user has switched OFF. Empty means everything is shown —
   *  storing exclusions rather than inclusions means a family that arrives in
   *  the payload later (because an agent found one) is visible by default
   *  rather than silently filtered out by a stale inclusion list. */
  hiddenFamilies: string[]
  /** Relation classes switched off, same reasoning. */
  hiddenClasses: RelClass[]
  /** 0..1 floor on edge strength. */
  minStrength: number
  /** Hide edges not observed within N days. null = no temporal filter.
   *  Applies to edges only: an object has no "last observed" that means the
   *  same thing. */
  recencyDays: number | null
  /** Only objects the user is tracking. */
  trackedOnly: boolean
  /** Only objects that have evidence behind them — provenance stronger than
   *  ai_inferred. Distinct from the Trust Lens, which is a workspace-wide
   *  floor; this one is a canvas filter the user can flick on and off. */
  evidenceOnly: boolean
}

export const NO_FILTERS: GraphFilters = {
  hiddenFamilies: [],
  hiddenClasses: [],
  minStrength: 0,
  recencyDays: null,
  trackedOnly: false,
  evidenceOnly: false,
}

/** True when anything is actually being filtered. Drives the "N hidden by
 *  filters" note and the empty state — a blank canvas with no explanation is
 *  the worst thing this view can show. */
export const filtersActive = (f: GraphFilters): boolean =>
  f.hiddenFamilies.length > 0 || f.hiddenClasses.length > 0
  || f.minStrength > 0 || f.recencyDays !== null
  || f.trackedOnly || f.evidenceOnly

interface GraphUiState {
  mode: GraphMode
  layout: GraphLayout
  density: Density

  /** Hovered node id and edge key. Written on every pointer move, which is
   *  exactly why they are here and not in the workspace store. */
  hoverNode: string | null
  hoverEdge: string | null

  /** The edge whose inspector is open. Node inspection uses the workspace
   *  store's `primary`; an edge has no equivalent there, and inventing one
   *  would mean teaching every other view about a selection type it cannot
   *  render. */
  activeEdge: string | null

  /** Focus Mode: isolate the selection and its neighbourhood. Separate from
   *  the workspace store's `focus` trail, which is navigation — this one is a
   *  lens over the current canvas and does not refetch or lose the selection
   *  when it is switched off. */
  focusMode: boolean

  filters: GraphFilters

  /** An evidence or connection path to emphasise: ordered object ids. Drawn
   *  over the top of whatever else the canvas is doing, and cleared by the
   *  next selection change. */
  path: string[] | null
  pathLabel: string

  /** Panels. Minimap is opt-in above a node threshold rather than always on —
   *  a minimap of forty nodes is decoration. */
  minimap: boolean
  filtersOpen: boolean
  fullscreen: boolean

  /** Find-in-graph. A live substring over object names.
   *
   *  This is canvas state, not a workspace search: it does not fetch, it does
   *  not change the selection, and it is thrown away when you leave the view.
   *  What it does is re-weight the drawing — matches keep their labels and
   *  everything else drops back — so the answer to "where is Gazprom in this"
   *  is one keystroke rather than a hunt. */
  query: string
  findOpen: boolean

  /** The key. Tri-state on purpose: `null` means "whatever fits", and the
   *  stylesheet folds it away on a narrow stage. Once the reader has opened or
   *  closed it themselves that choice sticks at every width, because a panel
   *  that reopens itself when you resize the window is not a panel you control. */
  legendOpen: boolean | null

  setMode: (m: GraphMode) => void
  setLayout: (l: GraphLayout) => void
  setDensity: (d: Density) => void
  setHoverNode: (id: string | null) => void
  setHoverEdge: (key: string | null) => void
  setActiveEdge: (key: string | null) => void
  setFocusMode: (on: boolean) => void
  toggleFocusMode: () => void
  patchFilters: (p: Partial<GraphFilters>) => void
  toggleFamily: (family: string) => void
  toggleClass: (cls: RelClass) => void
  resetFilters: () => void
  setPath: (ids: string[] | null, label?: string) => void
  setMinimap: (on: boolean) => void
  setQuery: (q: string) => void
  setFindOpen: (on: boolean) => void
  toggleLegend: () => void
  setFiltersOpen: (on: boolean) => void
  setFullscreen: (on: boolean) => void
}

const readMode = (): GraphMode => {
  try {
    const v = localStorage.getItem('omx-graph-mode')
    return v === 'network' || v === 'clusters' || v === 'orbit' ? v : 'orbit'
  } catch { return 'orbit' }
}

const readDensity = (): Density => {
  try {
    const v = localStorage.getItem('omx-graph-density')
    return v === 'low' || v === 'high' ? v : 'medium'
  } catch { return 'medium' }
}

const readLegend = (): boolean | null => {
  try {
    const v = localStorage.getItem('omx-graph-legend')
    return v === 'true' ? true : v === 'false' ? false : null
  } catch { return null }
}

const persist = (key: string, value: string) => {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

export const useGraphUi = create<GraphUiState>()((set, get) => ({
  mode: readMode(),
  layout: 'force',
  density: readDensity(),

  hoverNode: null,
  hoverEdge: null,
  activeEdge: null,
  focusMode: false,
  filters: NO_FILTERS,
  path: null,
  pathLabel: '',
  minimap: true,
  filtersOpen: false,
  fullscreen: false,
  legendOpen: readLegend(),
  query: '',
  findOpen: false,

  setMode(m) {
    persist('omx-graph-mode', m)
    // Leaving a mode drops that mode's transient emphasis, but never the
    // selection — the user switching lenses has not changed what they hold.
    set({ mode: m, hoverNode: null, hoverEdge: null, activeEdge: null })
  },
  setLayout(l) { set({ layout: l }) },
  setDensity(d) { persist('omx-graph-density', d); set({ density: d }) },
  setHoverNode(id) { if (get().hoverNode !== id) set({ hoverNode: id }) },
  setHoverEdge(key) { if (get().hoverEdge !== key) set({ hoverEdge: key }) },
  setActiveEdge(key) { set({ activeEdge: key }) },
  setFocusMode(on) { set({ focusMode: on }) },
  toggleFocusMode() { set({ focusMode: !get().focusMode }) },
  patchFilters(p) { set({ filters: { ...get().filters, ...p } }) },
  toggleFamily(family) {
    const cur = get().filters.hiddenFamilies
    set({
      filters: {
        ...get().filters,
        hiddenFamilies: cur.includes(family)
          ? cur.filter((f) => f !== family)
          : [...cur, family],
      },
    })
  },
  toggleClass(cls) {
    const cur = get().filters.hiddenClasses
    set({
      filters: {
        ...get().filters,
        hiddenClasses: cur.includes(cls)
          ? cur.filter((c) => c !== cls)
          : [...cur, cls],
      },
    })
  },
  resetFilters() { set({ filters: NO_FILTERS }) },
  setPath(ids, label = '') { set({ path: ids, pathLabel: label }) },
  setMinimap(on) { set({ minimap: on }) },
  setQuery(q) { set({ query: q }) },
  setFindOpen(on) {
    // Closing clears. A dimmed canvas with no visible cause is the worst
    // possible resting state, and it is exactly what a panel that hides while
    // keeping its query would leave behind.
    set(on ? { findOpen: true, filtersOpen: false } : { findOpen: false, query: '' })
  },
  toggleLegend() {
    // From auto, the first press means "close it" — the only reason to reach
    // for the control is that the key is in the way.
    const next = get().legendOpen === false
    persist('omx-graph-legend', String(next))
    set({ legendOpen: next })
  },
  setFiltersOpen(on) { set(on ? { filtersOpen: true, findOpen: false } : { filtersOpen: false }) },
  setFullscreen(on) { set({ fullscreen: on }) },
}))
