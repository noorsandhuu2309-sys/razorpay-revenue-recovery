// The shared state every view reads. Selecting in one view updates all of them.
//
// The `origin` field is the non-obvious part and it is load-bearing. Without
// it: view A selects -> view B reacts -> B re-emits -> A reacts -> loop. The
// existing TERRA bus has this latent and survives only because single selection
// converges on itself. A selection SET does not, so every mutation records who
// caused it and views ignore their own echoes.

import { useMemo } from 'react'
import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'
import { api } from '../lib/api'
import { groupOpen } from '../lib/views'
import { DENSITY, useGraphUi } from './graphUi'
import type {
  FocusStep, GraphPayload, NovaTurn, OmxEvent, OmxObject, Ontology, Provenance,
  Summary, ViewId, Workspace,
} from '../lib/types'

/** Weakest-first, matching `PROVENANCE[*].rank` on the backend. The lens keeps
 *  everything at or above the chosen floor; index here IS the rank. */
export const PROVENANCE_ORDER: Provenance[] = [
  'user_created', 'verified', 'source_backed', 'ai_inferred',
]

export const provenanceRank = (p: Provenance | string): number => {
  const i = PROVENANCE_ORDER.indexOf(p as Provenance)
  return i === -1 ? PROVENANCE_ORDER.length - 1 : i
}

interface WorkspaceState {
  // -- workspace ---------------------------------------------------------
  workspaces: Workspace[]
  workspaceId: string
  ontology: Ontology | null
  summary: Summary | null
  /** Counts for EVERY Space, keyed by id.
   *
   *  Exists because of a specific complaint: switching to another Space
   *  appeared to do nothing. It was doing something — the graph, claims and
   *  brief all reloaded — but two of the three Spaces on this machine are a
   *  leftover demo with 0 objects and a scratch Space with 48, and neither the
   *  rail nor the view they landed on said so. The switch was working and
   *  invisible, which is indistinguishable from broken.
   *
   *  So the rail now states what each Space holds, before and after the click.
   *  An empty Space reads as empty rather than as a dead tab. */
  summaries: Record<string, Summary>

  // -- selection ---------------------------------------------------------
  /** focus: what the Inspector shows and the breadcrumb names */
  primary: string | null
  /** the full selection — this IS the Context Lens contents */
  selected: string[]
  /** survives selection changes until explicitly cleared */
  pinned: string[]
  /** which view caused the last change; views ignore their own echo */
  origin: ViewId | 'system'
  /** hydrated objects for everything in selected+pinned */
  contextObjects: Record<string, OmxObject>

  // -- focus -------------------------------------------------------------
  /** The trail the user drilled through. Empty means "the whole Space".
   *  Focus is navigation, not selection: it says WHERE you are, while
   *  `selected` says WHAT you are holding. Conflating the two is what makes
   *  graph products feel like they lose your place. */
  focus: FocusStep[]

  // -- view --------------------------------------------------------------
  view: ViewId
  graph: GraphPayload | null
  graphLoading: boolean
  typeFilter: string[]
  relationFilter: string[]
  /** Trust lens: hide anything weaker than this provenance. `ai_inferred`
   *  (the weakest) means "show everything". */
  provenanceFloor: Provenance

  // -- activity ----------------------------------------------------------
  activity: OmxEvent[]

  // -- conversation ------------------------------------------------------
  /** NOVA's thread for this Space. Server-owned, so the bottom bar and the
   *  Ask view are the same conversation rather than two histories. */
  turns: NovaTurn[]
  asking: boolean

  // -- ui ----------------------------------------------------------------
  paletteOpen: boolean
  inspectorOpen: boolean
  activityOpen: boolean
  busy: string | null

