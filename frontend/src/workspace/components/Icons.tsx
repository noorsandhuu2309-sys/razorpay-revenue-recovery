// The OMNIX icon set.
//
// This replaces the unicode glyphs the workspace shipped with. Those were not
// merely plain — they were ambiguous: ◈ stood for Ask OMNIX, Brief, TERRA Ask
// AND the Space bullets, ◉ for both Agents and Analyst Runs, ◎ for both Intents
// and the Situation Room. With the rail collapsed to icons only, four different
// destinations rendered as the same mark and the rail stopped being navigable.
// Uniqueness here is a functional requirement, not decoration.
//
// House style, inherited from the bundle these replace:
//   * 24x24 viewBox, stroke-only, no fills, currentColor
//   * 1.7 stroke width, round caps and joins
//   * geometry that says what the view DOES, in the product's own vocabulary:
//     nodes and edges for the graph, an orbit for Orbit, a scale for Claims,
//     a dashed outline wherever something is inferred rather than established.
//
// Every icon takes its size from the `size` prop and its colour from
// `currentColor`, so the rail's active/inactive states need no icon-specific
// CSS.

interface IconProps { size?: number; className?: string }

function Svg({ size = 16, className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round"
      // `omx-icon` is what the stylesheet keys the baseline fix off. It must
      // stay on every icon and off the large layout SVGs (Orbit's dial, the
      // map), which size themselves and would be broken by an icon's rules.
      className={className ? `omx-icon ${className}` : 'omx-icon'}
      aria-hidden="true" focusable="false"
    >{children}</svg>
  )
}

// -- brand ------------------------------------------------------------------
/** The OMNIX mark: nested hexagons around a core. Lifted from the retired
 *  bundle, where it was the app's own glyph rather than a borrowed one. */
export function OmnixMark({ size = 22, className }: IconProps) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 40 40" fill="none"
      className={className} aria-hidden="true" focusable="false"
    >
      <path d="M20 2.5 36 11.5V28.5L20 37.5 4 28.5V11.5Z"
            stroke="currentColor" strokeWidth={1.8} />
      <path d="M20 10 29 15.2V25.6L20 30.8 11 25.6V15.2Z"
            stroke="currentColor" strokeWidth={1.4}
            fill="currentColor" fillOpacity={0.16} />
      <circle cx="20" cy="20.4" r="2.4" fill="currentColor" />
    </svg>
  )
}

// -- top level --------------------------------------------------------------
export const IconHome = (p: IconProps) => (
  <Svg {...p}><path d="M3 10.5 12 3l9 7.5" /><path d="M5.5 9.5V20h13V9.5" /><path d="M9.75 20v-5.5h4.5V20" /></Svg>
)

export const IconSpace = (p: IconProps) => (
  <Svg {...p}><path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5Z" /><circle cx="12" cy="12" r="2.2" /></Svg>
)

// -- workspace lenses -------------------------------------------------------
/** Ask: a conversation that carries context, not a bare speech bubble. */
export const IconAsk = (p: IconProps) => (
  <Svg {...p}><path d="M20 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 3.5V6a2.5 2.5 0 0 1 2.5-2.5h11A2.5 2.5 0 0 1 20 6Z" /><circle cx="9" cy="10.2" r="1.05" fill="currentColor" stroke="none" /><circle cx="12.6" cy="10.2" r="1.05" fill="currentColor" stroke="none" /><circle cx="16.2" cy="10.2" r="1.05" fill="currentColor" stroke="none" /></Svg>
)

/** Graph: nodes and the edges between them. */
export const IconGraph = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="5" r="2.3" /><circle cx="5" cy="17.5" r="2.3" /><circle cx="19" cy="17.5" r="2.3" /><path d="M10.4 6.9 6.6 15.5" /><path d="M13.6 6.9l3.8 8.6" /><path d="M7.3 17.5h9.4" /></Svg>
)

/** Compare: two columns weighed against each other. */
export const IconCompare = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="7" height="16" rx="1.4" /><rect x="14" y="4" width="7" height="16" rx="1.4" /><path d="M10.5 12h3" /></Svg>
)

/** Timeline: events on an axis, unevenly spaced because time is. */
export const IconTimeline = (p: IconProps) => (
  <Svg {...p}><path d="M3 12h18" /><circle cx="7" cy="12" r="1.9" /><circle cx="13" cy="12" r="1.9" /><circle cx="18.5" cy="12" r="1.5" /><path d="M7 10.1V6.5" /><path d="M13 13.9v3.6" /></Svg>
)

