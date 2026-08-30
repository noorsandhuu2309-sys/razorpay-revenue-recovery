// Charts for model output, drawn as inline SVG.
//
// A model that is asked for "the numbers as a bar chart" can only answer in
// text, so the answer arrives as a markdown table and the reader does the
// plotting in their head. This closes that: the model emits a ```chart fence
// holding a small JSON spec and it is rendered as a real chart in the reply.
//
// Inline SVG rather than a charting library, for three reasons that all bite
// here specifically:
//
//   * No new dependency. The bundle already carries maplibre, ogl and gsap;
//     adding recharts/d3 for four chart types is weight for nothing.
//   * The spec is MODEL-AUTHORED, i.e. untrusted. Everything below goes through
//     React as text and numbers — there is no `dangerouslySetInnerHTML` and no
//     eval-shaped config, so a malformed or hostile spec renders wrong at
//     worst, never executes.
//   * Theme. Colours come from the accent ramp as literal hex read off the
//     computed root, because the canvas-facing tokens in this app must be
//     literal (see the workspace.css header) — a `color-mix()` value would
//     reach an SVG attribute unparsed.
//
// Anything the parser cannot make sense of falls back to the raw fenced block,
// so a bad spec degrades to "here is the JSON" rather than to a blank hole.

import { useMemo } from 'react'

export type ChartKind = 'bar' | 'column' | 'line' | 'area' | 'pie' | 'donut' | 'scatter'

export interface ChartPoint { label: string; value: number }
export interface ChartSeries { name?: string; points: ChartPoint[] }

export interface ChartSpec {
  type: ChartKind
  title?: string
  xLabel?: string
  yLabel?: string
  series: ChartSeries[]
  /** Force the value axis to start at zero. Default true for bar/area. */
  zero?: boolean
}

const KINDS: ChartKind[] = ['bar', 'column', 'line', 'area', 'pie', 'donut', 'scatter']

/** Parse a ```chart fence body into a spec, or null if it is not one.
 *
 *  Deliberately generous about shape. Models are consistent about intent and
 *  inconsistent about schema, so `{data: [...]}`, `{series: [...]}` and a bare
 *  array all mean the same thing and all three are accepted. Rejecting on a key
 *  name would mean showing the user raw JSON because the model said `data`
 *  where we wanted `series`. */
export function parseChart(body: string): ChartSpec | null {
  let raw: unknown
  try {
    raw = JSON.parse(body)
  } catch {
    return null
  }
  if (!raw || typeof raw !== 'object') return null
  const obj = Array.isArray(raw) ? { data: raw } : (raw as Record<string, unknown>)

  const kindRaw = String(obj.type ?? obj.kind ?? 'bar').toLowerCase().trim()
  const type = (KINDS.includes(kindRaw as ChartKind) ? kindRaw : 'bar') as ChartKind

  const toPoints = (arr: unknown): ChartPoint[] => {
    if (!Array.isArray(arr)) return []
    const out: ChartPoint[] = []
    for (const it of arr) {
      if (typeof it === 'number' && Number.isFinite(it)) {
        out.push({ label: String(out.length + 1), value: it })
        continue
      }
      if (!it || typeof it !== 'object') continue
      const r = it as Record<string, unknown>
      // `x`/`y` and `name`/`value` are both common; neither is more correct.
      const label = String(r.label ?? r.name ?? r.x ?? r.category ?? '')
      const rawV = r.value ?? r.y ?? r.count ?? r.amount
      const value = typeof rawV === 'number' ? rawV : Number(rawV)
      if (!Number.isFinite(value)) continue
      out.push({ label, value })
    }
    return out
  }

  let series: ChartSeries[] = []
  const s = obj.series ?? obj.data ?? obj.datasets
  if (Array.isArray(s) && s.length && typeof s[0] === 'object' && s[0] !== null
      && ('points' in (s[0] as object) || 'data' in (s[0] as object)
          || 'values' in (s[0] as object))) {
    // Multi-series: [{name, points|data|values}]
    series = (s as Record<string, unknown>[]).map((g) => ({
      name: g.name ? String(g.name) : undefined,
      points: toPoints(g.points ?? g.data ?? g.values),
    })).filter((g) => g.points.length)
  } else {
    const points = toPoints(s)
    if (points.length) series = [{ points }]
  }
  if (!series.length) return null

  return {
    type,
    title: obj.title ? String(obj.title) : undefined,
    xLabel: obj.xLabel ? String(obj.xLabel) : undefined,
    yLabel: obj.yLabel ? String(obj.yLabel) : undefined,
    zero: typeof obj.zero === 'boolean' ? obj.zero : undefined,
    series,
  }
}

