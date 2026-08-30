// The layer registry: one row per thing the map can draw.
//
// This is the file the sidebar is built from, and the reason the sidebar can be
// generated rather than hand-written. Adding a layer means adding a row here
// and a `build` case — the toggle, the group, the hint and the "needs a point
// first" state all follow.
//
// Two conventions worth keeping:
//
//   * **`mapLayers` lists the MapLibre layer ids a toggle controls.** Several
//     conceptual layers are two or three real ones (a fill plus its outline, a
//     line plus its casing), and the toggle has to hide all of them together or
//     you get a country outline with no country.
//
//   * **`needs` is declarative, not enforced by hiding.** A layer that needs a
//     focus point stays toggleable with none set; the sidebar shows why it is
//     empty instead of disabling the control. A disabled toggle with no
//     explanation is the most annoying possible answer to "why is nothing
//     showing".

import type { ExpressionSpecification } from 'maplibre-gl'

export type LayerId =
  | 'basemap' | 'graticule' | 'countries' | 'risk' | 'borders' | 'cities'
  | 'objects' | 'highlights' | 'places' | 'route' | 'geofences' | 'saved'
  | 'radar' | 'air' | 'measure'

export type LayerGroup = 'Base' | 'Intelligence' | 'Spatial' | 'Environment'

export interface LayerDef {
  id: LayerId
  label: string
  group: LayerGroup
  hint: string
  /** On for a user who has never touched the controls. */
  on: boolean
  /** MapLibre layer ids this toggle shows and hides. */
  mapLayers: string[]
  /** What has to exist before this layer can draw anything. */
  needs?: 'focus' | 'workspace' | 'search' | 'route'
}

export const LAYERS: readonly LayerDef[] = [
  // -- base ---------------------------------------------------------------
  {
    id: 'basemap', label: 'Basemap', group: 'Base', on: true,
    hint: 'Streets, terrain and labels from OpenStreetMap',
    mapLayers: ['basemap'],
  },
  {
    id: 'graticule', label: 'Graticule', group: 'Base', on: false,
    hint: 'Meridians and parallels every 30°',
    mapLayers: ['graticule-line'],
  },
  {
    id: 'borders', label: 'Admin borders', group: 'Base', on: false,
    hint: 'First-level internal boundaries',
    mapLayers: ['borders-line'],
  },
  {
    id: 'cities', label: 'Cities', group: 'Base', on: true,
    hint: 'Ranked by population, thinned as you zoom out',
    mapLayers: ['cities-dot', 'cities-label'],
  },

  // -- intelligence — what the old World Map did --------------------------
  {
    id: 'countries', label: 'Countries', group: 'Intelligence', on: true,
    hint: 'Clickable country shapes — selects the workspace object',
    mapLayers: ['countries-fill', 'countries-line'],
  },
  {
    id: 'risk', label: 'Risk heatmap', group: 'Intelligence', on: true,
    hint: "ORACLE's per-country risk assessment, where it has scored one",
    mapLayers: ['countries-risk'],
  },
  {
    id: 'objects', label: 'Workspace objects', group: 'Intelligence', on: false,
    hint: 'Objects in this Space that carry coordinates',
    mapLayers: ['objects-dot', 'objects-label'], needs: 'workspace',
  },
  {
    // The only entry whose `mapLayers` is empty. Highlights are DOM markers,
    // not a MapLibre layer, so the generic show/hide loop has nothing to do
    // for it — MapView reads `layers.highlights` directly when it diffs the
    // pins. It still belongs in this registry: a layer the user can see is a
    // layer the user must be able to turn off, and the sidebar is generated
    // from here.
    id: 'highlights', label: 'News highlights', group: 'Intelligence', on: true,
    hint: "Where today's stories are breaking, sized by how many outlets carry them",
    mapLayers: [],
  },

  // -- spatial ------------------------------------------------------------
  {
    id: 'places', label: 'Places', group: 'Spatial', on: true,
    hint: 'POI search results, clustered',
    mapLayers: ['places-cluster', 'places-cluster-count', 'places-dot',
                'places-label'],
    needs: 'search',
  },
  {
    id: 'route', label: 'Routes', group: 'Spatial', on: true,
    hint: 'Alternatives, with the active one highlighted',
    mapLayers: ['route-casing', 'route-line', 'route-step'], needs: 'route',
  },
  {
    id: 'saved', label: 'Saved places', group: 'Spatial', on: true,
    hint: 'Home, college, work — anything TERRA remembers',
    mapLayers: ['saved-dot', 'saved-label'], needs: 'workspace',
  },
  {
    id: 'geofences', label: 'Geofences', group: 'Spatial', on: true,
    hint: 'Watched areas and whether you are inside them',
    mapLayers: ['fence-fill', 'fence-line', 'fence-label'], needs: 'workspace',
  },
  {
    id: 'measure', label: 'Measure', group: 'Spatial', on: false,
    hint: 'Click the map to measure distance along a path',
    mapLayers: ['measure-line', 'measure-dot', 'measure-label'],
  },

  // -- environment --------------------------------------------------------
  {
    id: 'radar', label: 'Precipitation radar', group: 'Environment', on: false,
    hint: 'Live radar from RainViewer — animates the last two hours',
    mapLayers: ['radar-raster'],
  },
  {
    id: 'air', label: 'Air quality', group: 'Environment', on: false,
    hint: 'Sampled over a grid around the focus point, in one API call',
    mapLayers: ['air-heat', 'air-point'], needs: 'focus',
  },
]

