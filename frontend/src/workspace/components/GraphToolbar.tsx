// The graph's own controls: a toolbar and a filter panel.
//
// Both live in one file because they are one instrument — the filter panel is
// opened from the toolbar and shares its surface treatment — and because the
// rule they follow is the same as the action bar's:
//
//   **only offer a control that is real.** The layout menu lists Force and
//   Community and nothing else, because those are the two layouts that exist.
//   A Hierarchical entry that does nothing is worse than no menu at all, since
//   it teaches the reader that the controls are decorative.
//
// Sizing is deliberate: 26px square buttons, 11px labels. The brief asks for
// these not to be oversized, and a graph toolbar that competes with the graph
// is a toolbar nobody trusts.

import { useEffect, useRef } from 'react'
import { useWorkspace } from '../store/workspace'
import {
  DENSITY, filtersActive, useGraphUi,
  type Density, type GraphLayout,
} from '../store/graphUi'
import { CLASS_COLOR, CLASS_LABEL, type GraphModel, type RelClass } from '../lib/graphModel'
import {
  IconClose, IconDensity, IconExpand, IconFilter, IconFind, IconFit, IconFocus,
  IconFullscreen, IconFullscreenExit, IconLayout, IconMinimap, IconRefresh,
  IconZoomIn, IconZoomOut,
} from './Icons'

