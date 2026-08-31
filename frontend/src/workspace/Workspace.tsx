// The workspace shell. Left rail for Spaces and views, centre canvas, right
// inspector, NOVA across the bottom.
//
// Views are grouped rather than listed flat. WORKSPACE views draw the object
// graph the user is building; TERRA views are the live geopolitical instrument
// that existed before the workspace and still does, in full. They share one
// selection: clicking a country on the Map selects the same object the Graph
// would, so the two groups are one product rather than two apps in a frame.
//
// The surfaces around the canvas — focus trail, trust lens, action bar,
// inspector, NOVA bar — are shell furniture rather than view features, because
// context must survive a view switch. But "belongs to the shell" is not the
// same as "belongs on every screen": each view declares in `lib/views` which
// of them it actually uses, and the shell honours that. ARENA asking for none
// of them is why it now gets the whole window instead of 60% of it.

import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import markUrl from './assets/omnix-mark.png'
import { HomeView } from './views/Home'
import { AskNovaView } from './views/Ask'
import { ChallengeView } from './views/Challenge'
import { HelixView } from './views/Helix'
import { CompareView } from './views/Compare'
import { GraphView } from './views/GraphView'
// The World Map is the ONLY lazily-loaded view, and the reason is specific
// rather than a general policy: MapLibre GL is ~1MB, more than double the rest
// of the application put together. Bundling it eagerly makes every user who
// never opens a map pay for one on first load. Every other view is small
// enough that splitting it would cost a request and save nothing.
const MapView = lazy(() =>
  import('./views/MapView').then((m) => ({ default: m.MapView })))
import { BriefView, ClaimsView, SourcesView, TableView, TimelineView } from './views/Views'
import { OutputsView } from './views/Outputs'
import { IntentsView } from './views/Intents'
import { AgentsView } from './views/Agents'
import { RecoveryView } from './views/Recovery'
import { AnalysisView, NewsView, RelationshipsView } from './views/TerraViews'
import { AskView, IntelView, SituationView, TerraAgentsView } from './views/TerraIntel'
import { SettingsView } from './views/Settings'
import { Login } from './views/Login'
import { Inspector } from './components/Inspector'
import { NovaBar } from './components/NovaBar'
import { CommandPalette } from './components/CommandPalette'
import { ViewBoundary } from './components/ViewBoundary'
import { ActionBar } from './components/ActionBar'
import { Breadcrumbs } from './components/Breadcrumbs'
import { TrustLens } from './components/TrustLens'
import { ActivityPanel } from './components/ActivityPanel'
import { AppearanceMenu } from './components/AppearanceMenu'
import {
  IconActivity, IconChevron, IconInspector, IconPalette, IconSpace,
} from './components/Icons'
import { hydrate as hydrateAppearance } from './lib/appearance'
import { refresh as refreshAuth, useAuth } from './lib/auth'
import { GROUP_ORDER, groupOpen, viewDef, viewsInGroup, type ViewDef } from './lib/views'
import { useWorkspace } from './store/workspace'
import './workspace.css'

/** One collapsible rail group.
 *
 *  The collapsed rail USED to ignore the fold and draw every group open. The
 *  reasoning was that a folded group would be unreachable without widening the
 *  rail first — true at the time, and the cure was worse than the disease:
 *  collapsing the rail showed MORE destinations than expanding it (26 tiles
 *  against 13), overflowed a 900px window by 363px, and grew a scrollbar that
 *  ate 16 of the rail's 56 pixels. Navigation you have to scroll, in a column
 *  too narrow to hold the scrollbar it needs, is what made the collapsed rail
 *  read as a prototype.
 *
 *  So the fold is honoured in both states, and the premise is fixed instead:
 *  collapsed, the group header survives as a full-width toggle strip — a
 *  hairline that turns into a chevron on hover — so a folded group is one click
 *  away rather than unreachable. The label itself goes, because a
 *  three-character stub of "WORKSPACE" is noise; it comes back in the hover
 *  flyout, which is where every other label lives at this width. */
