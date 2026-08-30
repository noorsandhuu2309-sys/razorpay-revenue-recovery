// Appearance: the mode x accent model, and the bridge from CSS into <canvas>.
//
// Two independent axes, stamped on <html> as `data-theme` and `data-accent`.
// workspace.css owns every colour; this module owns only which pair of
// attributes is set, and reads colours back out for the two surfaces that
// cannot use CSS at all.
//
// It is deliberately not part of the Zustand store. The attributes have to be
// applied before React mounts (index.html runs the same two reads inline, or
// the first paint is dark and then snaps), and a store that is written before
// it exists is worse than a module that owns a DOM attribute.

import { useSyncExternalStore } from 'react'

export type Mode = 'dark' | 'light'
export type AccentId = 'gold' | 'red' | 'blue' | 'green' | 'mono'

export interface AccentDef {
  id: AccentId
  /** Shown under the swatch row. */
  label: string
  /** What it reads as in each mode — used for titles, not for painting. */
  hint: string
}

/** The five accents, in picker order: the house colour first, then warm to
 *  cool, then the achromatic pair last because it is the odd one out.
 *
 *  No colours here. A swatch is painted by putting `data-accent` on it and
 *  letting workspace.css's ramp blocks do the work, so this list cannot drift
 *  out of sync with the palette. */
export const ACCENTS: readonly AccentDef[] = [
  { id: 'gold', label: 'Gold', hint: 'The house colour — TERRA gold' },
  { id: 'red', label: 'Red', hint: 'Vermilion' },
  { id: 'blue', label: 'Blue', hint: 'Azure' },
  { id: 'green', label: 'Green', hint: 'Emerald' },
  { id: 'mono', label: 'Mono', hint: 'White on black, black on white' },
] as const

export const DEFAULT_ACCENT: AccentId = 'gold'
export const DEFAULT_MODE: Mode = 'dark'

// `omx-theme` predates the accent axis and already holds people's dark/light
// choice, so it keeps its name and its values rather than being migrated.
const MODE_KEY = 'omx-theme'
const ACCENT_KEY = 'omx-accent'

const isAccent = (v: unknown): v is AccentId =>
  ACCENTS.some((a) => a.id === v)

export interface Appearance { mode: Mode; accent: AccentId }

/** What the DOM currently says. The attributes are the state — there is no
 *  second copy to fall out of step with them. */
export function current(): Appearance {
  if (typeof document === 'undefined') return { mode: DEFAULT_MODE, accent: DEFAULT_ACCENT }
  const root = document.documentElement
  const accent = root.getAttribute('data-accent')
  return {
    mode: root.getAttribute('data-theme') === 'light' ? 'light' : 'dark',
    accent: isAccent(accent) ? accent : DEFAULT_ACCENT,
  }
}

/** Read the saved pair, falling back to the OS preference for mode.
 *
 *  Only consulted when nothing is stored: an explicit choice outranks the OS,
 *  and re-reading the media query on every load would silently undo it. */
export function stored(): Appearance {
  let mode: Mode = DEFAULT_MODE
  let accent: AccentId = DEFAULT_ACCENT
  try {
    const m = localStorage.getItem(MODE_KEY)
    if (m === 'light' || m === 'dark') mode = m
    else if (typeof matchMedia === 'function'
      && matchMedia('(prefers-color-scheme: light)').matches) mode = 'light'
    const a = localStorage.getItem(ACCENT_KEY)
    if (isAccent(a)) accent = a
  } catch { /* private mode */ }
  return { mode, accent }
}

const listeners = new Set<() => void>()
let swapTimer: ReturnType<typeof setTimeout> | undefined

/** Write the pair to <html>, persist it, and tell React.
 *
 *  `omx-swapping` turns on the blanket colour transition in workspace.css for
 *  the length of the cross-fade. It is removed again rather than left on,
 *  because a permanent transition on every element would also tween hover —
 *  a 260ms lag on a button highlight feels broken. */
export function apply({ mode, accent }: Appearance, { animate = true } = {}) {
  const root = document.documentElement
  const before = current()
  if (before.mode === mode && before.accent === accent) return

  if (animate) {
    root.classList.add('omx-swapping')
    clearTimeout(swapTimer)
    swapTimer = setTimeout(() => root.classList.remove('omx-swapping'), 320)
  }
  root.setAttribute('data-theme', mode)
  root.setAttribute('data-accent', accent)
  try {
    localStorage.setItem(MODE_KEY, mode)
    localStorage.setItem(ACCENT_KEY, accent)
  } catch { /* private mode */ }
  for (const fn of listeners) fn()
}

export const setMode = (mode: Mode) => apply({ ...current(), mode })
export const setAccent = (accent: AccentId) => apply({ ...current(), accent })
export const toggleMode = () =>
  setMode(current().mode === 'light' ? 'dark' : 'light')

/** Put the saved pair on <html>. Idempotent, and safe to call after the inline
 *  script in index.html has already done it — which is the normal case. */
