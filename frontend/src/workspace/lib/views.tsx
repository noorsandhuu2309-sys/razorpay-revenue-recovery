// The view registry: one row per destination, and the single source of truth
// for what the shell puts around it.
//
// Before this existed, the shell drew the same furniture everywhere — the
// 380px Inspector, the 97px NOVA bar, the Trust Lens — regardless of whether
// the view had any use for them. ARENA is the clearest case: it has no objects
// to inspect, no provenance to filter and its own prompt box, yet it gave up
// 40% of the window to all three and ran a debate across six models in the
// 932px that were left.
//
// So each view declares what it actually needs. The rules the flags follow:
//
//   inspector  only where clicking something selects a workspace object worth
//              interrogating. Not where the view owns its own detail pane.
//   trustLens  only where the view draws provenance-carrying graph material.
//              TERRA's live feeds read /api/terra/* directly and carry no
//              provenance, so a floor control there would filter nothing and
//              imply it had.
//   actionBar  only where a selection can be acted on.
//   novaBar    everywhere EXCEPT views that own a primary input. Two competing
//              text boxes on one screen is the fastest way to make a user
//              distrust both.

import type { ReactNode } from 'react'
import type { ViewId } from './types'
import {
  IconAgents, IconAnalysis, IconAsk, IconBrief, IconChallenge, IconClaims,
  IconCompare, IconGraph, IconHome, IconIntel, IconIntents, IconMap, IconNews,
  IconOutputs, IconRelationships, IconRuns, IconSettings, IconSituation,
  IconSources, IconQuery, IconTable, IconTimeline, IconHelix,
} from '../components/Icons'

export interface ViewDef {
  id: ViewId
  label: string
  icon: (p: { size?: number }) => ReactNode
  group: 'main' | 'Spaces' | 'Research' | 'Work' | 'TERRA' | 'HELIX' | 'More' | 'foot'
  /** One line, shown as the rail tooltip, under the title in the topbar, and
   *  in the command palette. */
  hint: string
  inspector: boolean
  trustLens: boolean
  actionBar: boolean
  novaBar: boolean
}

const def = (
  id: ViewId, label: string, icon: ViewDef['icon'], group: ViewDef['group'],
  hint: string,
  flags: Partial<Pick<ViewDef, 'inspector' | 'trustLens' | 'actionBar' | 'novaBar'>> = {},
): ViewDef => ({
  id, label, icon, group, hint,
  inspector: true, trustLens: true, actionBar: true, novaBar: true, ...flags,
})