  /** Rail chrome. Both persist: a user who folds the rail down to icons, or
   *  folds TERRA away, means it for the next session too — re-expanding on
   *  every load is the fastest way to make a collapse control feel broken. */
  railCollapsed: boolean
  /** Which rail groups are open, keyed by group label. */
  groupsOpen: Record<string, boolean>
  toggleRail: () => void
  toggleGroup: (label: string) => void

  /** Which generated output the Outputs view should open. Set by Create so the
   *  user lands on the document they just made rather than on a list where
   *  they have to find it — the artifact IS the response to the action. */
  activeOutput: string | null
  openOutput: (id: string | null) => void

  // -- actions -----------------------------------------------------------
  init: () => Promise<void>
  setWorkspace: (id: string) => Promise<void>
  setView: (v: ViewId) => void

  select: (id: string, origin?: ViewId | 'system') => void
  toggle: (id: string, origin?: ViewId | 'system') => void
  selectMany: (ids: string[], origin?: ViewId | 'system') => void
  deselect: (id: string) => void
  clearSelection: () => void
  pin: (id: string) => void
  unpin: (id: string) => void

  /** Enter Focus Mode on an object: the canvas reorganises around it. */
  focusOn: (obj: OmxObject) => Promise<void>
  /** Pop back to a depth; 0 returns to the whole Space. */
  focusTo: (depth: number) => Promise<void>

  loadGraph: (roots?: string[]) => Promise<void>
  expandNode: (id: string, limit?: number) => Promise<void>
  /** Progressive disclosure, one ring at a time (§15). */
  expandRing: (id: string, degree: 1 | 2) => Promise<void>
  expandAll: (id: string) => Promise<void>
  refreshSummary: () => Promise<void>
  /** Counts for every Space. Sequential, and failures are skipped rather than
   *  aborting the batch — one unreachable Space must not blank the others. */
  loadSummaries: () => Promise<void>
  loadActivity: () => Promise<void>

  /** Send one message to NOVA. Every surface funnels through this so the
   *  thread, the busy state and the follow-on refreshes behave identically
   *  wherever the user typed. */
  ask: (text: string) => Promise<void>
  loadThread: () => Promise<void>
  clearThread: () => Promise<void>

  setTypeFilter: (t: string[]) => void
  setRelationFilter: (r: string[]) => void
  setProvenanceFloor: (p: Provenance) => void
  setPalette: (open: boolean) => void
  setInspector: (open: boolean) => void
  setActivity: (open: boolean) => void
  setBusy: (label: string | null) => void
}

/** localStorage helpers. Every read is guarded: private mode throws on access,
 *  and a corrupt value must degrade to the default rather than blank the app
 *  before it has rendered anything. */
function readFlag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === 'true'
  } catch { return fallback }
}

/** Rail folds the user has explicitly set, and only those.
 *
 *  This deliberately returns a SPARSE map. It used to seed every group with a
 *  default here — a second defaults table that duplicated `groupDefaultOpen`
 *  in lib/views.tsx and had already drifted from it (it carried a `Workspace`
 *  group that no longer exists, and omitted `More`). Because it wrote `false`
 *  rather than leaving the key absent, every consumer's `?? groupDefaultOpen()`
 *  fallback was dead code: a stored `false` is not nullish, so the real default
 *  could never be consulted. That is what kept TERRA folded even once the
 *  World Map became the landing view.
 *
 *  An absent key now means "no opinion", which is what lets the rail open the
 *  group containing the current view while still obeying a user who has folded
 *  it by hand. views.tsx owns the defaults; this function owns the overrides.
 */
function readGroups(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem('omx-rail-groups')
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    return parsed as Record<string, boolean>
  } catch { return {} }
}

function writeJSON(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* private mode */ }
}

/** Does this object survive the current trust lens? */
export const passesLens = (
  o: { provenance: Provenance | string }, floor: Provenance,
): boolean => provenanceRank(o.provenance) <= provenanceRank(floor)

