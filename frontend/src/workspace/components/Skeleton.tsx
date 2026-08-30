// Loading placeholders shaped like the content they stand in for.
//
// Every data view used to render a bare 12px `.omx-spin` while its fetch was in
// flight. On a workspace with 644 objects and 358 sources that is a second or
// more of a nearly-empty pane, and a spinner says only "wait" — it does not say
// what is coming, so the layout lurches into place when it lands. A skeleton
// that matches the real row rhythm reserves the space up front, which is why
// the same wait reads as faster.
//
// Skeletons are placeholders, NOT data. They never imply a count: the row
// counts below are chosen to fill a viewport, and no caller should read one as
// "this many results are coming".

interface SkelProps {
  /** CSS width — a percentage keeps rows ragged like real text. */
  w?: string | number
  h?: number
  /** Mono-ish blocks (counts, tiers) get the small radius, prose the pill. */
  round?: boolean
}

/** One shimmering block. */
export function Skel({ w = '100%', h = 12, round = false }: SkelProps) {
  return (
    <span
      className="omx-skel"
      style={{ width: typeof w === 'number' ? `${w}px` : w, height: h,
               borderRadius: round ? 999 : 'var(--omx-r)' }}
    />
  )
}

/** Wrapper that announces the wait once, for screen readers, and hides the
 *  decorative blocks from them — a reader should hear "Loading", not 60
 *  anonymous boxes. */
function Frame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="omx-skel-frame" role="status" aria-live="polite" aria-busy="true">
      <span className="omx-sr">{label}</span>
      <div aria-hidden="true">{children}</div>
    </div>
  )
}

/** Rows under a header — the Table view. Widths vary per column so the block
 *  reads as a table rather than a grid of identical bars. */
export function SkeletonTable({ rows = 14 }: { rows?: number }) {
  return (
    <Frame label="Loading objects">
      <div className="omx-skel-toolbar">
        <Skel w={280} h={30} />
        <Skel w={90} h={10} />
      </div>
      <div className="omx-skel-table">
        {Array.from({ length: rows }, (_, i) => (
          <div className="row" key={i}>
            <Skel w={10} h={10} round />
            <Skel w={`${52 + ((i * 13) % 34)}%`} />
            <Skel w={72} />
            <Skel w={84} h={11} round />
            <Skel w={38} />
            <Skel w={24} />
          </div>
        ))}
      </div>
    </Frame>
  )
}

/** Stacked cards — Brief, Claims, Compare. */
export function SkeletonCards({ rows = 5 }: { rows?: number }) {
  return (
    <Frame label="Loading">
      <div className="omx-skel-cards">
        {Array.from({ length: rows }, (_, i) => (
          <div className="card" key={i}>
            <div className="head">
              <Skel w={64} h={11} round />
              <Skel w={`${28 + ((i * 17) % 26)}%`} h={13} />
            </div>
            <Skel w={`${74 + ((i * 9) % 22)}%`} />
            <Skel w={`${40 + ((i * 11) % 30)}%`} />
          </div>
        ))}
      </div>
    </Frame>
  )
}

/** Source-library rows: tier chip, title block, credibility meter. */
export function SkeletonSources({ rows = 7 }: { rows?: number }) {
  return (
    <Frame label="Loading sources">
      <div className="omx-skel-sources">
        {Array.from({ length: rows }, (_, i) => (
          <div className="row" key={i}>
            <Skel w={92} h={13} round />
            <div className="body">
              <Skel w={`${46 + ((i * 15) % 38)}%`} h={13} />
              <Skel w={128} h={10} />
              <Skel w={`${68 + ((i * 7) % 28)}%`} />
            </div>
            <Skel w={64} h={10} />
          </div>
        ))}
      </div>
    </Frame>
  )
}

/** Dated groups of one-line entries — the Timeline. */
export function SkeletonTimeline({ groups = 3, perGroup = 4 }: {
  groups?: number; perGroup?: number
}) {
  return (
    <Frame label="Loading timeline">
      <div className="omx-skel-timeline">
        {Array.from({ length: groups }, (_, g) => (
          <div className="group" key={g}>
            <Skel w={104} h={10} />
            <div className="items">
              {Array.from({ length: perGroup }, (_, i) => (
                <div className="item" key={i}>
                  <Skel w={`${50 + ((g * 19 + i * 13) % 40)}%`} h={13} />
                  <Skel w={`${22 + ((g * 7 + i * 11) % 18)}%`} h={10} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Frame>
  )
}
