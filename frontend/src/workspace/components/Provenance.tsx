// The two provenance marks, in one place.
//
// The brief's second hard problem was making provenance ambient rather than a
// rash of badges: four trust levels on every object, edge and event, legible on
// a 6px graph node and on a document header, without the interface becoming
// noise. The answer is two forms at two densities — a chip where there is room
// to read a word, a 7px dot everywhere else — and both live here so a view
// cannot invent a third.
//
// `ai_inferred` is drawn hollow and dashed in both forms. That is deliberate
// and load-bearing: it means the weakest level survives greyscale, colour-blind
// vision and a 7px render, which is the one case that actually matters. A
// confident-sounding claim built on inference must never look settled.

import type { Provenance } from '../lib/types'

const SHORT: Record<Provenance, string> = {
  user_created: 'User',
  verified: 'Verified',
  source_backed: 'Sourced',
  ai_inferred: 'AI',
}

const EXPLAIN: Record<Provenance, string> = {
  user_created: 'You stated this yourself',
  verified: 'Checked against a source',
  source_backed: 'OMNIX can cite a source for this',
  ai_inferred: 'A model inferred this — unverified',
}

/** The 7px mark. Default form: it costs almost no space, so it can appear on
 *  every row without the layout paying for it. */
export function ProvDot({ p }: { p: Provenance | string }) {
  const key = (p || 'ai_inferred') as Provenance
  return (
    <span
      className="omx-prov-dot"
      data-p={key}
      role="img"
      aria-label={`Provenance: ${SHORT[key] ?? key}`}
      title={EXPLAIN[key] ?? String(p)}
    />
  )
}

/** The spelled-out chip. Use where the level itself is the information —
 *  inspector header, claim header, output provenance strip. */
export function ProvChip({ p, label }: { p: Provenance | string; label?: string }) {
  const key = (p || 'ai_inferred') as Provenance
  return (
    <span className="omx-prov" data-p={key} title={EXPLAIN[key] ?? ''}>
      {label ?? SHORT[key] ?? key}
    </span>
  )
}