export const VIEWS: ViewDef[] = [
  // -- top level ------------------------------------------------------------
  // Home is the assistant. It owns a composer, so the global NOVA bar stands
  // down — two text boxes on one screen is the fastest way to make a user
  // distrust both, and it is worse here than anywhere else because the two
  // would go to different backends and keep separate histories.
  def('home', 'Home', IconHome, 'main', 'Ask anything — streaming, with sources',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),

  // CHALLENGE owns its own composer and operates on an idea the user types
  // rather than on selected objects, so the object chrome stands down.
  def('challenge', 'Challenge', IconChallenge, 'main',
      'Stress-test an idea before you research it',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),

  // -- the spine ------------------------------------------------------------
  // These five ARE the product: ask a question, get claims, check the sources
  // behind them, see how the evidence connects, read what changed. Everything
  // below this block is a lens onto the same material or a side surface, which
  // is why it sits behind a fold.
  // The conversation owns its own composer, so the global bar stands down.
  def('nova', 'Ask OMNIX', IconAsk, 'Research', 'The conversation with NOVA',
      { trustLens: false, actionBar: false, novaBar: false }),
  def('claims', 'Claims', IconClaims, 'Research', 'Assertions and the evidence for them'),
  def('sources', 'Sources', IconSources, 'Research', 'Everything this Space can cite'),
  def('graph', 'Graph', IconGraph, 'Research', 'Objects and how they connect'),
  def('brief', 'Brief', IconBrief, 'Research', 'What changed while you were away'),

  // -- work -----------------------------------------------------------------
  // These three own their own list/detail panes; a second detail panel beside
  // them is dead weight.
  def('intents', 'Intents', IconIntents, 'Work', 'Standing monitors on what you care about',
      { inspector: false, trustLens: false, actionBar: false }),
  def('outputs', 'Outputs', IconOutputs, 'Work', 'Documents this Space has made',
      { inspector: false, trustLens: false, actionBar: false }),
  def('agents', 'Agents', IconAgents, 'Work', 'Runs, progress, cost and controls',
      { inspector: false, trustLens: false, actionBar: false }),

  // -- TERRA ----------------------------------------------------------------
  // The map still selects real workspace objects, but it now carries the whole
  // geospatial subsystem in its own sidebar — layers, search, routes,
  // conditions, memory, geofences. That sidebar occupies the right edge, which
  // is exactly where the Inspector would be, and it owns two text inputs of
  // its own. So both stand down; the Trust Lens does too, because most of what
  // the map now draws comes from live providers and carries no provenance.
  def('map', 'World Map', IconMap, 'TERRA',
      'Everywhere: countries, risk, places, routes and conditions',
      { inspector: false, trustLens: false, novaBar: false }),
  // The rest read /api/terra/* directly. They carry no provenance, so no lens.
  def('intel', 'Intel', IconIntel, 'TERRA', 'Ranked signal from the live corpus',
      { trustLens: false }),
  def('news', 'World News', IconNews, 'TERRA', 'The live article feed, clustered',
      { trustLens: false }),
  def('relationships', 'Relationships', IconRelationships, 'TERRA', 'Who is doing what to whom',
      { trustLens: false }),
  def('situation', 'Situation Room', IconSituation, 'TERRA', 'Theatres and active hotspots',
      { inspector: false, trustLens: false, actionBar: false }),
  def('analysis', 'Analysis', IconAnalysis, 'TERRA', 'Synthesised assessment of the corpus',
      { inspector: false, trustLens: false, actionBar: false }),
  // TERRA's Ask owns its own query box. Renamed from a bare "Ask" because two
  // rail items called the same thing is a coin flip every time you navigate.
  def('ask', 'Query TERRA', IconQuery, 'TERRA', 'Semantic search, what-if and briefings',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
  def('terra-agents', 'Analyst Runs', IconRuns, 'TERRA', "TERRA's own job queue",
      { inspector: false, trustLens: false, actionBar: false }),

  // -- HELIX ----------------------------------------------------------------
  // The bioinformatics corpus. Its own group rather than an item under
  // Research, for the same reason TERRA has one: it is a self-contained domain
  // with its own data, its own retrieval and its own answer layer, and filing
  // it beside Claims would imply it draws on the same Space material. It does
  // not — it reads PubMed, not the workspace. It owns its question box, so the
  // global bar stands down, and it carries no workspace provenance, so the
  // lens does too.
  def('helix', 'Bioinformatics', IconHelix, 'HELIX',
      'Ask the literature: 4,000+ PubMed papers, answered with citations',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),

  // -- alternative lenses ---------------------------------------------------
  // Three ways of looking at objects already reachable from Graph and Claims.
  // Genuinely useful, but nobody's first move, and putting them at the top
  // level made the rail read as a feature list rather than a workflow.
  //
  // Below TERRA rather than above it: TERRA is a subsystem you go to, while
  // these are re-presentations of material you already have. A fold labelled
  // "More" sitting above a named product surface reads as if the named thing
  // is the afterthought.
  def('compare', 'Compare', IconCompare, 'More', 'Hold two or more side by side'),
  def('timeline', 'Timeline', IconTimeline, 'More', 'Events in the order they happened'),
  def('table', 'Table', IconTable, 'More', 'Every object as sortable rows'),

  // -- foot -----------------------------------------------------------------
  // Pinned to the bottom of the rail, out of the workflow. Settings is not a
  // destination you navigate to as part of doing work, and putting it in a
  // group would make it compete with views that are.
  def('settings', 'Settings', IconSettings, 'foot',
      'Account, appearance, Spaces and sign out',
      { inspector: false, trustLens: false, actionBar: false, novaBar: false }),
]

const BY_ID = new Map(VIEWS.map((v) => [v.id, v]))

/** Never throws: an unregistered view falls back to Home's chrome rather than
 *  rendering a shell with no panels at all. */
export function viewDef(id: ViewId): ViewDef {
  return BY_ID.get(id) ?? VIEWS[0]
}

/** Rail groups in order, and whether each starts open.
 *
 *  The rail used to show all 21 destinations at once, for a product that sells
 *  one thing: an answer you can defend. A list that long is not navigation, it
 *  is an inventory — it asks the user to pick a feature before they have a
 *  question, and it makes the two surfaces that matter (Claims, Sources) look
 *  exactly as important as Table.
 *
 *  So the default rail is the spine plus the work queue — ten items — and the
 *  eleven others sit behind two folds. Nothing is removed and nothing becomes
 *  unreachable: folded views keep their route, still answer to the command
 *  palette (⌘K), and a user who opens a fold has it remembered. `defaultOpen`
 *  is only the first impression, which is the thing that was wrong. */
export const GROUPS: readonly { label: ViewDef['group']; defaultOpen: boolean }[] = [
  { label: 'Research', defaultOpen: true },
  { label: 'Work', defaultOpen: true },
  // TERRA is a whole subsystem, and a good one, but it is a side surface for a
  // research tool. Eight items of it above the fold buried the spine — hence
  // still folded, but ABOVE "More", because it is a named product surface and
  // "More" is a drawer. A drawer listed above the thing it is more of reads as
  // if the named surface were the leftovers.
  { label: 'TERRA', defaultOpen: false },
  // Folded like TERRA, and directly after it: both are named domain subsystems
  // sitting beside the spine rather than inside it.
  { label: 'HELIX', defaultOpen: false },
  { label: 'More', defaultOpen: false },
]

export const GROUP_ORDER = GROUPS.map((g) => g.label)

/** Whether a group starts open, for a user who has never touched the control. */
export function groupDefaultOpen(label: string): boolean {
  return GROUPS.find((g) => g.label === label)?.defaultOpen ?? true
}

/** Whether a rail group is open right now.
 *
 *  Every consumer must use this rather than reading `overrides[label]` and
 *  falling back on its own, because there are three inputs and they have to be
 *  weighed in the same order everywhere:
 *
 *    1. An explicit fold by the user always wins, in both directions.
 *    2. Otherwise the group containing the CURRENT VIEW is open. TERRA starts
 *       folded and the World Map lives in it, so without this the app opens on
 *       a view whose own rail entry is off screen.
 *    3. Otherwise the group's registered default.
 *
 *  The rail and the toggle disagreeing about rule 2 is not cosmetic: the
 *  toggle computes the next state by negating the current one, so a toggle
 *  that thought TERRA was shut while the rail drew it open would swallow the
 *  first click and leave the header looking broken. */
export function groupOpen(
  label: string, view: ViewId, overrides: Record<string, boolean>,
): boolean {
  return overrides[label] ?? (viewDef(view).group === label || groupDefaultOpen(label))
}

export function viewsInGroup(group: ViewDef['group']): ViewDef[] {
  return VIEWS.filter((v) => v.group === group)
}