/** Table: rows of the same thing. */
export const IconTable = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4.5" width="18" height="15" rx="1.6" /><path d="M3 9.5h18" /><path d="M3 14.5h18" /><path d="M9 9.5V19.5" /></Svg>
)

/** Brief: what changed, summarised. */
export const IconBrief = (p: IconProps) => (
  <Svg {...p}><path d="M5 3.5h9l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20Z" /><path d="M13.5 3.5V9h5.5" /><path d="M8.5 13h7" /><path d="M8.5 16.5h4.5" /></Svg>
)

/** Claims: evidence weighed. The scale is the Claim Ledger's whole idea —
 *  supported on one pan, contradicted on the other. */
export const IconClaims = (p: IconProps) => (
  <Svg {...p}><path d="M12 4v16" /><path d="M6.5 7.5h11" /><path d="M3 14.5 6.5 7.5 10 14.5" /><path d="M3 14.5a3.5 3.5 0 0 0 7 0" /><path d="M14 14.5 17.5 7.5 21 14.5" /><path d="M14 14.5a3.5 3.5 0 0 0 7 0" /></Svg>
)

/** CHALLENGE: a proposition at the centre under independent pressure from
 *  every side. Deliberately NOT a tick, shield or gauge — the unit reaches no
 *  verdict, and an icon implying one would be the first thing here to mislead. */
export const IconChallenge = (p: IconProps) => (
  <Svg {...p}><path d="M12 8.5 15.5 12 12 15.5 8.5 12Z" /><path d="M12 3v2.6" /><path d="M12 18.4V21" /><path d="M3 12h2.6" /><path d="M18.4 12H21" /></Svg>
)

/** Sources: stacked documents with a citation anchor. */
export const IconSources = (p: IconProps) => (
  <Svg {...p}><rect x="3.5" y="3.5" width="12" height="15" rx="1.5" /><path d="M7 7.5h5" /><path d="M7 11h5" /><path d="M18.5 8v10.5a2 2 0 0 1-2 2H7" /></Svg>
)

// -- work -------------------------------------------------------------------
/** Outputs: something made and taken out of the workspace. */
export const IconOutputs = (p: IconProps) => (
  <Svg {...p}><path d="M4 15.5V19a1.8 1.8 0 0 0 1.8 1.8h12.4A1.8 1.8 0 0 0 20 19v-3.5" /><path d="M12 3.5v11" /><path d="M7.75 10.25 12 14.5l4.25-4.25" /></Svg>
)

/** Intents: a standing watch on a target. */
export const IconIntents = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3.2" /><path d="M12 1.8v3" /><path d="M12 19.2v3" /><path d="M22.2 12h-3" /><path d="M4.8 12h-3" /></Svg>
)

/** Agents: a worker unit with a live pulse. */
export const IconAgents = (p: IconProps) => (
  <Svg {...p}><rect x="4" y="7" width="16" height="12" rx="2.4" /><path d="M12 7V3.8" /><circle cx="12" cy="2.8" r="1.2" /><path d="M8.5 12.5v2" /><path d="M15.5 12.5v2" /><path d="M2 12v3" /><path d="M22 12v3" /></Svg>
)

// -- TERRA ------------------------------------------------------------------
/** World Map: the flat projection TERRA actually draws. */
export const IconMap = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17" /><path d="M12 3.5c2.6 2.6 2.6 14.4 0 17" /><path d="M12 3.5c-2.6 2.6-2.6 14.4 0 17" /></Svg>
)

/** Intel: a signal picked out of noise. */
export const IconIntel = (p: IconProps) => (
  <Svg {...p}><path d="M3 14.5l3.5-5 3 3.5 3.5-7 3 8 2.5-3.5" /><path d="M3 19.5h18" /></Svg>
)

/** World News: an incoming dispatch. */
export const IconNews = (p: IconProps) => (
  <Svg {...p}><path d="M4.5 4.5h11a1.5 1.5 0 0 1 1.5 1.5v11a2.5 2.5 0 0 0 2.5 2.5H6a1.5 1.5 0 0 1-1.5-1.5Z" /><path d="M19.5 19.5a2.5 2.5 0 0 0 2.5-2.5v-6h-5" /><path d="M7.5 8h5" /><path d="M7.5 11.5h5" /><path d="M7.5 15h3" /></Svg>
)