/** Categorical colours.
 *
 *  Series 1 is the accent so a single-series chart matches the shell, and the
 *  rest are fixed hues chosen to stay distinguishable on both grounds. They are
 *  deliberately NOT accent-derived: five shades of one hue is not a categorical
 *  palette, it is a sequential one, and it reads as an ordering that the data
 *  does not have. */
const SERIES_COLORS = [
  'var(--omx-accent, #d4a545)',
  '#4f9dd9', '#63b98a', '#c96f8f', '#9b8ad4', '#d98f4f', '#6fb8c9',
]

const NUM = (n: number): string => {
  const a = Math.abs(n)
  if (a >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}k`
  if (Number.isInteger(n)) return String(n)
  return String(Math.round(n * 100) / 100)
}

/** "Nice" axis ticks — round numbers a human would have chosen. */
function ticks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) return min === 0 ? [0, 1] : [Math.min(0, min), Math.max(0, max)]
  const span = max - min
  const rough = span / count
  const mag = Math.pow(10, Math.floor(Math.log10(rough)))
  const norm = rough / mag
  const step = (norm >= 7.5 ? 10 : norm >= 3 ? 5 : norm >= 1.5 ? 2 : 1) * mag
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  const out: number[] = []
  // Guard the loop: a pathological step (0, or denormal) would spin forever on
  // a spec the model got wrong.
  for (let v = lo, i = 0; v <= hi + step / 2 && i < 40; v += step, i++) {
    out.push(Math.abs(v) < step / 1e6 ? 0 : v)
  }
  return out.length > 1 ? out : [lo, hi]
}

const W = 640
const H = 340

function CartesianChart({ spec }: { spec: ChartSpec }) {
  const { type, series } = spec
  const horizontal = type === 'bar'

  const labels = useMemo(() => {
    // The union of labels across series, in first-seen order, so two series
    // with a gap still line up on the same categories.
    const seen: string[] = []
    for (const g of series) {
      for (const p of g.points) if (!seen.includes(p.label)) seen.push(p.label)
    }
    return seen
  }, [series])

  const values = series.flatMap((g) => g.points.map((p) => p.value))
  const wantZero = spec.zero ?? (type !== 'line' && type !== 'scatter')
  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)
  const lo = wantZero ? Math.min(0, rawMin) : rawMin
  const hi = wantZero ? Math.max(0, rawMax) : rawMax
  const tk = ticks(lo, hi)
  const vMin = tk[0]
  const vMax = tk[tk.length - 1]
  const vSpan = vMax - vMin || 1

  // Axis titles are SEMANTIC, not positional: models label the category axis
  // `xLabel` and the measure `yLabel` whatever the orientation, because that is
  // how the data is described. A horizontal bar chart puts categories on the
  // left and values along the bottom, so the two titles have to swap sides —
  // printing them positionally captioned this exact chart "Country" under the
  // tonnage axis and "Lithium Reserves" beside the country names.
  const catTitle = spec.xLabel
  const valTitle = spec.yLabel
  const bottomTitle = horizontal ? valTitle : catTitle
  const sideTitle = horizontal ? catTitle : valTitle

  // Left gutter is measured from the widest tick label rather than fixed: a
  // fixed gutter clips "1.2M" and wastes half of it on "8". The rotated side
  // title needs its own strip on top of that, or it prints straight through the
  // category names.
  const AXIS_STRIP = 18
  const padL = (horizontal
    ? Math.min(180, Math.max(60, ...labels.map((l) => l.length * 6.6 + 14)))
    : Math.max(44, ...tk.map((t) => NUM(t).length * 7.4 + 14)))
    + (sideTitle ? AXIS_STRIP : 0)
  const padR = 16
  const padT = 16
  const padB = (horizontal ? 34 : 46) + (bottomTitle ? 16 : 0)
  const plotW = W - padL - padR
  const plotH = H - padT - padB

  const vx = (v: number) => padL + ((v - vMin) / vSpan) * plotW
  const vy = (v: number) => padT + plotH - ((v - vMin) / vSpan) * plotH
  const band = (horizontal ? plotH : plotW) / Math.max(1, labels.length)
  const cat = (i: number) => (horizontal ? padT : padL) + band * (i + 0.5)

  const zeroPos = horizontal ? vx(Math.max(vMin, Math.min(0, vMax))) : vy(Math.max(vMin, Math.min(0, vMax)))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="omx-chart-svg" role="img"
         aria-label={spec.title || `${type} chart`}>
      {/* Gridlines + value axis */}
      {tk.map((t) => {
        const p = horizontal ? vx(t) : vy(t)
        return (
          <g key={`t${t}`}>
            <line
              className={`omx-chart-grid ${t === 0 ? 'zero' : ''}`}
              x1={horizontal ? p : padL} x2={horizontal ? p : W - padR}
              y1={horizontal ? padT : p} y2={horizontal ? padT + plotH : p}
            />
            <text
              className="omx-chart-tick"
              x={horizontal ? p : padL - 8}
              y={horizontal ? padT + plotH + 16 : p}
              textAnchor={horizontal ? 'middle' : 'end'}
              dominantBaseline={horizontal ? 'auto' : 'middle'}
            >{NUM(t)}</text>
          </g>
        )
      })}

      {/* Category axis labels. Rotated once they would collide — measured from
          the band width, not from a hardcoded category count. */}
      {!horizontal && labels.map((l, i) => {
        const rotate = band < l.length * 7.2
        const x = cat(i)
        const y = padT + plotH + (rotate ? 12 : 18)
        return (
          <text
            key={`c${i}`} className="omx-chart-cat" x={x} y={y}
            textAnchor={rotate ? 'end' : 'middle'}
            transform={rotate ? `rotate(-38 ${x} ${y})` : undefined}
          >{l.length > 18 ? `${l.slice(0, 17)}…` : l}</text>
        )
      })}
      {horizontal && labels.map((l, i) => (
        <text key={`c${i}`} className="omx-chart-cat" x={padL - 8} y={cat(i)}
              textAnchor="end" dominantBaseline="middle">
          {l.length > 24 ? `${l.slice(0, 23)}…` : l}
        </text>
      ))}

      {series.map((g, si) => {
        const color = SERIES_COLORS[si % SERIES_COLORS.length]
        const at = (label: string) => g.points.find((p) => p.label === label)

        if (type === 'bar' || type === 'column') {
          const groupW = (band * 0.72) / series.length
          return (
            <g key={si}>
              {labels.map((l, i) => {
                const p = at(l)
                if (!p) return null
                const off = cat(i) - (band * 0.72) / 2 + groupW * si
                if (horizontal) {
                  const x0 = Math.min(zeroPos, vx(p.value))
                  const w = Math.abs(vx(p.value) - zeroPos)
                  return <rect key={i} className="omx-chart-bar" x={x0} y={off}
                               width={Math.max(1, w)} height={Math.max(1, groupW - 2)}
                               fill={color}><title>{`${l}: ${p.value}`}</title></rect>
                }
                const y0 = Math.min(zeroPos, vy(p.value))
                const h = Math.abs(vy(p.value) - zeroPos)
                return <rect key={i} className="omx-chart-bar" x={off} y={y0}
                             width={Math.max(1, groupW - 2)} height={Math.max(1, h)}
                             fill={color}><title>{`${l}: ${p.value}`}</title></rect>
              })}
            </g>
          )
        }

        // line / area / scatter share the same point geometry
        const pts = labels
          .map((l, i) => ({ p: at(l), i }))
          .filter((e): e is { p: ChartPoint; i: number } => !!e.p)
          .map(({ p, i }) => ({ x: cat(i), y: vy(p.value), p }))

        const d = pts.map((q, i) => `${i ? 'L' : 'M'}${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(' ')

        return (
          <g key={si}>
            {type === 'area' && pts.length > 1 && (
              <path className="omx-chart-area" fill={color}
                    d={`${d} L${pts[pts.length - 1].x.toFixed(1)},${zeroPos.toFixed(1)} L${pts[0].x.toFixed(1)},${zeroPos.toFixed(1)} Z`} />
            )}
            {type !== 'scatter' && pts.length > 1 && (
              <path className="omx-chart-line" d={d} stroke={color} fill="none" />
            )}
            {pts.map((q, i) => (
              <circle key={i} className="omx-chart-dot" cx={q.x} cy={q.y}
                      r={type === 'scatter' ? 4.5 : 3.2} fill={color}>
                <title>{`${q.p.label}: ${q.p.value}`}</title>
              </circle>
            ))}
          </g>
        )
      })}

      {sideTitle && (
        <text className="omx-chart-axis" transform={`rotate(-90 11 ${padT + plotH / 2})`}
              x={11} y={padT + plotH / 2} textAnchor="middle">{sideTitle}</text>
      )}
      {bottomTitle && (
        <text className="omx-chart-axis" x={padL + plotW / 2} y={H - 5}
              textAnchor="middle">{bottomTitle}</text>
      )}
    </svg>
  )
}

