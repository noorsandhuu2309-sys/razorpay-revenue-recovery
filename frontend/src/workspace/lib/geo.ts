// Types and client for TERRA's geospatial layer (/api/terra/geo/*).
//
// Kept out of `lib/api.ts` deliberately. That file is the workspace API —
// objects, claims, sources, the graph — and TERRA's spatial layer is a
// different subsystem with a different shape: every response carries provider
// and freshness metadata, which nothing in the workspace API does. Merging
// them would mean either polluting every workspace type with optional
// `freshness`, or losing it here.

const BASE = '/api/terra/geo'

/** How current a piece of data is. The single most important field in this
 *  file: the UI is required to render the difference, and `Freshness` exists
 *  so it cannot be forgotten. */
export type Freshness = 'live' | 'cached' | 'stale' | 'estimated' | 'offline'

export interface Meta {
  freshness: Freshness
  provider: string
  ageS: number | null
  error: string
  attempted: string[]
}

export interface GeoPlace {
  name: string
  coord: { lat: number; lon: number }
  lat: number
  lon: number
  category: string
  address: string
  distanceM: number | null
  distanceKind: string
  openingHours: string | null
  openNow: boolean | null
  rating: number | null
  ratingCount: number | null
  priceLevel: number | null
  phone: string | null
  website: string | null
  wheelchair: string | null
  tags: Record<string, string>
  externalId: string
  source: string
}

export interface GeoStep {
  instruction: string
  distanceM: number
  durationS: number
  coord: { lat: number; lon: number } | null
}

export interface GeoRoute {
  distanceM: number
  distanceKm: number
  durationS: number
  durationTrafficS: number | null
  /** Already decoded to [lat, lon] pairs — the client never sees a polyline. */
  geometry: [number, number][]
  steps: GeoStep[]
  summary: string
  mode: string
  tolls: boolean | null
  source: string
  score: number | null
  scoreParts: Record<string, number>
}

export interface GeoWeather {
  temperatureC: number | null
  feelsLikeC: number | null
  humidityPct: number | null
  precipitationMm: number | null
  precipitationProbabilityPct: number | null
  windKph: number | null
  windDirectionDeg: number | null
  uvIndex: number | null
  cloudCoverPct: number | null
  visibilityM: number | null
  code: number | null
  description: string
  emoji: string
  isDay: boolean | null
  sunrise: string | null
  sunset: string | null
  timezone: string
  utcOffsetS: number | null
  source: string
}

export interface GeoAir {
  index: number | null
  scale: string
  band: string
  pm25: number | null
  pm10: number | null
  ozone: number | null
  no2: number | null
  so2: number | null
  co: number | null
  dominant: string
  source: string
}

export interface HourlyPoint {
  time: string
  hour: number
  temperatureC: number | null
  precipitationProbabilityPct: number | null
  precipitationMm: number | null
  windKph: number | null
  uvIndex: number | null
  humidityPct: number | null
  code: number
  emoji: string
  description: string
}

export interface SavedPlace {
  id: string
  label: string
  slug: string
  kind: string
  lat: number
  lon: number
  address: string
  category: string
  notes: string
  tags: string[]
  visitCount: number
  lastVisitAt: string | null
  createdAt: string
  distanceM?: number
}

export interface Geofence {
  id: string
  label: string
  shape: string
  lat: number
  lon: number
  radiusM: number
  polygon: number[][]
  trigger: string
  action: string
  payload: Record<string, unknown>
  active: boolean
  inside: boolean
  lastEventAt: string | null
  createdAt: string
}

export interface GeofenceEvent {
  id: string
  geofenceId: string
  label: string
  transition: string
  lat: number
  lon: number
  dispatched: boolean
  createdAt: string
}