function RailGroup({ label, collapsed, open, onToggle, children, count }: {
  label: string
  collapsed: boolean
  open: boolean
  onToggle: () => void
  children: ReactNode
  count: number
}) {
  if (collapsed) {
    return (
      <div className={`omx-rail-group ${open ? '' : 'closed'}`}>
        <button
          className={`omx-group-grip ${open ? '' : 'closed'} ${label === 'TERRA' ? 'terra' : ''}`}
          onClick={onToggle}
          aria-expanded={open}
          aria-label={`${label} — ${open ? 'hide' : 'show'} ${count} views`}
          data-tip={label}
          data-hint={open ? `Hide these ${count}` : `${count} more views`}
        >
          <span className="ln" aria-hidden="true" />
          <span className="chev" aria-hidden="true"><IconChevron size={10} /></span>
        </button>
        {open && children}
      </div>
    )
  }
  return (
    <div className="omx-rail-group">
      <button
        className={`omx-group-head ${open ? '' : 'closed'} ${label === 'TERRA' ? 'terra' : ''}`}
        onClick={onToggle}
        aria-expanded={open}
      >
        <span>{label}</span>
        {!open && <span className="ct">{count}</span>}
        <span className="chev" aria-hidden="true"><IconChevron size={11} /></span>
      </button>
      {open && children}
    </div>
  )
}

/** The label that stands in for every rail label once the rail is icons only.
 *
 *  It replaces the native `title` attribute, which was the collapsed rail's
 *  ONLY way to name a destination and a poor one: the OS tooltip takes about a
 *  second to appear, is styled by the desktop rather than the product, and
 *  cannot show the view's hint as a second line. Twenty-odd unlabelled icons
 *  whose names arrive a second late is most of what "first-stage prototype"
 *  meant here.
 *
 *  One node for the whole rail, not one per button: it is positioned `fixed`
 *  because the rail is a scroll container with `overflow-x: hidden`, so a tip
 *  living inside a tile would be clipped by its own parent. Hover is delegated,
 *  so group grips and the account row get the same treatment for free, and
 *  focus is wired alongside it so the flyout answers the keyboard too. */
function RailTips({ enabled }: { enabled: boolean }) {
  const [tip, setTip] = useState<{ top: number; left: number; label: string; hint: string } | null>(null)
  const railRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!enabled) { setTip(null); return }
    const rail = document.querySelector<HTMLElement>('.omx-rail')
    if (!rail) return
    railRef.current = rail

    const show = (e: Event) => {
      const el = (e.target as HTMLElement | null)?.closest?.('[data-tip]') as HTMLElement | null
      if (!el) return
      const r = el.getBoundingClientRect()
      setTip({
        top: r.top + r.height / 2,
        left: rail.getBoundingClientRect().right + 8,
        label: el.dataset.tip ?? '',
        hint: el.dataset.hint ?? '',
      })
    }
    const hide = () => setTip(null)

    rail.addEventListener('mouseover', show)
    rail.addEventListener('mouseleave', hide)
    rail.addEventListener('focusin', show)
    rail.addEventListener('focusout', hide)
    // A tile that scrolls out from under the pointer would otherwise leave its
    // label hanging beside whatever took its place.
    rail.addEventListener('scroll', hide, { passive: true })
    window.addEventListener('blur', hide)
    return () => {
      rail.removeEventListener('mouseover', show)
      rail.removeEventListener('mouseleave', hide)
      rail.removeEventListener('focusin', show)
      rail.removeEventListener('focusout', hide)
      rail.removeEventListener('scroll', hide)
      window.removeEventListener('blur', hide)
    }
  }, [enabled])

  if (!tip) return null
  return (
    <div className="omx-rail-tip" style={{ top: tip.top, left: tip.left }} role="presentation">
      <span className="l">{tip.label}</span>
      {tip.hint && <span className="h">{tip.hint}</span>}
    </div>
  )
}

/** The gate, and the only place that decides whether the app renders at all.
 *
 *  Three states, and the middle one matters: until `/api/auth/me` answers we
 *  know nothing, and painting either the workspace or the login screen on a
 *  guess means one of them flashes and is replaced. A signed-in user seeing the
 *  sign-in form for 200ms reads as having been logged out.
 *
 *  When the server has the gate switched off (`OMNIX_AUTH=off`) `required` is
 *  false and the shell renders regardless — the dev escape hatch is honoured on
 *  both sides or it is not an escape hatch. */