/** Relationships: a directed link between two parties. */
export const IconRelationships = (p: IconProps) => (
  <Svg {...p}><circle cx="5.5" cy="7" r="2.4" /><circle cx="18.5" cy="17" r="2.4" /><path d="M8 7.6h6.5a2.5 2.5 0 0 1 0 5H9a2.5 2.5 0 0 0 0 5h7" /><path d="M14.2 15.2 16.4 17.6 14 19.6" /></Svg>
)

/** Situation Room: a theatre under watch. */
export const IconSituation = (p: IconProps) => (
  <Svg {...p}><path d="M2.5 12a9.5 9.5 0 0 1 19 0" /><path d="M2.5 12a9.5 9.5 0 0 0 19 0" /><circle cx="12" cy="12" r="3" /><path d="M12 2.5v2" /><path d="M12 19.5v2" /></Svg>
)

/** Analysis: a synthesis drawn out of many inputs. */
export const IconAnalysis = (p: IconProps) => (
  <Svg {...p}><path d="M12 2.8 14.3 9l6.2 0.3-4.9 3.8 1.7 6-5.3-3.5-5.3 3.5 1.7-6L3.5 9.3 9.7 9Z" /></Svg>
)

/** Analyst Runs: work in a queue. */
export const IconRuns = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5.4l3.4 2" /></Svg>
)

/** Query TERRA: interrogating a corpus, not conversing with an assistant.
 *  Distinct from IconAsk on purpose — they were the two rail items users had
 *  to tell apart by reading, which is the whole failure this set exists to
 *  fix. A lens over stacked records rather than a speech bubble. */
export const IconQuery = (p: IconProps) => (
  <Svg {...p}><path d="M3.5 6.5h9" /><path d="M3.5 10.5h6" /><path d="M3.5 14.5h4.5" /><circle cx="15.5" cy="13.5" r="4.5" /><path d="M18.9 16.9 22 20" /></Svg>
)

// -- shell controls ---------------------------------------------------------
export const IconPalette = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M7.5 10l2.5 2.2-2.5 2.2" /><path d="M12.5 14.6h4" /></Svg>
)
export const IconActivity = (p: IconProps) => (
  <Svg {...p}><path d="M3 12h4l2.5-6 4.5 12 2.5-6h4.5" /></Svg>
)
export const IconInspector = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M14.5 4.5v15" /></Svg>
)
export const IconSun = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 1.8v2.4M12 19.8v2.4M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M1.8 12h2.4M19.8 12h2.4M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7" /></Svg>
)
export const IconMoon = (p: IconProps) => (
  <Svg {...p}><path d="M20.5 13.2A8.6 8.6 0 1 1 10.8 3.5a6.7 6.7 0 0 0 9.7 9.7Z" /></Svg>
)
export const IconChevron = (p: IconProps) => (
  <Svg {...p}><path d="M9 5l7 7-7 7" /></Svg>
)

/** Close / dismiss. Replaces the bare `✕` character, which five different
 *  controls used and which renders as a different weight — sometimes as an
 *  emoji — depending on which fallback font the machine reaches for. */
export const IconClose = (p: IconProps) => (
  <Svg {...p}><path d="M6 6l12 12" /><path d="M18 6 6 18" /></Svg>
)

/** Reload the panel's own data. */
export const IconRefresh = (p: IconProps) => (
  <Svg {...p}><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1" /><path d="M20.8 4.2v5.2h-5.2" /></Svg>
)

/** Up one level — the breadcrumb's escape hatch out of a focus drill-down. */
export const IconArrowUp = (p: IconProps) => (
  <Svg {...p}><path d="M12 19.5V5" /><path d="M5.8 11.2 12 5l6.2 6.2" /></Svg>
)

/** Sort direction. One glyph rotated by CSS would invert the arrowhead too, so
 *  the two directions are drawn rather than transformed. */
export const IconSortAsc = (p: IconProps) => (
  <Svg {...p}><path d="M12 19V6" /><path d="M7.5 10.5 12 6l4.5 4.5" /></Svg>
)
export const IconSortDesc = (p: IconProps) => (
  <Svg {...p}><path d="M12 5v13" /><path d="M16.5 13.5 12 18l-4.5-4.5" /></Svg>
)