export const GROUPS: LayerGroup[] = ['Base', 'Intelligence', 'Spatial', 'Environment']

export type LayerState = Record<LayerId, boolean>

export const defaultLayerState = (): LayerState =>
  Object.fromEntries(LAYERS.map((l) => [l.id, l.on])) as LayerState

/** Persisted map state.
 *
 *  Reading it is deliberately forgiving: a stored layer set from an older
 *  build will be missing ids that exist now, so it is merged over the defaults
 *  rather than used as-is. Using it directly meant a layer added after the
 *  user's last visit arrived as `undefined`, which MapLibre reads as hidden —
 *  a new feature that ships invisible to exactly the users who have used the
 *  product before.
 */
const STORE_KEY = 'omx.map.state.v1'

export interface PersistedMap {
  center?: [number, number]
  zoom?: number
  layers?: Partial<LayerState>
  sidebar?: boolean
  panel?: string
}

export function loadMapState(): PersistedMap {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    return raw ? (JSON.parse(raw) as PersistedMap) : {}
  } catch {
    return {}
  }
}

export function saveMapState(state: PersistedMap): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state))
  } catch {
    /* private mode, quota, or a browser that refuses — never worth failing on */
  }
}

/** The risk fill expression.
 *
 *  Data-driven so 200 countries are one layer. `hasRisk` gates it because a
 *  country ORACLE has not scored must render as *unshaded*, not as the bottom
 *  of the scale — "no assessment" and "assessed as calm" are different claims
 *  and colouring them the same makes the map confidently wrong.
 */
export const riskFill: ExpressionSpecification = [
  'case',
  ['boolean', ['get', 'hasRisk'], false],
  ['get', 'riskColor'],
  'rgba(0,0,0,0)',
]

/** Opacity for the risk layer: stronger where the score is higher, and lifted
 *  on hover so the country under the cursor reads as live. */
/** Opacity from the country's own risk score, before the zoom fade. */
const riskAlpha: ExpressionSpecification = [
  'case',
  ['boolean', ['feature-state', 'hover'], false], 0.72,
  ['interpolate', ['linear'], ['get', 'risk'], 0, 0.22, 100, 0.62],
]

// ...faded out as the camera comes down. The choropleth answers a world-scale
// question, and at street zoom it is a single flat wash over the whole
// viewport: a search for an address in a high-risk country landed on an
// unreadable red rectangle with the streets somewhere underneath it. Full
// strength to zoom 4 (a country in frame), a tint by zoom 9, where the
// basemap is the thing being read.
//
// The zoom interpolation has to be the OUTER expression and the data one the
// inner: MapLibre rejects `zoom` anywhere but as the input of a top-level
// step/interpolate, and it rejects it by dropping the entire layer — the
// choropleth simply stops existing, with one console line to say so.
export const riskOpacity: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  4, riskAlpha,
  9, ['*', riskAlpha, 0.18],
]