/** One rail destination.
 *
 *  Now that the rail is the ONLY navigation, a click on it has to feel like it
 *  did something — the topbar no longer echoes the change with a second
 *  highlight moving. So the item emits a ripple from where the pointer landed
 *  and the label carries the press through.
 *
 *  The ripple is a keyed element rather than a CSS class toggled on and off:
 *  clicking the same item twice in a row with a class would restart nothing,
 *  because the class never left. Bumping the key remounts the span, which is
 *  what makes a repeat click animate at all. It removes itself on
 *  `animationend`, so a long session does not accumulate one node per click.
 *
 *  `prefers-reduced-motion` is honoured in CSS, where the ripple's animation is
 *  disabled; the element is still mounted and still cleans itself up, which
 *  keeps the two paths identical apart from the motion. */
function RailItem({ view, active, collapsed, onGo }: {
  view: ViewDef
  active: boolean
  collapsed: boolean
  onGo: () => void
}) {
  const [ripple, setRipple] = useState<{ key: number; x: number; y: number } | null>(null)
  const Icon = view.icon

  return (
    <button
      className={`omx-rail-item ${active ? 'on' : ''}`}
      onClick={(e) => {
        const box = e.currentTarget.getBoundingClientRect()
        setRipple({
          key: Date.now(),
          x: e.clientX - box.left,
          y: e.clientY - box.top,
        })
        onGo()
      }}
      // Expanded, the label is on the tile and the native tooltip carries the
      // hint. Collapsed, both come from the flyout — leaving `title` on as well
      // would put the OS's slow grey copy on top of the product's own.
      title={collapsed ? undefined : `${view.label} — ${view.hint}`}
      aria-label={collapsed ? `${view.label} — ${view.hint}` : undefined}
      aria-current={active ? 'page' : undefined}
      data-tip={view.label}
      data-hint={view.hint}
    >
      {ripple && (
        <span
          key={ripple.key}
          className="omx-ripple"
          style={{ left: ripple.x, top: ripple.y }}
          onAnimationEnd={() => setRipple(null)}
          aria-hidden="true"
        />
      )}
      <span className="ic"><Icon size={16} /></span>
      {!collapsed && <span className="tx">{view.label}</span>}
    </button>
  )
}

export default function Workspace() {
  const auth = useAuth()

  useEffect(() => { void refreshAuth() }, [])

  if (!auth.ready) {
    return (
      <div className="omx-boot">
        <span className="omx-spin" />
      </div>
    )
  }
  if (auth.required && !auth.authenticated) return <Login />
  return <Shell />
}

