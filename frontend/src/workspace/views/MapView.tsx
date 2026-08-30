// The World Map — one map for everything geographic OMNIX knows.
//
// This replaced a hand-rolled canvas equirectangular renderer, and the reason
// is worth stating because the canvas was not bad: it drew 200 country
// polygons, a risk choropleth and 1251 city labels at 60fps, and it selected
// workspace objects. What it could not do was show a street. An equirectangular
// projection of `world.json` has no tiles, so POIs, routes and geofences had
// nowhere to live and had to be a second map in a second view — which meant two
// destinations in the rail for one question ("what is where") and two mental
// models for one map.
//
// So: MapLibre, from world zoom to street zoom, with everything the canvas did
// re-expressed as layers. Countries are a GeoJSON fill whose colour is a
// data-driven expression rather than 200 per-country paints. Clicking one still
// selects the same workspace object the graph would.
//
// The sidebar is generated from `map/layers.ts` — every toggle, group and
// "needs a point first" hint follows from that registry, so adding a layer is a
// row there plus a `build` case here, never a change to the sidebar.
//
// Two things this file deliberately does NOT do:
//   * It does not read state back off the map. MapLibre is told what to draw;
//     React owns selection, results and the focus point. Reading features back
//     is how the graph canvas ended up unable to render its data anywhere else.
//   * It does not request the user's location on mount. Geolocation is a click,
//     always, and every panel works from a map-clicked point instead.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AttributionControl, Map as MapLibreMap, Marker, NavigationControl, ScaleControl,
  LngLatBounds, setWorkerUrl,
  type GeoJSONSource, type MapMouseEvent, type RasterTileSource,
  type StyleSpecification,
} from 'maplibre-gl'
// Tell MapLibre where its worker actually is, and do it before any Map exists.
//
// maplibre-gl 6 derives the worker URL at RUNTIME from `import.meta.url`:
// `new URL('./maplibre-gl-worker.mjs', import.meta.url)`. After bundling,
// `import.meta.url` is our own chunk — so it asked for
// `/assets/maplibre-gl-worker.mjs`, which Vite never emitted because a runtime
// `new URL` string is invisible to the bundler. That 404s, the Worker is
// constructed against a dead script, and it never answers.
//
// The failure mode is the reason this comment is long: nothing throws. The
// raster basemap is fetched on the main thread and drew fine, and the news
// pins are DOM Markers, so the map LOOKED alive — while every GeoJSON source
// (countries, cities, places, the route line, the focus ring and the "you are
// here" dot) sat in `_isUpdatingWorker` forever with its data queued and never
// rendered a single feature. `queryRenderedFeatures` returning 0 for every
// layer while the sources hold hundreds of features is the fingerprint.
//
// `?worker&url` makes Vite bundle the worker WITH its shared chunk and hand
// back a real hashed URL; `worker.format: 'es'` in vite.config.ts keeps it an
// ES module, which is what MapLibre tries to construct first.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'

setWorkerUrl(maplibreWorkerUrl)
// Must come after maplibre-gl.css: this view's own sheet has to override
// `.maplibregl-map { position: relative }`. See the header of map.css.
import './map/map.css'

import { api } from '../lib/api'
import {
  geo, humanDistance, type Freshness, type GeoConfig, type GeoPlace,
  type GeoRoute, type Geofence, type SavedPlace,
} from '../lib/geo'
import { current as currentAppearance, useAppearance, type Mode } from '../lib/appearance'
import { useWorkspace } from '../store/workspace'
import type { OmxObject } from '../lib/types'

import {
  LAYERS, GROUPS, defaultLayerState, loadMapState, saveMapState,
  riskFill, riskOpacity, type LayerState,
} from './map/layers'
import {
  countriesToGeoJSON, bordersToGeoJSON, citiesToGeoJSON, graticule,
  circleRing, haversineM, countryCentroids, EMPTY,
  type RawFeature, type RiskScore,
} from './map/geodata'
import {
  HighlightsPanel, severityVar, type Highlight,
} from './map/highlights'
import {
  ConditionsPanel, DataPanel, Empty, FencePanel, Fresh, MemoryPanel,
  PlacesPanel, RoutePanel, SearchPanel, type Point,
} from './map/panels'
import { PlacePanel } from './map/place'

type PanelId = 'highlights' | 'place' | 'layers' | 'search' | 'places' | 'route'
  | 'conditions' | 'memory' | 'fences' | 'data'

const PANELS: { id: PanelId; label: string }[] = [
  // First, and the default: the map is now the landing surface, and a landing
  // surface that opens on a layer-toggle list answers a question nobody has
  // asked yet.
  { id: 'highlights', label: 'Highlights' },
  // Second, and where a click lands: "what is happening at the point I just
  // picked" is the question the map is opened with, and it was the one thing
  // the map could not answer — the country card said the name and the risk and
  // then stopped.
  { id: 'place', label: 'Location' },
  { id: 'layers', label: 'Layers' },
  { id: 'search', label: 'Search' },
  { id: 'places', label: 'Places' },
  { id: 'route', label: 'Route' },
  { id: 'conditions', label: 'Conditions' },
  { id: 'memory', label: 'Memory' },
  { id: 'fences', label: 'Fences' },
  { id: 'data', label: 'Data' },
]

const HOME = { center: [10, 25] as [number, number], zoom: 1.5 }

/** Panels a map click does not belong to, so a click may replace them with the
 *  Location briefing. Every other panel treats the click as its own input. */
const PASSIVE_PANELS = new Set<PanelId>(['highlights', 'place', 'layers', 'data'])

interface CountryCard {
  iso: string
  name: string
  object: OmxObject | null
  risk: RiskScore | null
}

