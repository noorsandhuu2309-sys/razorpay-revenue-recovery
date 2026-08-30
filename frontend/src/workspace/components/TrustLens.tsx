// The provenance trust lens.
//
// This is not in the blueprint. It is here because it is the one control only
// OMNIX can ship: every object and edge already carries an honest provenance
// level, defaulting to the weakest, and nothing in the system can mint
// `verified` for an extraction. That data exists in no competing product, so
// this filter cannot be copied without first rebuilding the discipline
// underneath it.
//
// What it does: sets a floor. At `source_backed`, everything a model merely
// inferred disappears from every view at once, and the user is looking only at
// what OMNIX can cite. Flipping it back is the fastest way to see how much of a
// picture is actually evidence — which is the question an analyst asks before
// they act on anything.

import { PROVENANCE_ORDER, provenanceRank, useWorkspace } from '../store/workspace'
import type { Provenance } from '../lib/types'

const LABEL: Record<Provenance, string> = {
  user_created: 'Asserted',
  verified: 'Verified',
  source_backed: 'Cited',
  ai_inferred: 'Everything',
}

const HINT: Record<Provenance, string> = {
  user_created: 'Only what you stated yourself',
  verified: 'Only checked facts and what you stated',
  source_backed: 'Only what OMNIX can cite a source for',
  ai_inferred: 'Including everything a model inferred',
}

/** Loosest first, so the bars climb left-to-right as the floor gets stricter.
 *  `PROVENANCE_ORDER` runs strongest-first because it mirrors the backend rank;
 *  the control reads better in the opposite direction, so it is reversed once
 *  here rather than the ranks being redefined. */
const STEPS: Provenance[] = [...PROVENANCE_ORDER].reverse()

/** 0 for the loosest floor, 3 for the strictest. */
const strictness = (p: Provenance): number =>
  PROVENANCE_ORDER.length - 1 - provenanceRank(p)

export function TrustLens() {
  const floor = useWorkspace((s) => s.provenanceFloor)
  const setFloor = useWorkspace((s) => s.setProvenanceFloor)
  const summary = useWorkspace((s) => s.summary)

  // How much of the Space survives the current floor — the number that makes
  // the control meaningful rather than decorative. Measured from the real
  // per-provenance counts; if the backend never reported them there is no
  // number to show and the readout is omitted rather than guessed.
  const total = summary?.objects ?? 0
  const kept = total
    ? PROVENANCE_ORDER
      .slice(0, PROVENANCE_ORDER.indexOf(floor) + 1)
      .reduce((n, p) => n + (summary?.byProvenance[p] ?? 0), 0)
    : 0
  const filtering = floor !== 'ai_inferred'

  return (
    <div className={`omx-trustlens ${filtering ? 'on' : ''}`}>
      <span className="omx-label">Trust</span>
      {/* A four-step equaliser rather than four text buttons. It is the only
          control in the topbar that shows its own state as a magnitude, which
          is the point: raising the floor should visibly cost you material. */}
      <div className="omx-trustlens-steps" role="radiogroup" aria-label="Provenance floor">
        {STEPS.map((p) => (
          <button
            key={p}
            role="radio"
            aria-checked={p === floor}
            aria-label={LABEL[p]}
            className={`omx-trustlens-step ${p} ${strictness(p) <= strictness(floor) ? 'lit' : ''} ${p === floor ? 'on' : ''}`}
            style={{ height: `${11 + strictness(p) * 2.4}px` }}
            title={`${LABEL[p]} — ${HINT[p]}`}
            onClick={() => setFloor(p)}
          />
        ))}
      </div>
      {filtering && total > 0 && (
        <span className="omx-label kept">
          {kept}/{total} shown
        </span>
      )}
    </div>
  )
}