/** Graph mode: one object centred, neighbours on a ring around it. */
export const IconOrbit = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="2.6" /><ellipse cx="12" cy="12" rx="9" ry="4.6" /><circle cx="21" cy="12" r="1.4" fill="currentColor" stroke="none" /></Svg>
)

/** Graph mode: force-directed mesh of the whole subgraph. */
export const IconNetwork = (p: IconProps) => (
  <Svg {...p}><circle cx="6" cy="6.5" r="2.1" /><circle cx="18" cy="6.5" r="2.1" /><circle cx="12" cy="17.5" r="2.1" /><path d="M8.1 6.5h7.8" /><path d="M7 8.4l3.9 7.2" /><path d="M17 8.4l-3.9 7.2" /></Svg>
)

/** A caution state the user can act on — not an error that already happened. */
export const IconWarning = (p: IconProps) => (
  <Svg {...p}><path d="M12 3.6 22 20.4H2Z" /><path d="M12 9.8v4.6" /><circle cx="12" cy="17.4" r="1.05" fill="currentColor" stroke="none" /></Svg>
)

/** An unresolved signal: raised, not yet acted on. */
export const IconFlag = (p: IconProps) => (
  <Svg {...p}><path d="M5.5 21V3.8" /><path d="M5.5 4.6h11.8l-2.4 4.3 2.4 4.3H5.5" /></Svg>
)

/** Two things exchanged or connected — the Connect verb, and the empty state
 *  for a corpus with no extracted relationships. */
export const IconSwap = (p: IconProps) => (
  <Svg {...p}><path d="M4 8.5h14" /><path d="M14.8 5.2 18.1 8.5 14.8 11.8" /><path d="M20 15.5H6" /><path d="M9.2 12.2 5.9 15.5 9.2 18.8" /></Svg>
)

// -- action verbs -----------------------------------------------------------
// The ActionBar's own vocabulary. These are UI chrome, unlike the object-family
// glyphs beside them in the same bar: those come from the backend ontology
// (`VISUAL`) and carry meaning the legend, table and inspector all share, so
// they stay characters and are NOT replaced here.

/** Focus: rebuild the canvas around one thing. */
export const IconFocus = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="7.5" /><circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" /></Svg>
)

/** Expand: pull the strongest neighbours in from outside the frame. */
export const IconExpand = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="3" /><path d="M12 4.2V8" /><path d="M12 16v3.8" /><path d="M4.2 12H8" /><path d="M16 12h3.8" /><path d="M6.5 6.5 9 9" /><path d="M17.5 17.5 15 15" /><path d="M17.5 6.5 15 9" /><path d="M6.5 17.5 9 15" /></Svg>
)

/** Track: keep a fix on this object and report what moves. */
export const IconTrack = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="2.6" fill="currentColor" stroke="none" /><path d="M12 4v-2.2" /><path d="M20 12h2.2" /></Svg>
)

/** Create: make a new document out of what is held. */
export const IconPlus = (p: IconProps) => (
  <Svg {...p}><path d="M12 5v14" /><path d="M5 12h14" /></Svg>
)

/** Worker transport controls. `‖` in particular was not a pause glyph at all
 *  but U+2016 DOUBLE VERTICAL LINE, a maths "parallel to" operator, which sits
 *  on a different baseline from the `▶` and `■` beside it. */
export const IconPlay = (p: IconProps) => (
  <Svg {...p}><path d="M7.5 4.8 18.5 12 7.5 19.2Z" /></Svg>
)
export const IconPause = (p: IconProps) => (
  <Svg {...p}><path d="M9.2 5v14" /><path d="M14.8 5v14" /></Svg>
)

/** The generic "nothing here yet" mark: an object outline that has not been
 *  filled. Dashed, per the house rule that inferred or absent things are drawn
 *  with a broken stroke. */
export const IconEmptyObject = (p: IconProps) => (
  <Svg {...p}><path d="M12 2.8 21 8v8L12 21.2 3 16V8Z" strokeDasharray="3.2 2.6" /><circle cx="12" cy="12" r="2.4" strokeDasharray="2.4 2.2" /></Svg>
)