export function GraphToolbar({ onZoom, onFit, onFitSelection }: {
  onZoom: (factor: number) => void
  onFit: () => void
  onFitSelection: () => void
}) {
  const mode = useGraphUi((s) => s.mode)
  const layout = useGraphUi((s) => s.layout)
  const setLayout = useGraphUi((s) => s.setLayout)
  const density = useGraphUi((s) => s.density)
  const setDensity = useGraphUi((s) => s.setDensity)
  const focusMode = useGraphUi((s) => s.focusMode)
  const toggleFocusMode = useGraphUi((s) => s.toggleFocusMode)
  const filters = useGraphUi((s) => s.filters)
  const filtersOpen = useGraphUi((s) => s.filtersOpen)
  const setFiltersOpen = useGraphUi((s) => s.setFiltersOpen)
  const findOpen = useGraphUi((s) => s.findOpen)
  const setFindOpen = useGraphUi((s) => s.setFindOpen)
  const query = useGraphUi((s) => s.query)
  const minimap = useGraphUi((s) => s.minimap)
  const setMinimap = useGraphUi((s) => s.setMinimap)
  const fullscreen = useGraphUi((s) => s.fullscreen)
  const setFullscreen = useGraphUi((s) => s.setFullscreen)
  const loadGraph = useWorkspace((s) => s.loadGraph)
  const selected = useWorkspace((s) => s.selected)
  const primary = useWorkspace((s) => s.primary)
  const expandRing = useWorkspace((s) => s.expandRing)
  const expandAll = useWorkspace((s) => s.expandAll)

  // All three modes now have a camera — Orbit drives a viewBox, the other two
  // drive cosmos — so zoom and fit are offered everywhere. Only the minimap is
  // still Network-and-Clusters: the orbit is bounded by construction and a
  // minimap of one dial shows nothing the dial does not.
  const spatial = mode !== 'orbit'

  const changeDensity = (d: Density) => {
    setDensity(d)
    // Density is a fetch parameter, so it has to go back to the server. This
    // is the only control here that does.
    void loadGraph()
  }

  return (
    <div className="omx-graph-toolbar" role="toolbar" aria-label="Graph controls">
      <div className="grp">
        <button className="omx-gbtn" onClick={() => onZoom(1.35)}
                title="Zoom in" aria-label="Zoom in"><IconZoomIn size={13} /></button>
        <button className="omx-gbtn" onClick={() => onZoom(1 / 1.35)}
                title="Zoom out" aria-label="Zoom out"><IconZoomOut size={13} /></button>
        <button className="omx-gbtn" onClick={onFit}
                title="Fit the whole graph" aria-label="Fit graph"><IconFit size={13} /></button>
      </div>

      <div className="grp">
        <button
          className={`omx-gbtn ${focusMode ? 'on' : ''}`}
          onClick={() => { toggleFocusMode(); if (!focusMode) onFitSelection() }}
          disabled={!selected.length}
          title={selected.length
            ? 'Focus — isolate the selection and its neighbourhood'
            : 'Select an object to focus on it'}
          aria-pressed={focusMode}
        ><IconFocus size={13} /></button>
        {spatial && (
          <button className={`omx-gbtn ${minimap ? 'on' : ''}`}
                  onClick={() => setMinimap(!minimap)}
                  title="Minimap" aria-label="Toggle minimap" aria-pressed={minimap}>
            <IconMinimap size={13} />
          </button>
        )}
      </div>

      <div className="grp">
        {/* Density is the control that decides how much of the Space is on
            screen at all. It reads as a segmented control rather than a menu
            because it is three exclusive steps and the reader should be able
            to see which one they are on without opening anything. */}
        <span className="omx-gbtn label" title="How much of the Space to load">
          <IconDensity size={13} />
        </span>
        {(Object.keys(DENSITY) as Density[]).map((d) => (
          <button
            key={d}
            className={`omx-gseg ${density === d ? 'on' : ''}`}
            onClick={() => changeDensity(d)}
            title={DENSITY[d].hint}
          >{DENSITY[d].label}</button>
        ))}
      </div>

      {mode !== 'orbit' && (
        <div className="grp">
          <span className="omx-gbtn label" title="Layout"><IconLayout size={13} /></span>
          {/* Only what is implemented. */}
          {(['force', 'community'] as GraphLayout[]).map((l) => (
            <button
              key={l}
              className={`omx-gseg ${layout === l ? 'on' : ''}`}
              onClick={() => setLayout(l)}
              title={l === 'force'
                ? 'Force-directed — how is all of this shaped'
                : 'Community — pull each cluster together'}
            >{l === 'force' ? 'Force' : 'Community'}</button>
          ))}
        </div>
      )}

      {/* Progressive disclosure. Expand belongs on the toolbar rather than in
          its own corner: it changes what is on the canvas, exactly as density
          and the filters do, and a graph that shows part of a Space needs the
          way to see more to sit beside the way to see less. */}
      {primary && (
        <div className="grp">
          <span className="omx-gbtn label" title="Expand the selection">
            <IconExpand size={13} />
          </span>
          <button className="omx-gseg" onClick={() => void expandRing(primary, 1)}
                  title="Pull in this object's own neighbours">+1</button>
          <button className="omx-gseg" onClick={() => void expandRing(primary, 2)}
                  title="Also expand everything that arrives with it">+2</button>
          <button className="omx-gseg" onClick={() => void expandAll(primary)}
                  title="Everything reachable within four hops">All</button>
        </div>
      )}

      <div className="grp">
        {/* Find sits with the other things that change what you can see, and
            costs one 26px button rather than a permanent input — the bar is
            already the widest thing on the stage. */}
        <button
          className={`omx-gbtn ${findOpen ? 'on' : ''} ${query.trim() ? 'live' : ''}`}
          onClick={() => setFindOpen(!findOpen)}
          title="Find an object by name  /"
          aria-label="Find an object"
          aria-expanded={findOpen}
        ><IconFind size={13} /></button>
        <button
          className={`omx-gbtn ${filtersOpen ? 'on' : ''} ${filtersActive(filters) ? 'live' : ''}`}
          onClick={() => setFiltersOpen(!filtersOpen)}
          title="Filter the graph"
          aria-expanded={filtersOpen}
        ><IconFilter size={13} /></button>
        <button className="omx-gbtn" onClick={() => void loadGraph()}
                title="Reset the graph to the current density"
                aria-label="Reset graph"><IconRefresh size={13} /></button>
        <button
          className={`omx-gbtn ${fullscreen ? 'on' : ''}`}
          onClick={() => setFullscreen(!fullscreen)}
          title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          aria-pressed={fullscreen}
        >{fullscreen ? <IconFullscreenExit size={13} /> : <IconFullscreen size={13} />}</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

/** Built from what is actually on the canvas.
 *
 *  A filter panel listing the ontology's eighteen families when four are
 *  present is a panel of dead switches — the reader flicks one and nothing
 *  changes, and they stop believing the rest. So the family list comes from the
 *  model, and the relation classes come from the edges that exist. */
export function GraphFilters({ model, hiddenCount }: {
  model: GraphModel
  /** How many objects the current filters are removing. Shown because a
   *  filter that silently shrinks the graph is indistinguishable from a graph
   *  that has nothing in it. */
  hiddenCount: number
}) {
  const open = useGraphUi((s) => s.filtersOpen)
  const setOpen = useGraphUi((s) => s.setFiltersOpen)
  const filters = useGraphUi((s) => s.filters)
  const patch = useGraphUi((s) => s.patchFilters)
  const toggleFamily = useGraphUi((s) => s.toggleFamily)
  const toggleClass = useGraphUi((s) => s.toggleClass)
  const reset = useGraphUi((s) => s.resetFilters)
  const ref = useRef<HTMLDivElement>(null)

  // Escape closes the panel. It defers to nothing else here — the palette owns
  // Escape while it is open and this panel cannot be open at the same time.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  if (!open) return null

  return (
    <div className="omx-graph-filters" ref={ref} role="dialog" aria-label="Graph filters">
      <div className="fhead">
        <span className="omx-label">Filter</span>
        <div style={{ flex: 1 }} />
        {hiddenCount > 0 && (
          <span className="omx-label hid">{hiddenCount} hidden</span>
        )}
        <button className="omx-btn icon" onClick={reset} title="Clear all filters">
          Reset
        </button>
        <button className="omx-btn icon" onClick={() => setOpen(false)}
                aria-label="Close filters"><IconClose size={13} /></button>
      </div>

      <div className="fsec">
        <div className="omx-label">Objects</div>
        {model.legend.map((f) => {
          const on = !filters.hiddenFamilies.includes(f.family)
          return (
            <label className="frow" key={f.family}>
              <input type="checkbox" checked={on} onChange={() => toggleFamily(f.family)} />
              {/* The swatch repeats the canvas's own shape, not just its
                  colour — the brief is explicit that information must never be
                  carried by colour alone. */}
              <span className="sw" style={{ color: f.color }} aria-hidden="true">
                {f.shape === 'diamond' ? '◆' : f.shape === 'square' ? '■'
                  : f.shape === 'triangle' ? '▲' : f.shape === 'hexagon' ? '⬢'
                    : f.shape === 'pentagon' ? '⬟' : '●'}
              </span>
              <span className="l">{f.label}</span>
              <span className="n omx-mono">{f.count}</span>
            </label>
          )
        })}
      </div>

      {model.classes.length > 0 && (
        <div className="fsec">
          <div className="omx-label">Relationships</div>
          {model.classes.map(({ cls, count }) => {
            const on = !filters.hiddenClasses.includes(cls as RelClass)
            return (
              <label className="frow" key={cls}>
                <input type="checkbox" checked={on} onChange={() => toggleClass(cls as RelClass)} />
                <span className="sw line" style={{ background: CLASS_COLOR[cls as RelClass] }} aria-hidden="true" />
                <span className="l">{CLASS_LABEL[cls as RelClass]}</span>
                <span className="n omx-mono">{count}</span>
              </label>
            )
          })}
        </div>
      )}

      <div className="fsec">
        <div className="omx-label">Minimum strength</div>
        <div className="frange">
          <input
            type="range" min={0} max={0.9} step={0.05}
            value={filters.minStrength}
            onChange={(e) => patch({ minStrength: Number(e.target.value) })}
            aria-label="Minimum relationship strength"
          />
          <span className="omx-mono v">
            {filters.minStrength === 0 ? 'any' : `${Math.round(filters.minStrength * 100)}%`}
          </span>
        </div>
      </div>

      <div className="fsec">
        {/* Recency is applied to OBJECTS, not to links. The subgraph payload
            carries no timestamp on an edge — first and last observed live on
            the relationship record — so a "links in the last 7 days" filter
            here would have nothing to read and would silently do nothing. */}
        <div className="omx-label">Objects updated within</div>
        <div className="fchips">
          {([[null, 'Any'], [7, '7 days'], [30, '30 days'], [90, '90 days']] as const).map(
            ([days, label]) => (
              <button
                key={label}
                className={`omx-chip sm ${filters.recencyDays === days ? 'on' : ''}`}
                onClick={() => patch({ recencyDays: days })}
              >{label}</button>
            ))}
        </div>
      </div>

      <div className="fsec">
        <label className="frow">
          <input type="checkbox" checked={filters.trackedOnly}
                 onChange={(e) => patch({ trackedOnly: e.target.checked })} />
          <span className="l">Tracked objects only</span>
        </label>
        <label className="frow">
          <input type="checkbox" checked={filters.evidenceOnly}
                 onChange={(e) => patch({ evidenceOnly: e.target.checked })} />
          <span className="l">Evidence-backed only</span>
          <span className="omx-label" style={{ marginLeft: 'auto' }}>excludes AI-inferred</span>
        </label>
      </div>
    </div>
  )
}
