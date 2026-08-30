// Turning OMNIX's compacted gazetteers into GeoJSON MapLibre can draw.
//
// `world.json`, `states.json` and `cities.json` predate this map by a year and
// are deliberately NOT GeoJSON — they are a compacted schema built for the old
// canvas renderer, where every byte was parsed on the main thread before the
// first frame. They stay compacted (the files are served from /static and
// nothing else should change), so the conversion happens here, once, at load.
//
// The shapes, for reference:
//   world/states: { features: [ { n: name, i: ISO-A2, g: polygons } ] }
//                 g is polygon -> ring -> [lon, lat], i.e. already MultiPolygon
//                 coordinates, which is the one lucky break in this file.
//   cities:       { c: [ [lon, lat, name, population, flag], ... ] }

import type { FeatureCollection, Feature, MultiPolygon, Point } from 'geojson'

export interface RawFeature { n?: string; i?: string; g: number[][][][] }

export interface RiskScore {
  iso2: string; name: string; score: number; band: string; color: string
  dimensions: Record<string, number>; top_dimension: string
  articles: number; confidence: number; sentiment: number
}

/** Countries as GeoJSON, with risk folded in as feature properties.
 *
 *  Risk lands on the FEATURE rather than being applied as a paint override per
 *  country, because MapLibre can then colour the whole layer with one
 *  data-driven expression instead of one layer per country. The old canvas
 *  drew 200 polygons a frame; this draws one layer.
 *
 *  `riskColor` is a literal colour string from the backend. It has to be —
 *  MapLibre parses paint values itself and cannot resolve a CSS custom
 *  property, which is the same constraint the graph and the old map both hit.
 */
export function countriesToGeoJSON(
  features: RawFeature[],
  risk: Record<string, RiskScore>,
): FeatureCollection<MultiPolygon> {
  const out: Feature<MultiPolygon>[] = []
  for (const f of features) {
    if (!f.g || !f.g.length) continue
    const iso = String(f.i || '').toUpperCase()
    const score = iso ? risk[iso] : undefined
    out.push({
      type: 'Feature',
      // The ISO code is the feature id so `setFeatureState` can drive hover
      // and selection without a lookup table. MapLibre needs a number or a
      // string here and silently ignores feature state when it is missing —
      // which reads as "hover does nothing" with no error anywhere.
      id: iso || undefined,
      properties: {
        name: String(f.n || iso || 'Unknown'),
        iso,
        hasRisk: !!score,
        risk: score ? score.score : 0,
        band: score ? score.band : '',
        riskColor: score ? score.color : '#00000000',
        topDimension: score ? score.top_dimension : '',
        articles: score ? score.articles : 0,
      },
      geometry: { type: 'MultiPolygon', coordinates: f.g },
    })
  }
  return { type: 'FeatureCollection', features: out }
}

/** Admin-1 boundaries. Lines only — no fill, no properties worth carrying. */
export function bordersToGeoJSON(
  features: RawFeature[],
): FeatureCollection<MultiPolygon> {
  return {
    type: 'FeatureCollection',
    features: features
      .filter((f) => f.g && f.g.length)
      .map((f) => ({
        type: 'Feature' as const,
        properties: { name: String(f.n || '') },
        geometry: { type: 'MultiPolygon' as const, coordinates: f.g },
      })),
  }
}

/** Cities as points, carrying population so the label layer can rank them.
 *
 *  1251 cities is far too many to label at world zoom; `population` drives
 *  both `symbol-sort-key` and a zoom-dependent filter so the biggest appear
 *  first and the rest fade in as you zoom. The old canvas did this with a
 *  hand-written `cityLimit()` per zoom step — the expression below replaces it
 *  and, unlike the original, keeps labels stable while panning. */
export function citiesToGeoJSON(rows: number[][]): FeatureCollection<Point> {
  return {
    type: 'FeatureCollection',
    features: (rows || [])
      .filter((c) => Array.isArray(c) && c.length >= 3)
      .map((c) => ({
        type: 'Feature' as const,
        properties: {
          name: String(c[2] ?? ''),
          population: Number(c[3] ?? 0),
          // Descending sort key: MapLibre draws low sort keys first and drops
          // later ones on collision, so this is what makes Tokyo survive and
          // an unnamed town lose.
          rank: -Number(c[3] ?? 0),
        },
        geometry: { type: 'Point' as const, coordinates: [Number(c[0]), Number(c[1])] },
      })),
  }
}