// -- voice ------------------------------------------------------------------
export const IconMic = (p: IconProps) => (
  <Svg {...p}><rect x="9" y="2.5" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0" /><path d="M12 17.5V21" /><path d="M8.5 21h7" /></Svg>
)
export const IconSpeaker = (p: IconProps) => (
  <Svg {...p}><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4Z" /><path d="M15.5 9.2a4 4 0 0 1 0 5.6" /><path d="M18.2 6.5a7.6 7.6 0 0 1 0 11" /></Svg>
)
export const IconSpeakerOff = (p: IconProps) => (
  <Svg {...p}><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4Z" /><path d="M16.5 10l4 4" /><path d="M20.5 10l-4 4" /></Svg>
)
/** Speech-to-speech: a loop between listening and speaking. */
export const IconConverse = (p: IconProps) => (
  <Svg {...p}><path d="M20.5 8.5a8.5 8.5 0 0 0-15.6-2" /><path d="M3.5 15.5a8.5 8.5 0 0 0 15.6 2" /><path d="M4.5 2.5v4h4" /><path d="M19.5 21.5v-4h-4" /><circle cx="12" cy="12" r="2.6" /></Svg>
)
export const IconStop = (p: IconProps) => (
  <Svg {...p}><rect x="6" y="6" width="12" height="12" rx="2" /></Svg>
)

// -- graph controls ---------------------------------------------------------
// Kept in the house style — 24x24, stroke-only — so the graph toolbar reads as
// part of the same instrument as the rail rather than as a bolted-on widget.

export const IconZoomIn = (p: IconProps) => (
  <Svg {...p}><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 21 21" /><path d="M10.5 7.5v6M7.5 10.5h6" /></Svg>
)
export const IconZoomOut = (p: IconProps) => (
  <Svg {...p}><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 21 21" /><path d="M7.5 10.5h6" /></Svg>
)
/** Fit: brackets closing on the content. */
export const IconFit = (p: IconProps) => (
  <Svg {...p}><path d="M3 8V3h5" /><path d="M21 8V3h-5" /><path d="M3 16v5h5" /><path d="M21 16v5h-5" /><rect x="8.5" y="8.5" width="7" height="7" rx="1" /></Svg>
)
export const IconFullscreen = (p: IconProps) => (
  <Svg {...p}><path d="M3 9V3h6" /><path d="M21 9V3h-6" /><path d="M3 15v6h6" /><path d="M21 15v6h-6" /></Svg>
)
export const IconFullscreenExit = (p: IconProps) => (
  <Svg {...p}><path d="M9 3v6H3" /><path d="M15 3v6h6" /><path d="M9 21v-6H3" /><path d="M15 21v-6h6" /></Svg>
)
/** Filter: a funnel, but drawn as a narrowing stack so it does not read as a
 *  cocktail glass at 13px. */
export const IconFind = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></Svg>
)
export const IconFilter = (p: IconProps) => (
  <Svg {...p}><path d="M3.5 5h17" /><path d="M6.5 11h11" /><path d="M9.5 17h5" /></Svg>
)
/** Density: three rows at increasing weight. */
export const IconDensity = (p: IconProps) => (
  <Svg {...p}><path d="M4 6.5h16" /><path d="M4 12h16" strokeWidth={2.4} /><path d="M4 17.5h16" strokeWidth={3.2} /></Svg>
)
/** Clusters: three groups with a link between them. */
export const IconClusters = (p: IconProps) => (
  <Svg {...p}><circle cx="7" cy="7" r="3.2" /><circle cx="17.5" cy="8.5" r="2.6" /><circle cx="10" cy="17.5" r="2.8" /><path d="M9.9 8.3l4.9.6" /><path d="M8 10.1l1.3 4.7" /></Svg>
)
/** Layout: a choice of arrangements. */
export const IconLayout = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="7.5" height="7.5" rx="1.2" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.2" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.2" /><circle cx="17.25" cy="17.25" r="3.75" /></Svg>
)
/** Minimap: a frame within a frame. */
export const IconMinimap = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="18" height="16" rx="1.5" /><rect x="12.5" y="12" width="6" height="5.5" rx="1" /></Svg>
)
/** Evidence path: a chain of steps leading somewhere. */
export const IconPath = (p: IconProps) => (
  <Svg {...p}><circle cx="5" cy="18.5" r="2.3" /><circle cx="12" cy="12" r="2.3" /><circle cx="19" cy="5.5" r="2.3" /><path d="M6.7 16.9 10.3 13.6" /><path d="M13.7 10.4 17.3 7.1" /></Svg>
)
/** A single relationship, as an inspectable object. */
export const IconLink = (p: IconProps) => (
  <Svg {...p}><path d="M10 14a4.5 4.5 0 0 0 6.4 0l2.6-2.6a4.5 4.5 0 0 0-6.4-6.4L11.3 6.3" /><path d="M14 10a4.5 4.5 0 0 0-6.4 0L5 12.6a4.5 4.5 0 0 0 6.4 6.4l1.3-1.3" /></Svg>
)
/** Settings: the conventional gear, drawn as a ring with eight lobes rather
 *  than a traced cog so it stays legible at 15px. */