/** Everything the AI should treat as "what the user means by these".
 *
 *  Safe to call imperatively with `contextIds(useWorkspace.getState())`, but
 *  NEVER pass it to `useWorkspace()` as a selector: it builds a new array every
 *  call, zustand compares with Object.is, and the component re-renders forever
 *  (React error #185). Use `useContextIds()` below when you need reactivity. */
export const contextIds = (s: WorkspaceState): string[] =>
  Array.from(new Set([...s.selected, ...s.pinned]))

export const useWorkspace = create<WorkspaceState>()(
  subscribeWithSelector((set, get) => ({
    workspaces: [],
    workspaceId: '',
    ontology: null,
    summary: null,
    summaries: {},

    primary: null,
    selected: [],
    pinned: [],
    origin: 'system',
    contextObjects: {},

    focus: [],

    // Home, which is now the assistant.
    //
    // This has been three things. The graph was wrong because a force layout
    // answers "look what I can render" rather than "what is going on". The
    // World Map replaced it and was a real improvement — today's news, sourced
    // and placed, which is the product's own claim about itself.
    //
    // It moves again because Home stopped being a table of contents. The map
    // answers a question the user did not ask; a composer lets them ask their
    // own, and the Space's counts sit on the empty state so the "what is going
    // on" answer is still one line above the box. The map is one click away and
    // first in TERRA, which is where a world view belongs.
    view: 'recovery',
    graph: null,
    graphLoading: false,
    typeFilter: [],
    relationFilter: [],
    provenanceFloor: 'ai_inferred',

    activity: [],

    turns: [],
    asking: false,

    paletteOpen: false,
    inspectorOpen: true,
    activityOpen: false,
    busy: null,

    railCollapsed: readFlag('omx-rail-collapsed', false),
    groupsOpen: readGroups(),
    toggleRail() {
      const next = !get().railCollapsed
      set({ railCollapsed: next })
      writeJSON('omx-rail-collapsed', next)
    },
    toggleGroup(label) {
      // `groupOpen` and not a local fallback, for the reason documented on it:
      // the rail draws the group with that function, and a toggle that
      // computed "currently open" any other way would negate a state the user
      // is not looking at and swallow the click.
      const open = groupOpen(label, get().view, get().groupsOpen)
      const next = { ...get().groupsOpen, [label]: !open }
      set({ groupsOpen: next })
      writeJSON('omx-rail-groups', next)
    },

    activeOutput: null,
    openOutput(id) { set({ activeOutput: id, view: id ? 'outputs' : get().view }) },

    async init() {
      const [{ workspaces }, ontology] = await Promise.all([
        api.workspaces(), api.ontology(),
      ])
      // Prefer a workspace with actual content — landing on an empty default
      // when a populated one exists reads as "the product is broken".
      let chosen = workspaces[0]
      for (const w of workspaces) {
        if (w.name === 'Geopolitical Intelligence') { chosen = w; break }
      }
      set({ workspaces, ontology, workspaceId: chosen?.id ?? '' })
      if (chosen) {
        await Promise.all([
          get().loadGraph(), get().refreshSummary(), get().loadActivity(),
          get().loadThread(),
        ])
      }
      // Not awaited with the rest: the rail's per-Space counts are one request
      // per Space, and blocking the first paint on all of them to decorate a
      // list is the wrong trade. They arrive a moment later and the badges
      // fade in.
      void get().loadSummaries()
    },

    async setWorkspace(id) {
      // Focus and selection are both scoped to a Space — carrying either
      // across would leave the breadcrumb naming objects that no longer exist.
      set({
        workspaceId: id, primary: null, selected: [], pinned: [],
        contextObjects: {}, graph: null, focus: [], activity: [], turns: [],
      })
      await Promise.all([
        get().loadGraph(), get().refreshSummary(), get().loadActivity(),
        get().loadThread(),
      ])
    },

    setView(v) { set({ view: v }) },

    select(id, origin = 'system') {
      set({ primary: id, selected: [id], origin })
      void hydrate([id])
    },

    toggle(id, origin = 'system') {
      const { selected } = get()
      const next = selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id]
      set({
        selected: next,
        // Focus follows the newest addition; removing the focused object
        // hands focus to whatever remains rather than blanking the Inspector.
        primary: next.includes(get().primary ?? '')
          ? get().primary
          : next[next.length - 1] ?? null,
        origin,
      })
      void hydrate(next)
    },

    selectMany(ids, origin = 'system') {
      set({ selected: ids, primary: ids[ids.length - 1] ?? null, origin })
      void hydrate(ids)
    },

    deselect(id) {
      const next = get().selected.filter((s) => s !== id)
      set({
        selected: next,
        primary: get().primary === id ? next[next.length - 1] ?? null : get().primary,
      })
    },

    clearSelection() {
      set({ selected: [], primary: null, origin: 'system' })
    },

    pin(id) {
      if (get().pinned.includes(id)) return
      set({ pinned: [...get().pinned, id] })
      void hydrate([id])
    },

    unpin(id) { set({ pinned: get().pinned.filter((p) => p !== id) }) },

    async focusOn(obj) {
      const { focus } = get()
      // Re-focusing something already on the trail is a pop, not a push —
      // otherwise drilling A > B > A grows a trail that lies about depth.
      const at = focus.findIndex((f) => f.id === obj.id)
      if (at !== -1) return get().focusTo(at + 1)

      set({
        focus: [...focus, { id: obj.id, name: obj.name, glyph: obj.glyph }],
        primary: obj.id, selected: [obj.id], origin: 'system',
        view: get().view === 'home' ? 'graph' : get().view,
      })
      void hydrate([obj.id])
      await get().loadGraph([obj.id])
    },

    async focusTo(depth) {
      const next = get().focus.slice(0, Math.max(0, depth))
      const head = next[next.length - 1]
      set({ focus: next, primary: head?.id ?? null, origin: 'system' })
      // Depth 0 is the whole Space, which is the unrooted graph.
      await get().loadGraph(head ? [head.id] : [])
    },

    async loadGraph(roots) {
      const ws = get().workspaceId
      if (!ws) return
      set({ graphLoading: true })
      try {
        const { focus, selected } = get()
        // Focus wins over selection as the graph root: you can select a node
        // in a neighbourhood without wanting the whole canvas to jump to it.
        const rootIds = roots
          ?? (focus.length ? [focus[focus.length - 1].id] : selected)
        // Density is the fix for the view opening on nine nodes out of 644.
        // One hop from a single seed can never return more than `per_node + 1`
        // objects however high `max_nodes` is set, so hops is the lever.
        const d = DENSITY[useGraphUi.getState().density]
        const graph = await api.graph(ws, {
          roots: rootIds.join(','),
          // Focus is a drill-down, so it earns another hop of depth. Clamped
          // at 4 server-side; asking beyond that is ignored, not an error.
          hops: Math.min(4, d.hops + (focus.length ? 1 : 0)),
          max_nodes: d.maxNodes, per_node: d.perNode,
          types: get().typeFilter.join(','),
          relations: get().relationFilter.join(','),
          communities: true,
        })
        set({ graph })
      } finally {
        set({ graphLoading: false })
      }
    },

    async expandNode(id, limit = 8) {
      const ws = get().workspaceId
      const graph = get().graph
      if (!ws || !graph) return
      const known = graph.nodes.map((n) => n.id)
      const add = await api.expand(ws, id, known, limit)
      if (!add.nodes.length) return
      const ids = new Set(known)
      set({
        graph: {
          ...graph,
          nodes: [...graph.nodes, ...add.nodes.filter((n) => !ids.has(n.id))],
          edges: [...graph.edges, ...add.edges],
        },
      })
    },

    /** Expand a whole ring at once — "+1" is the node's own neighbours, "+2"
     *  also expands everything that arrived with it.
     *
     *  Sequential rather than parallel on purpose: each call passes the ids
     *  already on screen as `exclude`, and firing them together would have
     *  every request working from the same stale exclusion list and pulling in
     *  the same neighbours several times over. */
    async expandRing(id, degree) {
      const ws = get().workspaceId
      if (!ws || !get().graph) return
      set({ graphLoading: true })
      try {
        await get().expandNode(id, 12)
        if (degree < 2) return
        const ring = (get().graph?.edges ?? [])
          .filter((e) => e.source === id || e.target === id)
          .map((e) => (e.source === id ? e.target : e.source))
        // Bounded: a hub with ninety neighbours would otherwise issue ninety
        // requests and land the whole Space on the canvas in one gesture.
        for (const nb of Array.from(new Set(ring)).slice(0, 12)) {
          await get().expandNode(nb, 6)
        }
      } finally {
        set({ graphLoading: false })
      }
    },

    /** Everything reachable from here, in one request, by asking the graph
     *  endpoint for a deep neighbourhood rather than walking expand calls. */
    async expandAll(id) {
      const ws = get().workspaceId
      if (!ws) return
      set({ graphLoading: true })
      try {
        const graph = await api.graph(ws, {
          roots: id, hops: 4, max_nodes: 600, per_node: 24,
          types: get().typeFilter.join(','),
          relations: get().relationFilter.join(','),
          communities: true,
        })
        set({ graph })
      } finally {
        set({ graphLoading: false })
      }
    },

    async refreshSummary() {
      const ws = get().workspaceId
      if (!ws) return
      try {
        const summary = await api.summary(ws)
        // Write both: the active Space's own counts, and its entry in the
        // per-Space map, so the rail badge cannot go stale after a research
        // run has added objects to the Space you are standing in.
        set({ summary, summaries: { ...get().summaries, [ws]: summary } })
      } catch { /* empty ws */ }
    },

    async loadSummaries() {
      const out: Record<string, Summary> = {}
      for (const w of get().workspaces) {
        try { out[w.id] = await api.summary(w.id) } catch { /* skip */ }
      }
      set({ summaries: { ...get().summaries, ...out } })
    },

    async loadActivity() {
      const ws = get().workspaceId
      if (!ws) return
      try {
        const { events } = await api.timeline(ws, { limit: 60 })
        set({ activity: events })
      } catch { /* an empty workspace has no activity, which is not an error */ }
    },

    async loadThread() {
      const ws = get().workspaceId
      if (!ws) return
      try {
        const { turns } = await api.thread(ws)
        set({ turns })
      } catch { /* a Space with no conversation yet */ }
    },

    async clearThread() {
      const ws = get().workspaceId
      if (!ws) return
      try { await api.clearThread(ws) } finally { set({ turns: [] }) }
    },

    async ask(text) {
      const ws = get().workspaceId
      const body = text.trim()
      if (!body || !ws || get().asking) return

      // Show the user's own message immediately. The server is the source of
      // truth for the thread, but waiting for a round trip to echo what they
      // just typed is what makes an assistant feel unresponsive.
      const optimistic: NovaTurn = {
        id: `local-${Date.now()}`, role: 'user', text: body, intent: '',
        model: '', executionId: null, context: contextIds(get()),
        createdAt: new Date().toISOString(),
      }
      set({ turns: [...get().turns, optimistic], asking: true, busy: 'Thinking…' })

      try {
        const res = await api.command(ws, body, contextIds(get())) as Record<string, any>

        // The server grounds an unselected question on whatever objects the
        // question named. Adopt those as the live selection so the user can
        // SEE what it answered from — an implicit context they cannot inspect
        // or correct is worse than none, because a wrong grounding then looks
        // like a wrong model.
        const resolved = res.resolved as { id: string }[] | undefined
        if (resolved?.length && !contextIds(get()).length) {
          get().selectMany(resolved.map((r) => r.id), 'system')
        }

        // A research or agent run answers later, so follow it and refresh the
        // graph when it lands rather than leaving the thread looking finished.
        if (res.executionId) {
          void get().loadActivity()
          void pollExecution(String(res.executionId))
        } else if (res.intent === 'query') {
          const results = res.results as { id: string }[] | undefined
          if (results?.length) {
            get().selectMany(results.slice(0, 6).map((r) => r.id), 'system')
          }
          void get().loadGraph()
        }
      } catch (e) {
        set({
          turns: [...get().turns, {
            id: `err-${Date.now()}`, role: 'assistant',
            text: e instanceof Error ? e.message : 'That request failed.',
            intent: 'error', model: '', executionId: null, context: [],
            createdAt: new Date().toISOString(),
          }],
        })
      } finally {
        set({ asking: false, busy: null })
        // Re-read from the server so the optimistic turn is replaced by the
        // stored pair and nothing can drift out of sync.
        await get().loadThread()
      }
    },

    setTypeFilter(t) { set({ typeFilter: t }); void get().loadGraph() },
    setRelationFilter(r) { set({ relationFilter: r }); void get().loadGraph() },
    // The lens is a read-time filter, not a query: re-fetching would make it
    // feel like a load instead of an adjustment, and every view already has
    // the provenance it needs.
    setProvenanceFloor(p) { set({ provenanceFloor: p }) },
    setPalette(open) { set({ paletteOpen: open }) },
    setInspector(open) { set({ inspectorOpen: open }) },
    setActivity(open) { set({ activityOpen: open }) },
    setBusy(label) { set({ busy: label }) },
  })),
)