export function hydrate() {
  const root = document.documentElement
  const { mode, accent } = stored()
  root.setAttribute('data-theme', mode)
  root.setAttribute('data-accent', accent)
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

// The snapshot must be referentially stable or useSyncExternalStore loops:
// current() builds a fresh object every call. Cached and rebuilt only when the
// attributes actually change.
let snapshot: Appearance = { mode: DEFAULT_MODE, accent: DEFAULT_ACCENT }
let snapshotKey = ''
function getSnapshot(): Appearance {
  const a = current()
  const key = `${a.mode}|${a.accent}`
  if (key !== snapshotKey) { snapshotKey = key; snapshot = a }
  return snapshot
}

/** Subscribe a component to the live pair. */
export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribe, getSnapshot, () => snapshot)
}

// ---------------------------------------------------------------------------
// Canvas
// ---------------------------------------------------------------------------

/** Everything the two canvas renderers need, in colours a 2D context accepts.
 *
 *  A canvas cannot use CSS variables — `fillStyle` takes a literal — so the
 *  accent has to be read out of the cascade and handed over. Doing that with
 *  getComputedStyle is a forced style recalculation, far too expensive to
 *  repeat 60 times a second, and doing it with a MutationObserver on <html> is
 *  how this codebase has frozen itself before. So: two attribute reads per
 *  frame (free) form a cache key, and the expensive read happens only when
 *  that key changes. */
export interface Ink {
  mode: Mode
  accent: AccentId
  /** The accent, and its two steps. */
  a: string
  aBright: string
  aDim: string
  /** `r, g, b` — compose with `rgba(${ink.aRgb}, 0.5)`. */
  aRgb: string
  /** The page ground, for canvases that clear to it. */
  ground: string
  /** Behind labels and panels drawn over the canvas. */
  plate: string
  platePlain: string
  /** Label text at three emphases. */
  strong: (alpha: number) => string
  normal: (alpha: number) => string
  faint: (alpha: number) => string
  /** Outline drawn around a node, to separate it from what is behind it. */
  nodeEdge: string
  hoverRing: string
  panelLine: string
  minimapDot: string
  /** `rgba` of the accent at an arbitrary alpha. */
  accentAt: (alpha: number) => string
}

/** Ink text is the mode's own text colour, not the accent's: a label is a
 *  label in every theme, and tinting body text with the accent is what makes a
 *  canvas look like a skin rather than a surface. */
const TEXT: Record<Mode, { strong: string; normal: string; faint: string }> = {
  dark: { strong: '240,234,217', normal: '226,220,204', faint: '178,171,155' },
  light: { strong: '26,23,16', normal: '48,43,32', faint: '96,90,76' },
}
const PLATE: Record<Mode, string> = { dark: '6,6,6', light: '255,254,251' }
const MINIMAP: Record<Mode, string> = { dark: '150,144,130', light: '90,84,70' }
const HOVER: Record<Mode, string> = { dark: '236,230,214', light: '40,35,24' }

const read = (style: CSSStyleDeclaration, name: string, fallback: string) =>
  style.getPropertyValue(name).trim() || fallback

function build(): Ink {
  const { mode, accent } = current()
  const s = getComputedStyle(document.documentElement)
  const a = read(s, '--omx-accent', '#d3ad55')
  const aRgb = read(s, '--omx-accent-rgb', '211, 173, 85')
  const t = TEXT[mode]
  const plate = PLATE[mode]
  return {
    mode,
    accent,
    a,
    aBright: read(s, '--omx-accent-bright', '#e8cd8b'),
    aDim: read(s, '--omx-accent-dim', '#a8863f'),
    aRgb,
    ground: read(s, '--omx-ground', mode === 'light' ? '#f7f5ef' : '#070707'),
    plate: `rgba(${plate},${mode === 'light' ? 0.9 : 0.72})`,
    platePlain: `rgba(${plate},${mode === 'light' ? 0.72 : 0.5})`,
    strong: (alpha) => `rgba(${t.strong},${alpha})`,
    normal: (alpha) => `rgba(${t.normal},${alpha})`,
    faint: (alpha) => `rgba(${t.faint},${alpha})`,
    nodeEdge: `rgba(${plate},${mode === 'light' ? 0.9 : 0.85})`,
    hoverRing: `rgba(${HOVER[mode]},${mode === 'light' ? 0.5 : 0.45})`,
    panelLine: `rgba(${aRgb},${mode === 'light' ? 0.28 : 0.22})`,
    minimapDot: `rgba(${MINIMAP[mode]},${mode === 'light' ? 0.4 : 0.42})`,
    accentAt: (alpha) => `rgba(${aRgb},${alpha})`,
  }
}

let cached: Ink | null = null
let cacheKey = ''

/** The live ink. Safe to call once per frame. */
export function inkNow(): Ink {
  const root = document.documentElement
  const key = `${root.getAttribute('data-theme')}|${root.getAttribute('data-accent')}`
  if (!cached || key !== cacheKey) {
    cacheKey = key
    cached = build()
  }
  return cached
}