export function MapView() {
  const { mode, accent } = useAppearance()
  const ws = useWorkspace((s) => s.workspaceId)
  const select = useWorkspace((s) => s.select)
  const toggle = useWorkspace((s) => s.toggle)

  const mapEl = useRef<HTMLDivElement | null>(null)
  const map = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)
  // `ready` means the STYLE loaded; `painted` means a frame with tiles in it
  // has actually been drawn. They are seconds apart on a cold tile cache, and
  // that gap is the whole reason the map looked broken on first open.
  const [painted, setPainted] = useState(false)
  const [config, setConfig] = useState<GeoConfig | null>(null)

  const stored = useRef(loadMapState())
  const [layers, setLayers] = useState<LayerState>(() => ({
    // Merged over the defaults, never used raw — a layer added since the
    // user's last visit would otherwise arrive as `undefined`, which MapLibre
    // reads as hidden. New features shipping invisible to returning users is a
    // uniquely annoying bug.
    ...defaultLayerState(), ...(stored.current.layers || {}),
  }))
  const [panel, setPanel] = useState<PanelId>(
    (stored.current.panel as PanelId) || 'highlights')
  const [open, setOpen] = useState(stored.current.sidebar !== false)

  // -- shared state the panels read --------------------------------------
  const [focus, setFocus] = useState<Point | null>(null)
  // The country under the focus point, read off the polygon the map already
  // drew. The Location briefing sends it as a hint so the answer is still
  // correct when the reverse geocoder is unreachable — the basemap knows what
  // country a pixel is in whether or not the network does.
  const [focusIso, setFocusIso] = useState('')
  const [me, setMe] = useState<Point | null>(null)
  const [locating, setLocating] = useState(false)
  const [notice, setNotice] = useState('')

  const [places, setPlaces] = useState<GeoPlace[]>([])
  const [placesMeta, setPlacesMeta] = useState<{
    freshness?: Freshness; ageS?: number | null; provider?: string
    attempted?: string[]
  }>({})
  const [routes, setRoutes] = useState<GeoRoute[]>([])
  const [routeMeta, setRouteMeta] = useState<{
    freshness?: Freshness; provider?: string; explanations?: string[]
    origin?: Point; destination?: Point; mode?: string; error?: string
  }>({})
  const [activeRoute, setActiveRoute] = useState(0)
  const [saved, setSaved] = useState<SavedPlace[]>([])
  const [fences, setFences] = useState<Geofence[]>([])
  const [hovered, setHovered] = useState<string | null>(null)
  const [drawRadius, setDrawRadius] = useState(200)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const mark = (k: string, on: boolean) => setBusy((b) => ({ ...b, [k]: on }))

  // -- world data ---------------------------------------------------------
  const [worldReady, setWorldReady] = useState(false)
  const worldData = useRef<{
    countries: RawFeature[]; borders: RawFeature[]; cities: number[][]
    risk: Record<string, RiskScore>
  }>({ countries: [], borders: [], cities: [], risk: {} })
  const countryByIso = useRef<Record<string, OmxObject>>({})

  const [hoverCountry, setHoverCountry] = useState<CountryCard | null>(null)
  const [readout, setReadout] = useState({ ll: '—', country: '' })

  // -- measurement --------------------------------------------------------
  const [measurePts, setMeasurePts] = useState<[number, number][]>([])
  const measureTotal = useMemo(
    () => measurePts.reduce((sum, p, i) =>
      i === 0 ? 0 : sum + haversineM(measurePts[i - 1], p), 0),
    [measurePts])

  // -- radar --------------------------------------------------------------
  const [radarFrames, setRadarFrames] = useState<
    { time: number; kind: string; url: string }[]>([])
  const [radarIdx, setRadarIdx] = useState(0)

  // -- highlights ---------------------------------------------------------
  // MapView owns this fetch rather than the panel, because the same array
  // drives both the list and the map pins. Two fetches would let them disagree
  // about what is happening in the world, which is the one thing a map beside
  // a feed must never do.
  const [highlights, setHighlights] = useState<Highlight[]>([])
  const [hiTotal, setHiTotal] = useState(0)
  const [hiLoading, setHiLoading] = useState(true)
  const [hiError, setHiError] = useState('')
  const [hiDomain, setHiDomain] = useState('')
  const [hiStory, setHiStory] = useState<string | null>(null)
  const [hiTick, setHiTick] = useState(0)
  const pins = useRef<Map<string, Marker>>(new Map())

  // =======================================================================
  // Map lifecycle
  // =======================================================================
  useEffect(() => { geo.config().then(setConfig).catch(() => setConfig(null)) }, [])

  useEffect(() => {
    let dead = false
    Promise.all([
      fetch('/static/world.json').then((r) => r.json()).catch(() => ({ features: [] })),
      fetch('/static/states.json').then((r) => r.json()).catch(() => ({ features: [] })),
      fetch('/static/cities.json').then((r) => r.json()).catch(() => ({ c: [] })),
      fetch('/api/terra/heatmap').then((r) => r.json()).catch(() => ({ scores: {} })),
    ]).then(([w, s, c, h]) => {
      if (dead) return
      worldData.current = {
        countries: w.features || [], borders: s.features || [],
        cities: c.c || [], risk: h.scores || {},
      }
      setWorldReady(true)
    })
    return () => { dead = true }
  }, [])

  useEffect(() => {
    if (!ws) return
    // Guarded like the layer effects below: switching Space reissues this
    // without cancelling the previous one, and a slower reply would otherwise
    // repaint the map with the Space the user just left.
    let dead = false
    api.objects(ws, { limit: 800 })
      .then((r) => {
        if (dead) return
        const byIso: Record<string, OmxObject> = {}
        for (const o of r.objects) {
          const m = /^terra:country:([A-Za-z]{2})$/.exec(o.externalId || '')
          if (m) byIso[m[1].toUpperCase()] = o
        }
        countryByIso.current = byIso
        const geoObjects = r.objects.filter((o) => o.geo && o.lat != null)
        setData('objects', {
          type: 'FeatureCollection',
          features: geoObjects.map((o) => ({
            type: 'Feature' as const,
            properties: { name: o.name, id: o.id, glyph: o.glyph || '◆' },
            geometry: { type: 'Point' as const, coordinates: [o.lon!, o.lat!] },
          })),
        })
      })
      .catch(() => { if (!dead) countryByIso.current = {} })
    return () => { dead = true }
  }, [ws, ready])

  const setData = useCallback((id: string, data: unknown) => {
    const src = map.current?.getSource(id) as GeoJSONSource | undefined
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    src?.setData(data as any)
  }, [])

  // Built ONCE per mount. `mode` used to be a dependency, which tore the whole
  // map down and rebuilt it on every theme change — losing the camera (the
  // rebuild re-read `stored.current`, captured at mount, so switching theme
  // teleported you back to wherever you had been when the view opened), the
  // loaded world data, the route, the search results and the hover state. The
  // theme is now applied to the live instance instead; see the effect below.
  useEffect(() => {
    if (!mapEl.current || map.current || !config) return

    const instance = new MapLibreMap({
      container: mapEl.current,
      style: baseStyle(
        mode === 'light' ? config.config.tiles.light : config.config.tiles.dark,
        config.config.tiles.attribution),
      center: stored.current.center || HOME.center,
      zoom: stored.current.zoom ?? HOME.zoom,
      attributionControl: false,
      // Tile Cache-Control lapses while a view sits open; re-requesting them is
      // the one cost the backend's caching layer cannot see, so it is disabled
      // here for the same reason the TTLs exist there.
      refreshExpiredTiles: false,
      maxZoom: 19,
    })
    instance.addControl(new AttributionControl({ compact: true }))
    instance.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left')

    instance.on('load', () => {
      buildLayers(instance)
      setReady(true)
      // Resize AFTER the style has loaded, and again on the next frame.
      //
      // This is what actually fixes the blank map, and the ResizeObserver
      // below is not a substitute for it. The observer's first callback
      // arrives at `observe()` time — before this `load` — and MapLibre had
      // nothing to paint yet, so the map sat unpainted until something later
      // happened to resize it. Collapsing the rail fixed it, which is how the
      // cause was found: the map was never wrong about its size, it had simply
      // never been asked to draw a frame after its style arrived.
      //
      // The rAF second call covers the case where `load` itself fires inside
      // the same layout pass that sizes the container.
      instance.resize()
      requestAnimationFrame(() => {
        if (map.current === instance) instance.resize()
      })
    })
    // First frame with its tiles in place. NOT `idle`: this map animates —
    // pulsing event markers keep a render scheduled — so `idle` may never fire
    // and the note would sit over a fully drawn world forever. `render` fires
    // on every frame, so the listener removes itself the moment the tiles are
    // in.
    const onFirstPaint = () => {
      if (map.current !== instance || !instance.areTilesLoaded()) return
      instance.off('render', onFirstPaint)
      setPainted(true)
    }
    instance.on('render', onFirstPaint)
    map.current = instance

    // MapLibre measures its container ONCE, in the constructor, and never
    // again on its own. This map is built from an effect keyed on `config`,
    // which arrives from `/api/terra/geo/config` — so construction happens
    // during layout, before the rail, the right panel and the web fonts have
    // settled. The canvas came out sized for a container ~120px narrower than
    // the one it ends up in, and the World Map opened as a BLANK rectangle
    // that only painted once the user happened to drag it. It looked like the
    // map was broken; it was the wrong size and had never been told.
    //
    // A ResizeObserver rather than a window `resize` listener, because none of
    // the things that change this container's size are window resizes:
    // collapsing the rail, opening the inspector and the fonts settling all
    // resize the element while the window stands still.
    const ro = new ResizeObserver(() => {
      // Guarded: `resize()` on a removed map throws, and the observer can fire
      // once more between `instance.remove()` and the disconnect below.
      if (map.current === instance) instance.resize()
    })
    ro.observe(mapEl.current)

    return () => {
      ro.disconnect()
      instance.off('render', onFirstPaint)
      instance.remove()
      map.current = null
      setReady(false)
      setPainted(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])

  // Theme, applied live.
  //
  // Two halves, and both were missing. The basemap swaps its tile URL in place
  // — CARTO ships a dark and a light sheet, and `setTiles` changes which one
  // without touching anything else on the map — and every layer OMNIX draws on
  // top has its accent-derived colours reapplied. Before this, picking a new
  // accent repainted the entire application except the map, which sat there in
  // the previous colour until the view was navigated away from and back.
  //
  // Keyed on `mode` and `accent` together because they are one appearance:
  // the tokens `restyle` reads resolve differently in each of the ten
  // combinations, so a change to either has to re-run both halves.
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !config) return
    const src = m.getSource('basemap') as RasterTileSource | undefined
    if (src && typeof src.setTiles === 'function') {
      src.setTiles(expandTiles(
        mode === 'light' ? config.config.tiles.light : config.config.tiles.dark))
    }
    restyle(m, mode)
  }, [mode, accent, ready, config])

  // Persist camera and layer state. Debounced on moveend rather than written
  // per frame — `moveend` already fires once per gesture.
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    const persist = () => {
      const c = m.getCenter()
      saveMapState({ center: [c.lng, c.lat], zoom: m.getZoom(), layers,
                     sidebar: open, panel })
    }
    m.on('moveend', persist)
    persist()
    return () => { m.off('moveend', persist) }
  }, [ready, layers, open, panel])

  // -- world data into the map -------------------------------------------
  useEffect(() => {
    if (!ready || !worldReady) return
    const { countries, borders, cities, risk } = worldData.current
    setData('countries', countriesToGeoJSON(countries, risk))
    setData('borders', bordersToGeoJSON(borders))
    setData('cities', citiesToGeoJSON(cities))
    setData('graticule', graticule(30))
  }, [ready, worldReady, setData])

  // -- layer visibility ---------------------------------------------------
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    for (const def of LAYERS) {
      for (const id of def.mapLayers) {
        if (m.getLayer(id)) {
          m.setLayoutProperty(id, 'visibility', layers[def.id] ? 'visible' : 'none')
        }
      }
    }
  }, [layers, ready])

  // =======================================================================
  // Feature data -> sources
  // =======================================================================
  useEffect(() => {
    if (!ready) return
    setData('places', {
      type: 'FeatureCollection',
      features: places.map((p) => ({
        type: 'Feature' as const,
        properties: {
          id: p.externalId || `${p.lat},${p.lon}`,
          name: p.name, category: p.category,
          rating: p.rating ?? null,
          open: p.openNow === true ? 1 : p.openNow === false ? 0 : -1,
        },
        geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
      })),
    })
  }, [places, ready, setData])

  useEffect(() => {
    if (!ready) return
    setData('route', {
      type: 'FeatureCollection',
      features: routes.map((r, i) => ({
        type: 'Feature' as const,
        properties: { active: i === activeRoute, index: i },
        // GeoJSON is [lon, lat]; TERRA's wire format is [lat, lon]. Getting
        // this backwards puts an Indian route in Somalia.
        geometry: {
          type: 'LineString' as const,
          coordinates: r.geometry.map(([lat, lon]) => [lon, lat]),
        },
      })),
    })
  }, [routes, activeRoute, ready, setData])

  useEffect(() => {
    if (!ready) return
    const features = fences.map((f) => ({
      type: 'Feature' as const,
      properties: { id: f.id, label: f.label, active: f.active, draft: false },
      geometry: {
        type: 'Polygon' as const,
        coordinates: [f.shape === 'polygon' && f.polygon.length >= 3
          ? [...f.polygon.map(([lat, lon]) => [lon, lat]),
             [f.polygon[0][1], f.polygon[0][0]]]
          : circleRing(f.lat, f.lon, f.radiusM)],
      },
    }))
    // The draft ring: a live preview of the fence about to be created, drawn
    // from the focus point and the radius slider. Without it, sizing a
    // geofence is guessing at a number in a form and finding out afterwards.
    if (panel === 'fences' && focus) {
      features.push({
        type: 'Feature' as const,
        properties: { id: '__draft', label: 'New fence', active: true, draft: true },
        geometry: {
          type: 'Polygon' as const,
          coordinates: [circleRing(focus.lat, focus.lon, drawRadius)],
        },
      })
    }
    setData('fences', { type: 'FeatureCollection', features })
  }, [fences, focus, drawRadius, panel, ready, setData])

  useEffect(() => {
    if (!ready) return
    setData('saved', {
      type: 'FeatureCollection',
      features: saved.map((s) => ({
        type: 'Feature' as const,
        properties: { id: s.id, name: s.label, kind: s.kind },
        geometry: { type: 'Point' as const, coordinates: [s.lon, s.lat] },
      })),
    })
  }, [saved, ready, setData])

  useEffect(() => {
    if (!ready) return
    const pts = measurePts
    setData('measure', {
      type: 'FeatureCollection',
      features: [
        ...(pts.length > 1 ? [{
          type: 'Feature' as const, properties: { kind: 'line' },
          geometry: { type: 'LineString' as const, coordinates: pts },
        }] : []),
        ...pts.map((p, i) => ({
          type: 'Feature' as const,
          properties: {
            kind: 'point',
            label: i === 0 ? 'start'
              : humanDistance(pts.slice(0, i + 1).reduce((s, q, j) =>
                  j === 0 ? 0 : s + haversineM(pts[j - 1], q), 0)),
          },
          geometry: { type: 'Point' as const, coordinates: p },
        })),
      ],
    })
  }, [measurePts, ready, setData])

  // -- focus marker -------------------------------------------------------
  useEffect(() => {
    if (!ready) return
    setData('focus', focus ? {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', properties: { kind: 'focus' },
        geometry: { type: 'Point', coordinates: [focus.lon, focus.lat] },
      }],
    } : EMPTY)
  }, [focus, ready, setData])

  useEffect(() => {
    if (!ready) return
    setData('me', me ? {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', properties: { kind: 'me' },
        geometry: { type: 'Point', coordinates: [me.lon, me.lat] },
      }],
    } : EMPTY)
  }, [me, ready, setData])

  // -- hover link between the list and the map ---------------------------
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    for (const id of ['places-dot', 'saved-dot']) {
      if (!m.getLayer(id)) continue
      m.setPaintProperty(id, 'circle-radius', [
        'case', ['==', ['get', 'id'], hovered ?? '\u0000'], 10, 5.5,
      ])
      m.setPaintProperty(id, 'circle-stroke-width', [
        'case', ['==', ['get', 'id'], hovered ?? '\u0000'], 3, 1.5,
      ])
    }
    if (m.getLayer('fence-line')) {
      m.setPaintProperty('fence-line', 'line-width', [
        'case', ['==', ['get', 'id'], hovered ?? '\u0000'], 3, 1.5,
      ])
    }
  }, [hovered, ready])

  // =======================================================================
  // Overlays
  // =======================================================================
  useEffect(() => {
    if (!layers.radar || !ready) return
    let dead = false
    geo.radar().then((r) => {
      if (dead) return
      setRadarFrames(r.radar?.frames || [])
      setRadarIdx(Math.max(0, (r.radar?.pastCount ?? 1) - 1))
    }).catch(() => setRadarFrames([]))
    return () => { dead = true }
  }, [layers.radar, ready])

  useEffect(() => {
    if (!layers.radar || radarFrames.length < 2) return
    const t = setInterval(() =>
      setRadarIdx((i) => (i + 1) % radarFrames.length), 700)
    return () => clearInterval(t)
  }, [layers.radar, radarFrames])

  useEffect(() => {
    const m = map.current
    if (!m || !ready || !radarFrames.length) return
    const frame = radarFrames[Math.min(radarIdx, radarFrames.length - 1)]
    const src = m.getSource('radar') as RasterTileSource | undefined
    // setTiles swaps the template in place. Removing and re-adding the source
    // would tear down every layer above it and make the animation flicker.
    src?.setTiles([frame.url])
  }, [radarIdx, radarFrames, ready])

  useEffect(() => {
    if (!layers.air || !focus || !ready) return
    let dead = false
    geo.airGrid(focus.lat, focus.lon, 15000, 5).then((r) => {
      if (dead || !r.grid) return
      setData('air', {
        type: 'FeatureCollection',
        features: r.grid.points.map((p) => ({
          type: 'Feature' as const,
          properties: { index: p.index, band: p.band },
          geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
        })),
      })
    }).catch(() => {})
    return () => { dead = true }
  }, [layers.air, focus, ready, setData])

  // =======================================================================
  // Interaction
  // =======================================================================
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return

    const onMove = (e: MapMouseEvent) => {
      setReadout({
        ll: `${e.lngLat.lat.toFixed(3)}°, ${e.lngLat.lng.toFixed(3)}°`,
        country: '',
      })
      if (!layers.countries || !m.getLayer('countries-fill')) {
        setHoverCountry(null)
        return
      }
      const hit = m.queryRenderedFeatures(e.point,
                                          { layers: ['countries-fill'] })[0]
      if (!hit) { setHoverCountry(null); return }
      const iso = String(hit.properties?.iso || '')
      setHoverCountry({
        iso,
        name: String(hit.properties?.name || iso || 'Unknown'),
        object: (iso && countryByIso.current[iso]) || null,
        risk: (iso && worldData.current.risk[iso]) || null,
      })
      setReadout((r) => ({ ...r, country: String(hit.properties?.name || '') }))
    }

    const onClick = async (e: MapMouseEvent) => {
      // Measuring takes the click entirely — a click that both measured and
      // moved the focus point would make the tool unusable.
      if (layers.measure) {
        setMeasurePts((pts) => [...pts, [e.lngLat.lng, e.lngLat.lat]])
        return
      }
      setFocus({ lat: e.lngLat.lat, lon: e.lngLat.lng })

      const hit = (layers.countries && m.getLayer('countries-fill'))
        ? m.queryRenderedFeatures(e.point, { layers: ['countries-fill'] })[0]
        : undefined
      const iso = String(hit?.properties?.iso || '')
      setFocusIso(iso)

      // Show what is happening there. Only from a panel that does not itself
      // consume the click: in Route the click is an origin, in Fences it is a
      // centre, in Conditions it is the point being read — swapping the panel
      // out from under any of those would break the tool mid-use.
      setPanel((current) =>
        PASSIVE_PANELS.has(current) ? 'place' : current)

      if (!iso || !ws) return
      // Resolve the map click to the SAME workspace object the graph selects.
      // Unchanged from the canvas version — it is the behaviour that made the
      // map part of the workspace rather than a picture of one.
      try {
        const res = await api.objects(ws, { externalId: `terra:country:${iso}` })
        const obj = res.objects[0]
        if (obj) {
          if (e.originalEvent.ctrlKey || e.originalEvent.metaKey) toggle(obj.id, 'map')
          else select(obj.id, 'map')
        }
      } catch { /* not projected into this workspace */ }
    }

    m.on('mousemove', onMove)
    m.on('click', onClick)
    return () => { m.off('mousemove', onMove); m.off('click', onClick) }
  }, [ready, layers.countries, layers.measure, ws, select, toggle])

  // =======================================================================
  // Actions
  // =======================================================================
  const flyTo = useCallback((p: Point, zoom = 14) => {
    map.current?.flyTo({ center: [p.lon, p.lat], zoom, duration: 900 })
  }, [])

  /** Move the focus point from a panel result rather than a map click.
   *
   *  The ISO hint is cleared rather than kept: it was read off the polygon
   *  under the *previous* point, and a stale country hint on a new place is
   *  worse than none — the briefing would resolve the wrong country without
   *  ever looking wrong. `show` is set where picking a result IS the request
   *  to see the place, which is Search and nothing else. */
  const pickPoint = useCallback((p: Point, zoom: number, show = false) => {
    setFocus(p)
    setFocusIso('')
    flyTo(p, zoom)
    if (show) setPanel('place')
  }, [flyTo])

  const refreshMemory = useCallback(() => {
    if (!ws) return
    geo.savedPlaces(ws).then((r) => setSaved(r.places)).catch(() => {})
    geo.geofences(ws).then((r) => setFences(r.geofences)).catch(() => {})
  }, [ws])

  useEffect(() => { refreshMemory() }, [refreshMemory])

  // -- highlights: fetch --------------------------------------------------
  // Refetched on domain change and on an explicit refresh. Not polled: TERRA
  // ingests on its own schedule and a timer here would redraw the pins under
  // the user's cursor mid-read for no new information.
  useEffect(() => {
    let dead = false
    setHiLoading(true)
    fetch(`/api/terra/events?limit=40${hiDomain ? `&domain=${hiDomain}` : ''}`)
      .then((r) => {
        if (!r.ok) throw new Error(`the world feed returned HTTP ${r.status}`)
        return r.json()
      })
      .then((d: { events?: Highlight[]; total?: number }) => {
        if (dead) return
        setHighlights(d.events || [])
        setHiTotal(d.total ?? (d.events || []).length)
        setHiError('')
      })
      .catch((e: unknown) => {
        if (dead) return
        setHighlights([])
        setHiError(e instanceof Error ? e.message : 'the world feed is unreachable')
      })
      .finally(() => { if (!dead) setHiLoading(false) })
    return () => { dead = true }
  }, [hiDomain, hiTick])

  /** Every domain the feed mentions, so the filter row offers only filters
   *  that would return something. */
  const hiDomains = useMemo(() => {
    const set = new Set<string>()
    for (const h of highlights) for (const d of h.domains || []) set.add(d)
    return [...set].sort()
  }, [highlights])

  const spaceName = useWorkspace(
    (s) => s.workspaces.find((w) => w.id === s.workspaceId)?.name || 'this Space')

  /** Country centroids, computed once from the gazetteer already loaded for
   *  the choropleth. TERRA's clusters carry ISO codes and no coordinates, so
   *  this is what lets a story become a pin. */
  const centroids = useMemo(
    () => (worldReady ? countryCentroids(worldData.current.countries) : {}),
    [worldReady])

  /** A story's point on the map: the centroid of the first country it names. */
  const storyPoint = useCallback((h: Highlight): Point | null => {
    for (const iso of h.countries || []) {
      const c = centroids[iso.toUpperCase()]
      if (c) return { lat: c[1], lon: c[0], label: h.title }
    }
    return null
  }, [centroids])

  // -- highlights: pins ---------------------------------------------------
  // DOM markers rather than a symbol layer: each pin carries a count badge and
  // its own hover target, and a symbol layer cannot do either without a sprite
  // sheet built at load. There are at most a few dozen.
  //
  // Markers are diffed against the previous set by story id rather than
  // cleared and rebuilt. Removing and re-adding every marker on each render
  // drops the one under the cursor mid-hover, which reads as the pin flinching
  // away from the pointer.
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return

    const wanted = new Map<string, { h: Highlight; p: Point }>()
    if (layers.highlights) {
      for (const h of highlights) {
        const p = storyPoint(h)
        if (p) wanted.set(h.id, { h, p })
      }
    }

    for (const [id, marker] of pins.current) {
      if (!wanted.has(id)) { marker.remove(); pins.current.delete(id) }
    }

    for (const [id, { h, p }] of wanted) {
      let marker = pins.current.get(id)
      if (!marker) {
        const el = document.createElement('button')
        el.className = 'omx-gx-pin'
        el.type = 'button'
        el.textContent = String(h.size ?? '')
        el.title = h.title
        el.style.setProperty('--omx-sev', severityVar(h.severity))
        el.addEventListener('mouseenter', () => setHovered(id))
        el.addEventListener('mouseleave', () => setHovered(null))
        el.addEventListener('click', (ev) => {
          ev.stopPropagation()
          setHiStory(id)
          setPanel('highlights')
          setOpen(true)
        })
        marker = new Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(m)
        pins.current.set(id, marker)
      } else {
        marker.setLngLat([p.lon, p.lat])
      }
      marker.getElement().dataset.hover = String(hovered === id)
    }
  }, [ready, highlights, layers.highlights, storyPoint, hovered])

  // Markers live on the map instance, not in React's tree, so they outlive an
  // unmount unless this runs. Keyed to nothing: it is a teardown only.
  useEffect(() => () => {
    for (const marker of pins.current.values()) marker.remove()
    pins.current.clear()
  }, [])

  useEffect(() => {
    if (!ws) return
    geo.location(ws).then((r) => {
      if (r.known && r.lat !== undefined && r.lon !== undefined) {
        const p = { lat: r.lat, lon: r.lon, label: r.label || 'Last known' }
        setMe(p)
        setFocus((f) => f ?? p)
      }
    }).catch(() => {})
  }, [ws])

  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setNotice('This browser cannot report a location.'); return
    }
    setLocating(true); setNotice('')
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const p = { lat: pos.coords.latitude, lon: pos.coords.longitude,
                  label: 'You are here' }
      setMe(p); setFocus(p); setFocusIso(''); flyTo(p, 15); setLocating(false)
      if (!ws) return
      try {
        const r = await geo.observe(ws, p.lat, p.lon, pos.coords.accuracy)
        if (r.geofenceEvents?.length) {
          setNotice(r.geofenceEvents.map((e) =>
            `${e.transition === 'enter' ? 'Arrived at' : 'Left'} ${e.label}`).join(' · '))
          refreshMemory()
        } else if (!r.recorded && r.reason) {
          setNotice(`Position not stored — ${r.reason}.`)
        }
      } catch { /* best effort */ }
    }, (err) => {
      setLocating(false)
      setNotice(err.code === err.PERMISSION_DENIED
        ? 'Location permission denied. Click the map to pick a point instead.'
        : 'Could not get a fix. Click the map to pick a point.')
    }, { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 })
  }, [ws, flyTo, refreshMemory])

  const searchPlaces = useCallback(async (opts: {
    category?: string; q?: string; radius?: number; openNow?: boolean
  }) => {
    if (!focus) { setNotice('Click the map to set a point first.'); return }
    mark('places', true)
    try {
      const r = await geo.places(focus.lat, focus.lon, {
        category: opts.category, q: opts.q, radius: opts.radius ?? 2000,
        limit: 30, open_now: opts.openNow || undefined,
      })
      setPlaces(r.places)
      setPlacesMeta({ freshness: r.freshness, ageS: r.ageS,
                      provider: r.provider, attempted: r.attempted })
      setLayers((l) => ({ ...l, places: true }))
      setPanel('places')
      setNotice(r.places.length ? '' : 'Nothing found in that radius.')
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Search failed.')
    } finally { mark('places', false) }
  }, [focus])

  const fitRoute = useCallback((r: GeoRoute) => {
    if (!map.current || r.geometry.length < 2) return
    const b = new LngLatBounds()
    r.geometry.forEach(([lat, lon]) => b.extend([lon, lat]))
    map.current.fitBounds(b, { padding: { top: 60, bottom: 60, left: 60,
                                          right: open ? 400 : 60 }, duration: 900 })
  }, [open])

  const runRoute = useCallback(async (
    origin: Point | string, destination: Point | string, travel: string,
    prefer = 'score',
  ) => {
    if (!ws) return
    mark('route', true)
    try {
      const r = await geo.route({ workspace: ws, origin, destination,
                                  mode: travel, alternatives: 3, prefer })
      setRoutes(r.routes); setActiveRoute(0)
      setRouteMeta({ freshness: r.freshness, provider: r.provider,
                     explanations: r.explanations, origin: r.origin,
                     destination: r.destination, mode: r.mode, error: r.error })
      setLayers((l) => ({ ...l, route: true }))
      setPanel('route')
      if (r.routes.length) fitRoute(r.routes[0])
      setNotice(r.error && r.freshness !== 'live' ? r.error : '')
    } catch (e) {
      setNotice(e instanceof Error ? e.message : 'Routing failed.')
    } finally { mark('route', false) }
  }, [ws, fitRoute])

  const chooseRoute = useCallback(async (i: number) => {
    if (!ws || !routeMeta.origin || !routeMeta.destination) return
    try {
      const r = await geo.chooseRoute({
        workspace: ws, origin: routeMeta.origin,
        destination: routeMeta.destination, routes, chosenIndex: i,
        mode: routeMeta.mode, originLabel: routeMeta.origin.label,
        destinationLabel: routeMeta.destination.label,
      })
      const learned = Object.keys(r.learned || {})
      setNotice(learned.length
        ? `Noted — TERRA adjusted ${learned.join(', ')} in your route preferences.`
        : 'Noted.')
    } catch { /* learning is best effort */ }
  }, [ws, routeMeta, routes])

  const onStepHover = useCallback((coord: [number, number] | null) => {
    if (!ready) return
    setData('step', coord ? {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {},
                   geometry: { type: 'Point', coordinates: coord } }],
    } : EMPTY)
  }, [ready, setData])

  const focusLabel = focus
    ? (focus.label || `${focus.lat.toFixed(4)}, ${focus.lon.toFixed(4)}`)
    : 'Nothing selected'

  // =======================================================================
  return (
    <div className="omx-gx" data-open={open}>
      <div className="omx-gx-mapwrap">
        <div ref={mapEl} className="omx-gx-map" />
        {/* Held until the map has actually DRAWN, not merely until its config
            arrived. Config comes back from a local endpoint in milliseconds;
            the world basemap is ~120 tiles from a CDN and takes several
            seconds more. Tying the note to `config` alone meant it vanished
            almost immediately and left a black rectangle sitting where the
            world should be — which reads as a broken view, not a loading one.
            `idle` is MapLibre's "everything currently visible is drawn". */}
        {!painted && (
          <div className="omx-gx-mapnote">
            {config ? 'Drawing the world…' : 'Loading TERRA…'}
          </div>
        )}

        <div className="omx-gx-overlay">
          <button className="omx-btn" onClick={locate} disabled={locating}>
            {locating ? 'Locating…' : 'My location'}
          </button>
          {focus && (
            <button className="omx-btn" onClick={() => flyTo(focus, 14)}>Centre</button>
          )}
          <button className="omx-btn" onClick={() =>
            map.current?.flyTo({ ...HOME, duration: 900 })}>World</button>
          {layers.measure && measurePts.length > 0 && (
            <button className="omx-btn" onClick={() => setMeasurePts([])}>
              Clear ({humanDistance(measureTotal)})
            </button>
          )}
        </div>

        {notice && (
          <div className="omx-gx-notice" role="status">
            {notice}
            <button className="omx-gx-x" onClick={() => setNotice('')}
                    aria-label="Dismiss">×</button>
          </div>
        )}

        {/* The country card, preserved from the canvas map. It renders what
            exists and says plainly what does not — a country always has a name
            and an ISO code, usually has an object, and only sometimes has a
            risk score. */}
        {hoverCountry && (
          <div className="omx-gx-country">
            <div className="mc-head">
              <span className="mc-dot" style={{
                background: hoverCountry.risk?.color ?? 'var(--omx-text-faint)' }} />
              <strong>{hoverCountry.name}</strong>
              <span className="omx-label mc-iso">{hoverCountry.iso}</span>
            </div>
            {hoverCountry.risk ? (
              <div className="omx-gx-sub">
                risk {Math.round(hoverCountry.risk.score)} · {hoverCountry.risk.band}
                {hoverCountry.risk.top_dimension
                  ? ` · driven by ${hoverCountry.risk.top_dimension}` : ''}
              </div>
            ) : (
              <div className="omx-gx-sub dim">Not assessed by ORACLE</div>
            )}
            {hoverCountry.object
              ? <div className="omx-gx-sub dim">Click to select in this Space</div>
              : <div className="omx-gx-sub dim">No object in this Space</div>}
          </div>
        )}

        <div className="omx-gx-strip">
          <span className="omx-gx-strip-name">{focusLabel}</span>
          {readout.country && (
            <span className="omx-gx-strip-item">{readout.country}</span>
          )}
          <span className="omx-gx-strip-item">{readout.ll}</span>
          {layers.measure && (
            <span className="omx-gx-strip-item">
              measure: {measurePts.length < 2
                ? 'click two points' : humanDistance(measureTotal)}
            </span>
          )}
          {layers.radar && radarFrames.length > 0 && (
            <span className="omx-gx-strip-item">
              radar {radarIdx + 1}/{radarFrames.length}
              {radarFrames[radarIdx]?.kind === 'forecast' ? ' · nowcast' : ''}
            </span>
          )}
        </div>

        {/* The sidebar lives INSIDE the map wrapper and floats over it, so the
            map keeps the full width and does not re-render every tile when the
            panel opens or closes. */}
        <aside className="omx-gx-side" data-open={open}>
          <button className="omx-gx-handle" onClick={() => setOpen((o) => !o)}
                  title={open ? 'Collapse panel' : 'Expand panel'}
                  aria-expanded={open}>
            {open ? '›' : '‹'}
          </button>
          {open && (
            <>
              <nav className="omx-gx-tabs">
                {PANELS.map((p) => (
                  <button key={p.id} className="omx-gx-tab"
                          data-on={panel === p.id}
                          onClick={() => setPanel(p.id)}>{p.label}</button>
                ))}
              </nav>
              <div className="omx-gx-panel">
                {panel === 'highlights' && (
                  <HighlightsPanel
                    events={highlights} total={hiTotal} loading={hiLoading}
                    error={hiError} domain={hiDomain} domains={hiDomains}
                    spaceName={spaceName} hovered={hovered} expanded={hiStory}
                    refreshing={hiLoading}
                    onDomain={setHiDomain} onHover={setHovered}
                    onExpand={setHiStory}
                    onRefresh={() => setHiTick((t) => t + 1)}
                    onPick={(h) => {
                      const p = storyPoint(h)
                      // Zoom 4 is the country-in-context zoom: the story's
                      // country fills the pane with its neighbours still
                      // visible, which is the frame a geopolitical story is
                      // actually read in.
                      if (p) flyTo(p, 4)
                      // A story about a country OMNIX already tracks selects
                      // that object, so the rest of the workspace follows the
                      // map. A story about one it does not simply moves the
                      // camera — inventing an object here would put an
                      // unevidenced node in the graph.
                      const iso = (h.countries || [])[0]
                      const obj = iso ? countryByIso.current[iso.toUpperCase()] : null
                      if (obj) select(obj.id)
                      if (!p) {
                        setNotice(`"${h.title}" carries no country TERRA can place.`)
                      }
                    }} />
                )}
                {panel === 'place' && (
                  <PlacePanel focus={focus} isoHint={focusIso} workspaceId={ws}
                              onFly={(p, z) => flyTo(p, z ?? 8)}
                              onCountry={(iso) => {
                                const obj = countryByIso.current[iso.toUpperCase()]
                                if (obj) select(obj.id, 'map')
                                else setNotice(
                                  `${iso} is not projected into this Space yet.`)
                              }} />
                )}
                {panel === 'layers' && (
                  <LayersPanel layers={layers} setLayers={setLayers}
                               focus={focus} ws={ws}
                               hasPlaces={places.length > 0}
                               hasRoute={routes.length > 0} />
                )}
                {panel === 'search' && (
                  <SearchPanel workspaceId={ws} focus={focus}
                               onPick={(p) => pickPoint(p, 15, true)}
                               onSearch={searchPlaces} busy={!!busy.places} />
                )}
                {panel === 'places' && (
                  <PlacesPanel places={places} meta={placesMeta}
                               busy={!!busy.places} hovered={hovered}
                               onHover={setHovered}
                               onPick={(p) => pickPoint(p, 17)}
                               onRoute={(p) => focus && runRoute(focus, p, 'driving')}
                               onSave={async (p) => {
                                 if (!ws) return
                                 await geo.savePlace({ workspace: ws,
                                   label: p.label, lat: p.lat, lon: p.lon })
                                 refreshMemory()
                                 setNotice(`Saved "${p.label}".`)
                               }} />
                )}
                {panel === 'route' && (
                  <RoutePanel routes={routes} meta={routeMeta} active={activeRoute}
                              busy={!!busy.route} saved={saved} focus={focus} me={me}
                              onSelect={(i) => { setActiveRoute(i); fitRoute(routes[i]) }}
                              onChoose={chooseRoute} onRun={runRoute}
                              onStepHover={onStepHover} />
                )}
                {panel === 'conditions' && <ConditionsPanel focus={focus} />}
                {panel === 'memory' && (
                  <MemoryPanel workspaceId={ws} saved={saved} focus={focus}
                               onRefresh={refreshMemory}
                               onPick={(p) => pickPoint(p, 16)}
                               onNotice={setNotice} hovered={hovered}
                               onHover={setHovered} />
                )}
                {panel === 'fences' && (
                  <FencePanel workspaceId={ws} fences={fences} focus={focus}
                              drawRadius={drawRadius} setDrawRadius={setDrawRadius}
                              onRefresh={refreshMemory}
                              onPick={(p) => pickPoint(p, 15)}
                              onNotice={setNotice} hovered={hovered}
                              onHover={setHovered} />
                )}
                {panel === 'data' && (
                  <DataPanel config={config}
                             onReload={() => geo.config().then(setConfig).catch(() => {})} />
                )}
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Layers panel — generated from the registry
// ---------------------------------------------------------------------------
function LayersPanel({ layers, setLayers, focus, ws, hasPlaces, hasRoute }: {
  layers: LayerState
  setLayers: React.Dispatch<React.SetStateAction<LayerState>>
  focus: Point | null; ws: string; hasPlaces: boolean; hasRoute: boolean
}) {
  const unmet = (need?: string) => {
    if (need === 'focus' && !focus) return 'needs a point — click the map'
    if (need === 'workspace' && !ws) return 'needs a Space'
    if (need === 'search' && !hasPlaces) return 'nothing searched yet'
    if (need === 'route' && !hasRoute) return 'no route planned yet'
    return ''
  }
  return (
    <>
      {GROUPS.map((group) => (
        <section key={group} className="omx-gx-sec">
          <h3 className="omx-gx-h">{group}</h3>
          <ul className="omx-gx-layers">
            {LAYERS.filter((l) => l.group === group).map((l) => {
              const why = unmet(l.needs)
              return (
                <li key={l.id}>
                  {/* The toggle stays enabled when its data is missing. A
                      disabled control with no explanation is the worst answer
                      to "why is nothing showing" — so it toggles, and says
                      what it is waiting for. */}
                  <label className="omx-gx-toggle" data-on={layers[l.id]}>
                    <input type="checkbox" checked={layers[l.id]}
                           onChange={() => setLayers((s) =>
                             ({ ...s, [l.id]: !s[l.id] }))} />
                    <span className="omx-gx-switch" aria-hidden />
                    <span className="omx-gx-toggle-text">
                      <strong>{l.label}</strong>
                      <span className="omx-gx-sub dim">
                        {why || l.hint}
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Style and layer construction
// ---------------------------------------------------------------------------
/** A minimal style over a raster tile URL.
 *
 *  Hand-written because every hosted vector style worth using needs a key, and
 *  the map has to render on a fresh clone with none. `{s}` is expanded into
 *  MapLibre's `tiles` array — MapLibre has no subdomain placeholder of its own
 *  and leaving it in 404s every tile. */
/** One tile template into the concrete URL list MapLibre wants. */
function expandTiles(template: string): string[] {
  const hosts = template.includes('{s}')
    ? ['a', 'b', 'c', 'd'].map((s) => template.replace('{s}', s))
    : [template]
  return hosts.map((t) =>
    t.replace('{r}', window.devicePixelRatio > 1.5 ? '@2x' : ''))
}

function baseStyle(template: string, attribution: string): StyleSpecification {
  return {
    version: 8,
    sources: {
      basemap: {
        type: 'raster',
        tiles: expandTiles(template),
        tileSize: 256, attribution, maxzoom: 19,
      },
    },
    layers: [{
      id: 'basemap', type: 'raster', source: 'basemap',
      paint: basemapPaint('dark'),
    }],
  }
}

/** Raster paint for the basemap, per mode.
 *
 *  CARTO's `dark_all` sheet is near-black by design — it is built to sit under
 *  a dense data overlay and get out of the way. Under OMNIX's overlay it got
 *  out of the way so completely that the map read as an empty canvas: coasts,
 *  country shapes and place names were all technically drawn and all of them
 *  below the threshold where a person notices them.
 *
 *  So the sheet is lifted rather than replaced. `raster-brightness-min` raises
 *  the floor off true black, `raster-contrast` puts the separation back that
 *  raising the floor costs, and a small `raster-saturation` cut keeps the
 *  basemap achromatic so it never competes with the accent-coloured layers
 *  above it. Light mode needs the opposite correction: `light_all` is close to
 *  white, and the ceiling comes DOWN so the same overlays keep their contrast.
 *
 *  These are the properties raster paint can honestly do. Hue-rotating toward
 *  the accent was tried and is a no-op: both sheets are effectively greyscale,
 *  and rotating the hue of a pixel with no chroma changes nothing. The accent
 *  reaches the map through the vector layers drawn on top, which `restyle`
 *  repaints — that is where the theme is actually visible. */
function basemapPaint(mode: Mode): Record<string, number> {
  return mode === 'light'
    ? {
      'raster-brightness-min': 0.05,
      'raster-brightness-max': 0.93,
      'raster-contrast': 0.06,
      'raster-saturation': -0.15,
      'raster-opacity': 1,
    }
    : {
      // 0.14 rather than 0: the floor is what turns "a black rectangle" into
      // "an ocean with continents in it" at world zoom.
      'raster-brightness-min': 0.14,
      'raster-brightness-max': 0.92,
      'raster-contrast': 0.14,
      'raster-saturation': -0.2,
      'raster-opacity': 1,
    }
}

/** Reapply every paint property derived from a theme token.
 *
 *  The single source of truth for "what colour is this layer": `buildLayers`
 *  calls it once the layers exist, and the appearance effect calls it on every
 *  theme or accent change. Keeping the two paths on one function is the point
 *  — a layer coloured only inside `buildLayers` is a layer that silently stops
 *  following the theme, which is exactly what had happened to all of them.
 *
 *  Every write is guarded by `getLayer`. `restyle` runs from an effect keyed on
 *  appearance, and a theme change during the frame where the style is being
 *  reloaded would otherwise throw on a layer that does not exist yet. */
function restyle(m: MapLibreMap, mode: Mode): void {
  const accent = token('--omx-accent', '#d3ad55')
  const text = token('--omx-text', '#e8e4dc')
  const faint = token('--omx-text-faint', '#7b7b7b')
  const pos = token('--omx-pos', '#4ade80')
  const ground = token('--omx-ground', '#060606')

  const set = (layer: string, prop: string, value: unknown) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (m.getLayer(layer)) (m as any).setPaintProperty(layer, prop, value)
  }

  for (const [prop, value] of Object.entries(basemapPaint(mode))) {
    set('basemap', prop, value)
  }

  set('graticule-line', 'line-color', faint)
  set('countries-fill', 'fill-color', ground)
  set('countries-line', 'line-color', accent)
  set('borders-line', 'line-color', faint)

  set('cities-dot', 'circle-color', text)
  set('cities-label', 'text-color', text)
  set('cities-label', 'text-halo-color', ground)

  set('objects-dot', 'circle-color', accent)
  set('objects-dot', 'circle-stroke-color', ground)
  set('objects-label', 'text-color', accent)
  set('objects-label', 'text-halo-color', ground)

  set('route-line', 'line-color', ['case', ['get', 'active'], accent, faint])
  set('route-step', 'circle-color', accent)
  set('route-step', 'circle-stroke-color', accent)

  set('fence-fill', 'fill-color', ['case', ['get', 'draft'], pos, accent])
  set('fence-line', 'line-color', ['case', ['get', 'draft'], pos, accent])
  set('fence-label', 'text-color', accent)
  set('fence-label', 'text-halo-color', ground)

  set('air-point', 'text-color', text)
  set('air-point', 'text-halo-color', ground)

  set('saved-dot', 'circle-color', pos)
  set('saved-dot', 'circle-stroke-color', ground)
  set('saved-label', 'text-color', pos)
  set('saved-label', 'text-halo-color', ground)

  set('places-cluster', 'circle-color', accent)
  set('places-cluster', 'circle-stroke-color', accent)
  set('places-cluster-count', 'text-color', text)
  set('places-dot', 'circle-color', accent)
  set('places-dot', 'circle-stroke-color', ground)
  set('places-label', 'text-color', text)
  set('places-label', 'text-halo-color', ground)

  set('measure-line', 'line-color', accent)
  set('measure-dot', 'circle-color', accent)
  set('measure-label', 'text-color', text)
  set('measure-label', 'text-halo-color', ground)

  set('focus-ring', 'circle-stroke-color', text)
  // `me-dot` is deliberately absent. The blue "you are here" dot is a mapping
  // convention older than this product, and recolouring it per accent would
  // make the user's own position the one thing on screen they have to
  // re-learn every time they change theme.
}

/** The live mode, read from the DOM rather than passed down.
 *
 *  `buildLayers` runs from MapLibre's `load` event, not from render, so it has
 *  no props and no hook context. `<html data-theme>` is the state anyway — see
 *  lib/appearance — so reading it there is not a shortcut around React, it is
 *  reading the source. */
const currentMode = (): Mode => currentAppearance().mode

const token = (name: string, fallback: string): string => {
  try {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim()
    // getComputedStyle returns a custom property's TOKEN TEXT, so a value
    // defined with color-mix() arrives unparsed and MapLibre silently drops
    // the layer. Only literal colours are trusted.
    return /^#|^rgb|^hsl/.test(v) ? v : fallback
  } catch { return fallback }
}

function buildLayers(m: MapLibreMap): void {
  const accent = token('--omx-accent', '#d3ad55')
  const text = token('--omx-text', '#e8e4dc')
  const faint = token('--omx-text-faint', '#7b7b7b')
  const pos = token('--omx-pos', '#4ade80')
  const ground = token('--omx-ground', '#060606')

  const src = (id: string, extra: object = {}) =>
    m.addSource(id, { type: 'geojson', data: EMPTY, ...extra } as never)

  src('graticule'); src('countries'); src('borders'); src('cities')
  src('objects'); src('route'); src('fences'); src('saved'); src('air')
  src('measure'); src('focus'); src('me'); src('step')
  // Clustering is on the SOURCE, not the layer. A city-centre café search is
  // 30 overlapping pins otherwise, and the map becomes unreadable at exactly
  // the zoom where the results matter.
  src('places', { cluster: true, clusterRadius: 44, clusterMaxZoom: 15 })

  m.addSource('radar', {
    type: 'raster',
    tiles: ['https://tilecache.rainviewer.com/v2/radar/0/256/{z}/{x}/{y}/4/1_1.png'],
    tileSize: 256, maxzoom: 12,
    attribution: '<a href="https://www.rainviewer.com/">RainViewer</a>',
  })

  // -- base -------------------------------------------------------------
  m.addLayer({
    id: 'graticule-line', type: 'line', source: 'graticule',
    layout: { visibility: 'none' },
    paint: { 'line-color': faint, 'line-width': 0.5, 'line-opacity': 0.35 },
  })

  // -- countries and risk ----------------------------------------------
  m.addLayer({
    id: 'countries-fill', type: 'fill', source: 'countries',
    paint: {
      // Near-invisible but not zero: a fully transparent fill is not
      // hit-testable in some drivers, and this layer IS the click target for
      // country selection.
      'fill-color': ground,
      'fill-opacity': ['case', ['boolean', ['feature-state', 'hover'], false],
                       0.16, 0.02],
    },
  })
  m.addLayer({
    id: 'countries-risk', type: 'fill', source: 'countries',
    filter: ['boolean', ['get', 'hasRisk'], false],
    paint: { 'fill-color': riskFill, 'fill-opacity': riskOpacity },
  })
  // The country outline is the accent's main presence on the map, and it was
  // drawn at 0.6px / 0.4 opacity — below the threshold where it registers at
  // world zoom, which is where this view opens. Widening and lifting it is
  // what makes changing the accent visibly change the MAP rather than only the
  // chrome around it. Interpolated by zoom so it stays a hairline once you are
  // in close and the shapes are large enough to read on their own.
  m.addLayer({
    id: 'countries-line', type: 'line', source: 'countries',
    paint: {
      'line-color': accent,
      'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.9, 4, 1.3, 9, 0.8],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 1, 0.62, 6, 0.4],
    },
  })
  m.addLayer({
    id: 'borders-line', type: 'line', source: 'borders',
    layout: { visibility: 'none' },
    paint: { 'line-color': faint, 'line-width': 0.4, 'line-opacity': 0.4 },
  })

  // -- cities ------------------------------------------------------------
  m.addLayer({
    id: 'cities-dot', type: 'circle', source: 'cities',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 2, 1.4, 8, 3],
      'circle-color': text, 'circle-opacity': 0.5,
    },
  })
  m.addLayer({
    id: 'cities-label', type: 'symbol', source: 'cities',
    // Population-gated by zoom: 1251 labels at world zoom is a grey smear, and
    // the old canvas solved it with a hand-written per-zoom limit. This is the
    // same idea as a declarative filter, and unlike the original it keeps
    // labels stable while panning.
    filter: ['any',
      ['>', ['get', 'population'], 8_000_000],
      ['all', ['>', ['zoom'], 4], ['>', ['get', 'population'], 2_000_000]],
      ['all', ['>', ['zoom'], 6], ['>', ['get', 'population'], 500_000]],
      ['>', ['zoom'], 8],
    ],
    layout: {
      'text-field': ['get', 'name'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 3, 9, 10, 12],
      'text-offset': [0, 0.9], 'text-anchor': 'top',
      'symbol-sort-key': ['get', 'rank'],
      'text-allow-overlap': false,
    },
    paint: {
      'text-color': text, 'text-opacity': 0.75,
      'text-halo-color': ground, 'text-halo-width': 1.2,
    },
  })

  // -- workspace objects -------------------------------------------------
  m.addLayer({
    id: 'objects-dot', type: 'circle', source: 'objects',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 5, 'circle-color': accent,
      'circle-stroke-color': ground, 'circle-stroke-width': 1.5,
    },
  })
  m.addLayer({
    id: 'objects-label', type: 'symbol', source: 'objects',
    layout: {
      visibility: 'none', 'text-field': ['get', 'name'], 'text-size': 11,
      'text-offset': [0, 1.1], 'text-anchor': 'top',
    },
    paint: { 'text-color': accent, 'text-halo-color': ground,
             'text-halo-width': 1.2 },
  })

  // -- routes ------------------------------------------------------------
  // Casing under the line: a 1px stroke over a dark basemap disappears where
  // it crosses a road of similar value.
  m.addLayer({
    id: 'route-casing', type: 'line', source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#000', 'line-opacity': 0.5,
      'line-width': ['case', ['get', 'active'], 10, 6],
    },
  })
  m.addLayer({
    id: 'route-line', type: 'line', source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['case', ['get', 'active'], accent, faint],
      'line-width': ['case', ['get', 'active'], 5, 3],
      'line-opacity': ['case', ['get', 'active'], 1, 0.5],
    },
  })
  m.addLayer({
    id: 'route-step', type: 'circle', source: 'step',
    paint: {
      'circle-radius': 8, 'circle-color': accent, 'circle-opacity': 0.35,
      'circle-stroke-color': accent, 'circle-stroke-width': 2,
    },
  })

  // -- geofences ---------------------------------------------------------
  m.addLayer({
    id: 'fence-fill', type: 'fill', source: 'fences',
    paint: {
      'fill-color': ['case', ['get', 'draft'], pos, accent],
      'fill-opacity': ['case', ['get', 'draft'], 0.10,
                       ['get', 'active'], 0.13, 0.04],
    },
  })
  m.addLayer({
    id: 'fence-line', type: 'line', source: 'fences',
    paint: {
      'line-color': ['case', ['get', 'draft'], pos, accent],
      'line-width': 1.5, 'line-dasharray': [2, 2],
      'line-opacity': ['case', ['get', 'active'], 0.9, 0.35],
    },
  })
  m.addLayer({
    id: 'fence-label', type: 'symbol', source: 'fences',
    layout: { 'text-field': ['get', 'label'], 'text-size': 10,
              'symbol-placement': 'point' },
    paint: { 'text-color': accent, 'text-halo-color': ground,
             'text-halo-width': 1.2, 'text-opacity': 0.85 },
  })

  // -- air quality -------------------------------------------------------
  m.addLayer({
    id: 'air-heat', type: 'heatmap', source: 'air',
    layout: { visibility: 'none' },
    paint: {
      // Weighted by AQI on the US scale, where 150 is already "unhealthy" —
      // so the ramp saturates there rather than at the 500 theoretical max,
      // which would render every ordinary city as uniformly clean.
      'heatmap-weight': ['interpolate', ['linear'], ['get', 'index'],
                         0, 0.1, 150, 1],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 8, 40, 14, 110],
      'heatmap-opacity': 0.55,
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
        0, 'rgba(0,0,0,0)', 0.2, 'rgba(74,222,128,0.5)',
        0.45, 'rgba(250,204,21,0.6)', 0.7, 'rgba(249,115,22,0.7)',
        1, 'rgba(239,68,68,0.8)'],
    },
  })
  m.addLayer({
    id: 'air-point', type: 'symbol', source: 'air',
    layout: {
      visibility: 'none',
      'text-field': ['to-string', ['round', ['get', 'index']]],
      'text-size': 10, 'text-allow-overlap': false,
    },
    paint: { 'text-color': text, 'text-halo-color': ground,
             'text-halo-width': 1.4, 'text-opacity': 0.9 },
  })

  // -- radar -------------------------------------------------------------
  m.addLayer({
    id: 'radar-raster', type: 'raster', source: 'radar',
    layout: { visibility: 'none' },
    paint: { 'raster-opacity': 0.62 },
  })

  // -- saved places ------------------------------------------------------
  m.addLayer({
    id: 'saved-dot', type: 'circle', source: 'saved',
    paint: {
      'circle-radius': 5.5, 'circle-color': pos,
      'circle-stroke-color': ground, 'circle-stroke-width': 1.5,
    },
  })
  m.addLayer({
    id: 'saved-label', type: 'symbol', source: 'saved',
    layout: { 'text-field': ['get', 'name'], 'text-size': 11,
              'text-offset': [0, 1.1], 'text-anchor': 'top' },
    paint: { 'text-color': pos, 'text-halo-color': ground,
             'text-halo-width': 1.2 },
  })

  // -- places (clustered) ------------------------------------------------
  m.addLayer({
    id: 'places-cluster', type: 'circle', source: 'places',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': accent, 'circle-opacity': 0.28,
      'circle-radius': ['step', ['get', 'point_count'], 15, 5, 20, 15, 26],
      'circle-stroke-color': accent, 'circle-stroke-width': 1.5,
    },
  })
  m.addLayer({
    id: 'places-cluster-count', type: 'symbol', source: 'places',
    filter: ['has', 'point_count'],
    layout: { 'text-field': ['get', 'point_count_abbreviated'],
              'text-size': 11 },
    paint: { 'text-color': text },
  })
  m.addLayer({
    id: 'places-dot', type: 'circle', source: 'places',
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-radius': 5.5, 'circle-color': accent,
      'circle-stroke-color': ground, 'circle-stroke-width': 1.5,
    },
  })
  m.addLayer({
    id: 'places-label', type: 'symbol', source: 'places',
    filter: ['!', ['has', 'point_count']],
    minzoom: 13,
    layout: { 'text-field': ['get', 'name'], 'text-size': 10,
              'text-offset': [0, 1.1], 'text-anchor': 'top',
              'text-allow-overlap': false },
    paint: { 'text-color': text, 'text-halo-color': ground,
             'text-halo-width': 1.2, 'text-opacity': 0.85 },
  })

  // -- measurement -------------------------------------------------------
  m.addLayer({
    id: 'measure-line', type: 'line', source: 'measure',
    layout: { visibility: 'none', 'line-cap': 'round' },
    paint: { 'line-color': text, 'line-width': 2, 'line-dasharray': [1.5, 1.5] },
  })
  m.addLayer({
    id: 'measure-dot', type: 'circle', source: 'measure',
    layout: { visibility: 'none' },
    filter: ['==', ['get', 'kind'], 'point'],
    paint: { 'circle-radius': 4, 'circle-color': text,
             'circle-stroke-color': ground, 'circle-stroke-width': 1.5 },
  })
  m.addLayer({
    id: 'measure-label', type: 'symbol', source: 'measure',
    layout: {
      visibility: 'none', 'text-field': ['get', 'label'], 'text-size': 10,
      'text-offset': [0, -1.2], 'text-anchor': 'bottom',
      'text-allow-overlap': true,
    },
    filter: ['==', ['get', 'kind'], 'point'],
    paint: { 'text-color': text, 'text-halo-color': ground,
             'text-halo-width': 1.4 },
  })

  // -- focus and position, always on top --------------------------------
  m.addLayer({
    id: 'focus-ring', type: 'circle', source: 'focus',
    paint: {
      'circle-radius': 7, 'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-color': text, 'circle-stroke-width': 2,
    },
  })
  m.addLayer({
    id: 'me-dot', type: 'circle', source: 'me',
    paint: {
      'circle-radius': 6, 'circle-color': '#4a9eff',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 2.5,
    },
  })

  // Every colour above is also written by `restyle`, which is what keeps the
  // map following the theme afterwards. Running it once here means the two
  // paths cannot disagree about the initial paint — and if a layer is ever
  // added without a matching line in `restyle`, it will visibly fail to follow
  // an accent change rather than fail silently at some later date.
  restyle(m, currentMode())
}

export { Empty, Fresh }