function Shell() {
  const user = useAuth().user
  const init = useWorkspace((s) => s.init)
  const view = useWorkspace((s) => s.view)
  const setView = useWorkspace((s) => s.setView)
  const workspaces = useWorkspace((s) => s.workspaces)
  const workspaceId = useWorkspace((s) => s.workspaceId)
  const setWorkspace = useWorkspace((s) => s.setWorkspace)
  const summary = useWorkspace((s) => s.summary)
  const summaries = useWorkspace((s) => s.summaries)
  const inspectorOpen = useWorkspace((s) => s.inspectorOpen)
  const setInspector = useWorkspace((s) => s.setInspector)
  const setPalette = useWorkspace((s) => s.setPalette)
  const busy = useWorkspace((s) => s.busy)
  const focus = useWorkspace((s) => s.focus)
  const focusTo = useWorkspace((s) => s.focusTo)
  const activityOpen = useWorkspace((s) => s.activityOpen)
  const setActivity = useWorkspace((s) => s.setActivity)
  const activity = useWorkspace((s) => s.activity)
  const railCollapsed = useWorkspace((s) => s.railCollapsed)
  const toggleRail = useWorkspace((s) => s.toggleRail)
  const groupsOpen = useWorkspace((s) => s.groupsOpen)
  const toggleGroup = useWorkspace((s) => s.toggleGroup)

  useEffect(() => {
    // Normally a no-op: the inline script in index.html has already stamped
    // the saved mode and accent, which is what stops the first paint being
    // dark and then snapping to the user's theme. This is the fallback for
    // when that script did not run (a test renderer, an embedded mount).
    hydrateAppearance()
    void init()
  }, [init])

  // Escape backs out one level of focus. §16 asks for an escape hatch from
  // every transition, and a drill-down without a keyboard way out is the one
  // people get stuck in. It defers to the palette, which owns Escape while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (useWorkspace.getState().paletteOpen) return
      const f = useWorkspace.getState().focus
      if (f.length) { e.preventDefault(); void focusTo(f.length - 1) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focusTo])

  const active = workspaces.find((w) => w.id === workspaceId)
  const def = viewDef(view)
  // A panel shows only when the view asks for it AND the user has not folded
  // it away. Both conditions matter: the view knows what is relevant, the user
  // knows what they want to look at right now.
  const showInspector = def.inspector && inspectorOpen
  const ViewIcon = def.icon

  return (
    <div className={[
      'omx-shell',
      railCollapsed ? 'rail-collapsed' : '',
      showInspector ? '' : 'no-inspector',
      def.novaBar ? '' : 'no-nova',
    ].filter(Boolean).join(' ')}>
      <div className="omx-rail">
        <div className="omx-brand">
          {/* The wireframe-Möbius OMNIX mark — the original, and the one the
              product has always used. The PNG carries the shape in its alpha
              channel over flat gold RGB, so it is applied as a MASK and painted
              with background-color rather than drawn as an image: that is what
              lets one asset read correctly in both themes instead of shipping a
              light and a dark copy. mask-image is set inline because Vite
              hashes the asset URL at build time. */}
          <span
            className="mark"
            role="img"
            aria-label="OMNIX"
            style={{ maskImage: `url(${markUrl})`, WebkitMaskImage: `url(${markUrl})` }}
          />
          {!railCollapsed && (
            <>
              <div className="bwrap">
                <div className="bt">OMNIX</div>
                <div className="bs">Intelligence Workspace</div>
              </div>
              <button
                className="omx-rail-collapse"
                onClick={toggleRail}
                title="Collapse rail"
                aria-label="Collapse rail"
              ><IconChevron size={13} /></button>
            </>
          )}
        </div>
        {railCollapsed && (
          <button
            className="omx-rail-expand"
            onClick={toggleRail}
            aria-label="Expand rail"
            data-tip="Expand rail"
            data-hint="Show the labels"
          ><IconChevron size={13} /></button>
        )}

        {/* Only this middle band scrolls. The brand above it and the account
            below it are pinned, because a rail whose account row scrolls out of
            reach — which is what both states did, collapsed by 363px and
            expanded by 15 — has no fixed landmarks at all. */}
        <div className="omx-rail-scroll">
        <div className="omx-rail-group">
          {VIEWS_MAIN.map((v) => (
            <RailItem key={v.id} view={v} active={view === v.id}
                      collapsed={railCollapsed} onGo={() => setView(v.id)} />
          ))}
        </div>

        <RailGroup
          label="Spaces"
          collapsed={railCollapsed}
          open={groupsOpen.Spaces ?? true}
          onToggle={() => toggleGroup('Spaces')}
          count={workspaces.length}
        >
          {/* "Spaces", not "Workspaces": the blueprint's word, and the better
              one — a Space is somewhere you return to, a workspace is a mode.

              Each row states what the Space HOLDS. Without that, switching
              between three Spaces on this machine — one with 652 objects, one
              with 48, one with none — looked like clicking dead tabs: the app
              was reloading everything correctly and had no way to say so.
              A count that changes is the proof the switch did something, and
              "empty" said up front is why an empty Space no longer reads as a
              broken one. */}
          {workspaces.map((w) => {
            const s = summaries[w.id]
            return (
              <button
                key={w.id}
                className={`omx-rail-item space ${w.id === workspaceId ? 'on' : ''}`}
                // Lands on Graph, whose entire content is a function of the
                // Space. Staying put would leave you on Home or the World Map,
                // neither of which is Space-scoped — which is exactly how a
                // working switch came to look like a no-op.
                onClick={() => { void setWorkspace(w.id); setView('graph') }}
                title={railCollapsed ? undefined : (w.description || w.name)}
                aria-label={railCollapsed ? w.name : undefined}
                aria-current={w.id === workspaceId ? 'true' : undefined}
                data-tip={w.name}
                data-hint={s === undefined ? 'Loading…'
                  : s.objects === 0 ? 'Empty Space'
                    : `${s.objects} objects · ${s.sources} sources`}
              >
                {/* Collapsed, every Space drew the same hexagon: three
                    identical tiles in a column, and the only way to tell them
                    apart was to hover each one. A monogram makes the choice
                    visible without a tooltip, which is the whole job of an
                    icon-only rail. */}
                <span className="ic">
                  {railCollapsed
                    ? <span className="mono">{spaceMonogram(w.name)}</span>
                    : <IconSpace size={16} />}
                </span>
                {!railCollapsed && (
                  <span className="bd">
                    <span className="tx">{w.name}</span>
                    <span className="ct">
                      {s === undefined ? '…'
                        : s.objects === 0 ? 'empty'
                          : `${s.objects} objects · ${s.sources} sources`}
                    </span>
                  </span>
                )}
              </button>
            )
          })}
        </RailGroup>

        {GROUP_ORDER.map((group) => (
          <RailGroup
            key={group}
            label={group}
            collapsed={railCollapsed}
            open={groupOpen(group, view, groupsOpen)}
            onToggle={() => toggleGroup(group)}
            count={viewsInGroup(group).length}
          >
            {viewsInGroup(group).map((v) => (
              <RailItem key={v.id} view={v} active={view === v.id}
                        collapsed={railCollapsed} onGo={() => setView(v.id)} />
            ))}
          </RailGroup>
        ))}

        {summary && !railCollapsed && (
          <div className="omx-rail-group stats">
            <div className="omx-label">This Space</div>
            <div className="omx-rail-stats">
              {([
                ['objects', summary.objects],
                ['links', summary.relationships],
                ['claims', summary.claims],
                ['sources', summary.sources],
                ['tracked', summary.tracked],
              ] as const).map(([k, v]) => (
                <div className="st" key={k}>
                  <span className="n">{v}</span><span className="l">{k}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        </div>

        {/* Pinned to the bottom. Everything above is work; this is the account
            and the machinery around it, and interleaving the two is what makes
            a rail read as a list of everything rather than a workflow. */}
        <div className="omx-rail-foot">
          {VIEWS_FOOT.map((v) => (
            <RailItem key={v.id} view={v} active={view === v.id}
                      collapsed={railCollapsed} onGo={() => setView(v.id)} />
          ))}
          <button
            className={`omx-rail-account ${view === 'settings' ? 'on' : ''}`}
            onClick={() => setView('settings')}
            title={railCollapsed ? undefined : (user ? `${user.name} — ${user.email}` : 'Account')}
            aria-label={user ? `${user.name} — ${user.email}` : 'Account'}
            data-tip={user?.name ?? 'Not signed in'}
            data-hint={user?.email ?? 'Open settings'}
          >
            <span className="av">{user?.initials ?? '··'}</span>
            {!railCollapsed && (
              <span className="bd">
                <span className="n">{user?.name ?? 'Not signed in'}</span>
                <span className="e">{user?.email ?? 'Open settings'}</span>
              </span>
            )}
          </button>
        </div>
      </div>
      <RailTips enabled={railCollapsed} />

      <div className="omx-topbar">
        {/* There used to be a second navigation here: a strip of tabs for
            every sibling of the current view — i.e. an exact copy of whichever
            rail group was already open and highlighted two inches to the left.
            Two controls for one job means every navigation is a coin flip
            about which one to reach for, and the copy could never show
            anything the rail did not.

            The rail won because it shows ALL destinations at once, keeps its
            state, and is where the eye already goes. So the topbar stops
            navigating and starts answering the question the rail cannot: what
            am I looking at, and what is this view for. While drilled into an
            object the focus trail replaces it, because "where am I" outranks
            "what is this" once you are three levels deep. */}
        {focus.length ? <Breadcrumbs /> : (
          <div className="omx-viewid">
            <span className="g"><ViewIcon size={15} /></span>
            <span className="t">{def.label}</span>
            <span className="h">{def.hint}</span>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {busy && (
          <span className="omx-label busy"><span className="omx-spin" /> {busy}</span>
        )}
        {def.trustLens && <TrustLens />}
        <span className="omx-wsname">{active?.name}</span>
        <button className="omx-btn icon" onClick={() => setPalette(true)}
                title="Command palette — Ctrl K" aria-label="Command palette">
          <IconPalette size={15} />
        </button>
        <button className={`omx-btn icon ${activityOpen ? 'on' : ''}`}
                onClick={() => setActivity(!activityOpen)}
                title="Activity" aria-label="Activity">
          <IconActivity size={15} />
          {activity.length > 0 && <i className="omx-dot" />}
        </button>
        {/* Only offered where the view has an inspector to show. */}
        {def.inspector && (
          <button className={`omx-btn icon ${inspectorOpen ? 'on' : ''}`}
                  onClick={() => setInspector(!inspectorOpen)}
                  title={inspectorOpen ? 'Hide inspector' : 'Show inspector'}
                  aria-label={inspectorOpen ? 'Hide inspector' : 'Show inspector'}>
            <IconInspector size={15} />
          </button>
        )}
        <AppearanceMenu />
      </div>

      <div className="omx-canvas">
        {/* Every view renders inside a boundary. A render error in one of them
            used to unmount the entire workspace; now it is contained and the
            shell stays navigable. Keyed on `view` so navigating clears it. */}
        <ViewBoundary resetKey={view}>
        {view === 'home' && <HomeView />}
        {view === 'nova' && <AskNovaView />}
        {view === 'compare' && <CompareView />}
        {view === 'graph' && <GraphView />}
        {view === 'map' && (
          <Suspense fallback={<div className="omx-gx-boot">Loading the map…</div>}>
            <MapView />
          </Suspense>
        )}
        {view === 'timeline' && <TimelineView />}
        {view === 'table' && <TableView />}
        {view === 'brief' && <BriefView />}
        {view === 'claims' && <ClaimsView />}
        {view === 'sources' && <SourcesView />}
        {view === 'outputs' && <OutputsView />}
        {view === 'intents' && <IntentsView />}
        {view === 'agents' && <AgentsView />}
        {view === 'recovery' && <RecoveryView />}
        {view === 'news' && <NewsView />}
        {view === 'relationships' && <RelationshipsView />}
        {view === 'analysis' && <AnalysisView />}
        {view === 'intel' && <IntelView />}
        {view === 'situation' && <SituationView />}
        {view === 'ask' && <AskView />}
        {view === 'challenge' && <ChallengeView />}
        {view === 'terra-agents' && <TerraAgentsView />}
        {view === 'helix' && <HelixView />}
        {view === 'settings' && <SettingsView />}
        </ViewBoundary>

        {/* Held objects float over whatever view is showing, because the
            selection is the thing that persists across them. Outside the
            boundary: the selection survives a crashed view. */}
        {def.actionBar && <ActionBar />}
      </div>

      {showInspector && <Inspector />}
      <ActivityPanel />

      {def.novaBar && <NovaBar />}
      <CommandPalette />
    </div>
  )
}

const VIEWS_MAIN = viewsInGroup('main')
const VIEWS_FOOT = viewsInGroup('foot')

/** One or two characters standing in for a Space name on the collapsed rail.
 *
 *  Two initials where the name is several words ("Geopolitical Intelligence" →
 *  GI), one letter otherwise — a two-letter stub of a single word reads as an
 *  abbreviation of something and is worse than the initial alone. */
function spaceMonogram(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '·'
  if (words.length === 1) return words[0][0].toUpperCase()
  return (words[0][0] + words[1][0]).toUpperCase()
}