/** Follow a launched execution and fold its findings into the Space when it
 *  finishes. Polled rather than streamed because the thread only needs the
 *  terminal state, and an SSE subscription per message is a lot of machinery
 *  for "tell me when it is done". */
async function pollExecution(executionId: string, tries = 90): Promise<void> {
  for (let i = 0; i < tries; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    let status = ''
    try { status = (await api.execution(executionId)).status } catch { return }
    if (['completed', 'failed', 'cancelled'].includes(status)) {
      if (status === 'completed') {
        // Ingestion runs server-side on completion; give it a moment before
        // pulling the enriched graph.
        await new Promise((r) => setTimeout(r, 2500))
        const s = useWorkspace.getState()
        await Promise.all([
          s.loadGraph(), s.refreshSummary(), s.loadActivity(), s.loadThread(),
        ])
      }
      return
    }
  }
}

/** Reactive form of `contextIds`.
 *
 *  Subscribes to the two underlying arrays — which the store replaces only on
 *  a real change — and derives the union under `useMemo`, so the identity is
 *  stable between renders and the subscription cannot loop. */
export function useContextIds(): string[] {
  const selected = useWorkspace((s) => s.selected)
  const pinned = useWorkspace((s) => s.pinned)
  return useMemo(
    () => Array.from(new Set([...selected, ...pinned])), [selected, pinned])
}

/** Fetch full objects for ids not already cached, so the Context Lens can
 *  render names and types without every view passing objects around. */
async function hydrate(ids: string[]) {
  const { workspaceId, contextObjects, graph } = useWorkspace.getState()
  if (!workspaceId) return
  const missing = ids.filter((id) => !contextObjects[id])
  if (!missing.length) return

  const next: Record<string, OmxObject> = {}
  for (const id of missing) {
    // The graph payload usually already holds it — avoid a round trip.
    const local = graph?.nodes.find((n) => n.id === id)
    if (local) { next[id] = local; continue }
    try { next[id] = await api.object(workspaceId, id) } catch { /* gone */ }
  }
  if (Object.keys(next).length) {
    useWorkspace.setState((s) => ({ contextObjects: { ...s.contextObjects, ...next } }))
  }
}