export interface GeoConfig {
  config: {
    offline: boolean
    cacheEnabled: boolean
    privacyMode: boolean
    historyEnabled: boolean
    historyRetentionDays: number
    providers: Record<string, { configured: boolean; enabled: boolean }>
    tiles: { dark: string; light: string; attribution: string }
    ttl: Record<string, number>
  }
  providers: Record<string, {
    available: boolean; circuitOpen: boolean; failures: number
    usage: Record<string, number | string | null>
  }>
  usage: {
    providers: Record<string, Record<string, number | string | null>>
    totals: {
      calls: number; hits: number; misses: number; errors: number
      hitRate: number; callsAvoided: number
    }
    memoryEntries: number
  }
  capabilities: Record<string, string[]>
}

export interface SpatialContext {
  time: {
    iso: string; local: string; hour: number; partOfDay: string
    weekday: string; isWeekend: boolean
    sun?: { sunrise: string | null; sunset: string | null; note: string
            offsetSource?: string }
  }
  currentLocation: { lat: number; lon: number; label: string
                     address?: string; country?: string } | null
  weather: GeoWeather | null
  airQuality: GeoAir | null
  nearbyPlaces: Record<string, unknown>[]
  knownLocations: SavedPlace[]
  activeGeofences: { id: string; label: string; trigger: string
                     inside: boolean; distanceM: number }[]
  elevation: { metres: number } | null
  dataStatus: Record<string, { freshness: Freshness; provider?: string
                               ageS?: number; error?: string } | string>
}

export interface RouteResponse extends Meta {
  routes: GeoRoute[]
  origin: { lat: number; lon: number; label: string }
  destination: { lat: number; lon: number; label: string }
  mode: string
  explanations?: string[]
  crossings?: Geofence[]
}