export const IconSettings = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3.1" />
    <path d="M12 2.6v2.6M12 18.8v2.6M2.6 12h2.6M18.8 12h2.6M5.3 5.3l1.9 1.9M16.8 16.8l1.9 1.9M18.7 5.3l-1.9 1.9M7.2 16.8l-1.9 1.9" />
  </Svg>
)
/** Send. An arrow, not a paper plane: the plane reads as "mail", and this
 *  submits a turn in a conversation. */
export const IconSend = (p: IconProps) => (
  <Svg {...p}><path d="M4.5 12h14" /><path d="m12.5 6 6 6-6 6" /></Svg>
)
export const IconCopy = (p: IconProps) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M15 6.5A2.5 2.5 0 0 0 12.5 4h-6A2.5 2.5 0 0 0 4 6.5v6A2.5 2.5 0 0 0 6.5 15" />
  </Svg>
)
export const IconEdit = (p: IconProps) => (
  <Svg {...p}><path d="M4 20h4L19 9a2.12 2.12 0 0 0-3-3L5 17z" /><path d="m14.5 6.5 3 3" /></Svg>
)
export const IconCheck = (p: IconProps) => (
  <Svg {...p}><path d="m5 12.5 4.5 4.5L19 7" /></Svg>
)
export const IconTrash = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 7h16" /><path d="M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7" />
    <path d="M6.2 7v12.3A1.7 1.7 0 0 0 7.9 21h8.2a1.7 1.7 0 0 0 1.7-1.7V7" />
  </Svg>
)
/** Regenerate: a full circular arrow, distinct from IconRefresh's partial arc
 *  so "ask again" and "reload the data" are not the same glyph. */
export const IconRegen = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 12a8 8 0 1 1-2.6-5.9" /><path d="M20 4.5V10h-5.5" />
  </Svg>
)
/** Attach. */
export const IconAttach = (p: IconProps) => (
  <Svg {...p}>
    <path d="M20 11.5 12.2 19.3a4.6 4.6 0 0 1-6.5-6.5l8.1-8.1a3.1 3.1 0 0 1 4.4 4.4l-8.1 8.1a1.6 1.6 0 0 1-2.2-2.2l7.4-7.4" />
  </Svg>
)
/** Save to disk. Arrow into a tray — distinct from IconArrowUp's bare arrow. */
export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5v11" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
    <path d="M4.5 17.5v1.8A1.7 1.7 0 0 0 6.2 21h11.6a1.7 1.7 0 0 0 1.7-1.7v-1.8" />
  </Svg>
)
/** A model: a chip with pins. Used wherever a specific model is being named or
 *  chosen, so it never collides with IconAgents (which means a ROLE). */
export const IconModel = (p: IconProps) => (
  <Svg {...p}>
    <rect x="7" y="7" width="10" height="10" rx="1.8" />
    <path d="M10 3.5V7M14 3.5V7M10 17v3.5M14 17v3.5" />
    <path d="M3.5 10H7M3.5 14H7M17 10h3.5M17 14h3.5" />
  </Svg>
)

/** HELIX: a double helix — two strands crossing, with base pairs between them.
 *
 *  The only biological mark in the set, which is the point: HELIX is the one
 *  destination that reads a domain corpus rather than the workspace, and the
 *  rail should say so at a glance. Distinct from IconGraph (nodes and edges)
 *  and IconRelationships (a directed pair) at 16px, which is the size that
 *  matters. */
export const IconHelix = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 3c0 5 8 5 8 9s-8 4-8 9" />
    <path d="M16 3c0 5-8 5-8 9s8 4 8 9" />
    <path d="M9.6 7.2h4.8M8.2 12h7.6M9.6 16.8h4.8" />
  </Svg>
)