/** A graticule — meridians and parallels every `step` degrees.
 *
 *  Generated rather than shipped. Meridians are densified to 5° of latitude so
 *  they curve correctly under Web Mercator; drawing them as two-point lines
 *  makes them straight in projected space, which is right for a meridian and
 *  wrong for everything else, so the same densification is applied to both for
 *  consistency. */
export function graticule(step = 30): FeatureCollection {
  const features: Feature[] = []
  for (let lon = -180; lon <= 180; lon += step) {
    const line: [number, number][] = []
    for (let lat = -85; lat <= 85; lat += 5) line.push([lon, lat])
    features.push({
      type: 'Feature', properties: { kind: 'meridian' },
      geometry: { type: 'LineString', coordinates: line },
    })
  }
  for (let lat = -60; lat <= 60; lat += step) {
    const line: [number, number][] = []
    for (let lon = -180; lon <= 180; lon += 5) line.push([lon, lat])
    features.push({
      type: 'Feature', properties: { kind: 'parallel' },
      geometry: { type: 'LineString', coordinates: line },
    })
  }
  return { type: 'FeatureCollection', features }
}

/** A representative point per country, for pinning things that are known by
 *  ISO code and nothing more — TERRA's news clusters, which carry
 *  `countries: ['CO']` and no coordinates at all.
 *
 *  Two details make the difference between a pin in the country and a pin in
 *  the sea:
 *
 *    * **The largest ring wins, not the bounding box of all of them.** A bbox
 *      centre for the United States lands near Kansas only if Alaska and Hawaii
 *      are excluded; with them it lands in the Pacific. Ranking rings by area
 *      and using only the biggest picks the mainland every time.
 *    * **The centroid is area-weighted, not the average vertex.** Coastlines
 *      are digitised far more densely than inland borders, so averaging
 *      vertices drags the point towards whichever coast has the most detail —
 *      Norway ends up in the North Sea. The shoelace formula ignores vertex
 *      density.
 *
 *  Antimeridian-crossing countries (Russia, Fiji) are the known limitation:
 *  their largest ring is still contiguous in the source data, so they are
 *  correct, but a country genuinely split across ±180 would average to the
 *  wrong side. None in `world.json` is.
 */
export function countryCentroids(
  features: RawFeature[],
): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {}
  for (const f of features) {
    const iso = String(f.i || '').toUpperCase()
    if (!iso || !f.g?.length) continue
    let best: [number, number] | null = null
    let bestArea = 0
    for (const poly of f.g) {
      const ring = poly?.[0]
      if (!ring || ring.length < 3) continue
      // Shoelace: twice the signed area, and the same sum gives the centroid.
      let a2 = 0
      let cx = 0
      let cy = 0
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [x0, y0] = ring[j]
        const [x1, y1] = ring[i]
        const cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
      }
      const area = Math.abs(a2) / 2
      if (area <= bestArea || a2 === 0) continue
      bestArea = area
      best = [cx / (3 * a2), cy / (3 * a2)]
    }
    if (best && Number.isFinite(best[0]) && Number.isFinite(best[1])) {
      out[iso] = best
    }
  }
  return out
}

export const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

/** A circle as a polygon ring, latitude-corrected.
 *
 *  Dividing the longitude offset by cos(lat) is what stops a geofence "circle"
 *  rendering as an ellipse. Uncorrected it is 2.5% out in Bengaluru and a
 *  visibly squashed oval in Norway. */
export function circleRing(lat: number, lon: number, radiusM: number,
                           steps = 64): [number, number][] {
  const ring: [number, number][] = []
  const dLat = (radiusM / 6_371_008.8) * (180 / Math.PI)
  const dLon = dLat / Math.max(0.01, Math.cos((lat * Math.PI) / 180))
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI
    ring.push([lon + dLon * Math.cos(t), lat + dLat * Math.sin(t)])
  }
  return ring
}

/** Great-circle distance in metres. Used by the measurement tool, which must
 *  keep working with every provider unreachable. */
export function haversineM(a: [number, number], b: [number, number]): number {
  const R = 6_371_008.8
  const [lon1, lat1] = a
  const [lon2, lat2] = b
  const p1 = (lat1 * Math.PI) / 180
  const p2 = (lat2 * Math.PI) / 180
  const dp = p2 - p1
  const dl = ((lon2 - lon1) * Math.PI) / 180
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}