export interface EnvironmentResponse {
  weather: GeoWeather | null
  airQuality: GeoAir | null
  sun: { sunrise: string | null; sunset: string | null; note: string }
  signals: { concerns: string[]; favourable: string[]; assessed: boolean }
  dataStatus: Record<string, { freshness: Freshness; provider?: string }>
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body.error || body.detail || detail
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '' && v !== null) p.set(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const geo = {
  // -- configuration and health -----------------------------------------
  config: () => req<GeoConfig>('/config'),
  usage: () => req<{ usage: GeoConfig['usage']; health: Record<string, unknown> }>('/usage'),
  clearCache: (prefix = '') =>
    req<{ cleared: number }>('/cache/clear', {
      method: 'POST', body: JSON.stringify({ prefix }),
    }),
  categories: () => req<{ categories: string[] }>('/categories'),

  // -- location ----------------------------------------------------------
  location: (workspace: string) =>
    req<{ known: boolean; lat?: number; lon?: number; label?: string
          accuracyM?: number | null; at?: string; reason?: string }>(
      `/location${qs({ workspace })}`),

  /** Push a browser fix. Also evaluates geofences — see `api.observe_location`
   *  for why the two are one call. */
  observe: (workspace: string, lat: number, lon: number, accuracyM?: number) =>
    req<{ recorded: boolean; reason?: string
          geofenceEvents: { label: string; transition: string }[] }>(
      '/location', {
        method: 'POST',
        body: JSON.stringify({ workspace, lat, lon, accuracyM }),
      }),

  // -- geocoding ---------------------------------------------------------
  geocode: (workspace: string, q: string, near?: { lat: number; lon: number }) =>
    req<Meta & { results: GeoPlace[] }>(
      `/geocode${qs({ workspace, q, lat: near?.lat, lon: near?.lon })}`),

  reverse: (workspace: string, lat: number, lon: number) =>
    req<Meta & { place: GeoPlace | null }>(
      `/reverse${qs({ workspace, lat, lon })}`),

  // -- places ------------------------------------------------------------
  places: (lat: number, lon: number, opts: {
    q?: string; category?: string; radius?: number; limit?: number
    open_now?: boolean; ratings?: boolean
  } = {}) =>
    req<Meta & { places: GeoPlace[] }>(`/places${qs({ lat, lon, ...opts })}`),

  nearest: (lat: number, lon: number, category: string, radius = 5000) =>
    req<Meta & { places: GeoPlace[] }>(
      `/places/nearest${qs({ lat, lon, category, radius })}`),

  quiet: (lat: number, lon: number, radius = 5000, hours = 2) =>
    req<Meta & { places: GeoPlace[]; criteria: { note: string } }>(
      `/places/quiet${qs({ lat, lon, radius, hours })}`),

  // -- routing -----------------------------------------------------------
  route: (body: {
    workspace: string
    origin: { lat: number; lon: number; label?: string } | string
    destination: { lat: number; lon: number; label?: string } | string
    mode?: string; alternatives?: number; prefer?: string
    avoidWeather?: boolean
  }) => req<RouteResponse>('/route', {
    method: 'POST', body: JSON.stringify(body),
  }),

  chooseRoute: (body: {
    workspace: string
    origin: { lat: number; lon: number }
    destination: { lat: number; lon: number }
    routes: GeoRoute[]; chosenIndex: number; mode?: string
    originLabel?: string; destinationLabel?: string
  }) => req<{ learned: Record<string, number>
              weights: Record<string, number> }>('/route/choose', {
    method: 'POST', body: JSON.stringify(body),
  }),

  preferences: (workspace: string) =>
    req<{ weights: Record<string, number>; defaults: Record<string, number>
          factors: string[] }>(`/preferences${qs({ workspace })}`),

  setPreference: (workspace: string, key: string, weight: number) =>
    req<{ weights: Record<string, number> }>('/preferences', {
      method: 'POST', body: JSON.stringify({ workspace, key, weight }),
    }),

  resetPreferences: (workspace: string) =>
    req<{ reset: number; weights: Record<string, number> }>(
      `/preferences${qs({ workspace })}`, { method: 'DELETE' }),

  // -- environment -------------------------------------------------------
  weather: (lat: number, lon: number) =>
    req<Meta & { weather: GeoWeather | null }>(`/weather${qs({ lat, lon })}`),

  air: (lat: number, lon: number) =>
    req<Meta & { airQuality: GeoAir | null }>(`/air${qs({ lat, lon })}`),

  elevation: (lat: number, lon: number) =>
    req<Meta & { elevation: { metres: number } | null }>(
      `/elevation${qs({ lat, lon })}`),

  environment: (lat: number, lon: number) =>
    req<EnvironmentResponse>(`/environment${qs({ lat, lon })}`),

  /** The next N hours, for the forecast strip. */
  forecast: (lat: number, lon: number, hours = 24) =>
    req<Meta & { forecast: {
      hours: HourlyPoint[]; timezone: string; utcOffsetS: number
    } | null }>(`/forecast${qs({ lat, lon, hours })}`),

  /** Air quality sampled over a grid — one upstream call, not one per point. */
  airGrid: (lat: number, lon: number, radius = 12000, steps = 5) =>
    req<Meta & { grid: {
      points: { lat: number; lon: number; index: number; band: string
                scale: string; pm25: number | null }[]
      steps: number; min: number | null; max: number | null; scale: string
    } | null }>(`/air/grid${qs({ lat, lon, radius, steps })}`),

  /** Radar tile templates, oldest frame first. `kind` separates observed
   *  scans from nowcast, which the UI must not blur together. */
  radar: () =>
    req<Meta & { radar: {
      frames: { time: number; kind: 'observed' | 'forecast'; url: string }[]
      pastCount: number; nowcastCount: number; attribution: string
    } | null }>('/overlays/radar'),

  elevationProfile: (geometry: [number, number][]) =>
    req<Meta & { profile: {
      points: { lat: number; lon: number; elevationM: number }[]
      gainM: number; lossM: number; minM: number | null; maxM: number | null
    } }>('/elevation/profile', {
      method: 'POST', body: JSON.stringify({ geometry }),
    }),

  // -- spatial memory ----------------------------------------------------
  savedPlaces: (workspace: string) =>
    req<{ places: SavedPlace[] }>(`/places/saved${qs({ workspace })}`),

  savePlace: (body: {
    workspace: string; label: string; lat: number; lon: number
    kind?: string; address?: string; category?: string; notes?: string
  }) => req<SavedPlace>('/places/saved', {
    method: 'POST', body: JSON.stringify(body),
  }),

  deletePlace: (workspace: string, id: string) =>
    req<{ deleted: boolean }>(`/places/saved/${id}${qs({ workspace })}`,
                              { method: 'DELETE' }),

  history: (workspace: string, limit = 50) =>
    req<{ history: { id: string; lat: number; lon: number; label: string
                     arrivedAt: string; dwellS: number }[]
          enabled: boolean; privacyMode: boolean; retentionDays: number }>(
      `/history${qs({ workspace, limit })}`),

  forgetHistory: (workspace: string) =>
    req<{ deleted: number }>(`/history${qs({ workspace })}`,
                             { method: 'DELETE' }),

  privacy: (body: {
    privacyMode?: boolean; historyEnabled?: boolean
    retentionDays?: number; offline?: boolean
  }) => req<{ privacyMode: boolean; historyEnabled: boolean
              retentionDays: number; offline: boolean; note: string }>(
    '/privacy', { method: 'POST', body: JSON.stringify(body) }),

  // -- geofencing --------------------------------------------------------
  geofences: (workspace: string) =>
    req<{ geofences: Geofence[] }>(`/geofences${qs({ workspace })}`),

  createGeofence: (body: {
    workspace: string; label: string; lat: number; lon: number
    radiusM?: number; trigger?: string; action?: string
  }) => req<Geofence>('/geofences', {
    method: 'POST', body: JSON.stringify(body),
  }),

  deleteGeofence: (workspace: string, id: string) =>
    req<{ deleted: boolean }>(`/geofences/${id}${qs({ workspace })}`,
                              { method: 'DELETE' }),

  toggleGeofence: (workspace: string, id: string, active: boolean) =>
    req<{ updated: boolean }>(`/geofences/${id}/active`, {
      method: 'POST', body: JSON.stringify({ workspace, active }),
    }),

  geofenceEvents: (workspace: string, limit = 20) =>
    req<{ events: GeofenceEvent[] }>(
      `/geofences/events${qs({ workspace, limit })}`),

  // -- context and natural language --------------------------------------
  context: (workspace: string, lat: number, lon: number,
            opts: { places?: boolean; category?: string; radius?: number } = {}) =>
    req<SpatialContext>(`/context${qs({ workspace, lat, lon, ...opts })}`),

  ask: (workspace: string, text: string, at?: { lat: number; lon: number }) =>
    req<{ matched: boolean; path: string; tool?: string
          args?: Record<string, unknown>; result?: unknown
          ok?: boolean; error?: string; note?: string }>('/ask', {
      method: 'POST',
      body: JSON.stringify({ workspace, text, lat: at?.lat, lon: at?.lon }),
    }),
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------
export function humanDistance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return ''
  if (metres < 1000) return `${Math.round(metres / 10) * 10} m`
  if (metres < 10_000) return `${(metres / 1000).toFixed(1)} km`
  return `${Math.round(metres / 1000)} km`
}

export function humanDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  const mins = Math.round(seconds / 60)
  if (mins < 1) return 'under a min'
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m === 0 ? `${h} h` : `${h} h ${m} min`
}

/** The one-word label and tone for a freshness value.
 *
 *  `tone` drives colour. Note that `estimated` is warned about but `cached` is
 *  not: cached data is real data that was true recently, while an estimate was
 *  never measured at all. Collapsing the two would either cry wolf on every
 *  cache hit or say nothing about a straight-line "route". */
export function freshnessLabel(f: Freshness): { text: string; tone: string } {
  switch (f) {
    case 'live': return { text: 'Live', tone: 'ok' }
    case 'cached': return { text: 'Cached', tone: 'muted' }
    case 'stale': return { text: 'Stale', tone: 'warn' }
    case 'estimated': return { text: 'Estimated', tone: 'warn' }
    default: return { text: 'Offline', tone: 'bad' }
  }
}

export function ageLabel(ageS: number | null | undefined): string {
  if (!ageS) return ''
  if (ageS < 90) return `${Math.round(ageS)}s ago`
  if (ageS < 5400) return `${Math.round(ageS / 60)} min ago`
  if (ageS < 172800) return `${Math.round(ageS / 3600)} h ago`
  return `${Math.round(ageS / 86400)} d ago`
}