function PieChart({ spec }: { spec: ChartSpec }) {
  const points = spec.series[0]?.points ?? []
  // Negatives are meaningless in a part-of-whole chart and would draw an arc
  // backwards over its neighbour, so they are dropped rather than absolute-d
  // (which would silently misreport the data).
  const usable = points.filter((p) => p.value > 0)
  const total = usable.reduce((s, p) => s + p.value, 0)
  const R = 118
  const cx = 150
  const cy = 150
  const inner = spec.type === 'donut' ? R * 0.58 : 0

  if (!total) return <div className="omx-chart-bad">No positive values to plot.</div>

  let angle = -Math.PI / 2
  const arcs = usable.map((p, i) => {
    const sweep = (p.value / total) * Math.PI * 2
    const a0 = angle
    const a1 = angle + sweep
    angle = a1
    const large = sweep > Math.PI ? 1 : 0
    const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0)
    const x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1)
    // A single slice of 100% is a full circle: the arc's start and end points
    // coincide, so the path collapses to nothing and the chart renders blank.
    const full = usable.length === 1
    const d = full
      ? `M${cx},${cy - R} A${R},${R} 0 1 1 ${cx - 0.01},${cy - R} Z`
      : inner
        ? `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} `
          + `L${cx + inner * Math.cos(a1)},${cy + inner * Math.sin(a1)} `
          + `A${inner},${inner} 0 ${large} 0 ${cx + inner * Math.cos(a0)},${cy + inner * Math.sin(a0)} Z`
        : `M${cx},${cy} L${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} Z`
    return { d, p, color: SERIES_COLORS[i % SERIES_COLORS.length], pct: (p.value / total) * 100 }
  })

  return (
    <div className="omx-chart-pie">
      <svg viewBox="0 0 300 300" className="omx-chart-svg pie" role="img"
           aria-label={spec.title || 'pie chart'}>
        {arcs.map((a, i) => (
          <path key={i} d={a.d} fill={a.color} className="omx-chart-slice">
            <title>{`${a.p.label}: ${a.p.value} (${a.pct.toFixed(1)}%)`}</title>
          </path>
        ))}
      </svg>
      <ul className="omx-chart-legend">
        {arcs.map((a, i) => (
          <li key={i}>
            <span className="sw" style={{ background: a.color }} />
            <span className="lb">{a.p.label}</span>
            <span className="vl omx-mono">{NUM(a.p.value)} · {a.pct.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Chart({ spec }: { spec: ChartSpec }) {
  const pie = spec.type === 'pie' || spec.type === 'donut'
  const named = spec.series.filter((s) => s.name)

  return (
    <figure className="omx-chart">
      {spec.title && <figcaption className="omx-chart-title">{spec.title}</figcaption>}
      {pie ? <PieChart spec={spec} /> : <CartesianChart spec={spec} />}
      {!pie && named.length > 1 && (
        <ul className="omx-chart-legend row">
          {named.map((s, i) => (
            <li key={i}>
              <span className="sw" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
              <span className="lb">{s.name}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  )
}
