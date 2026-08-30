// The trust lens is the one control OMNIX can ship that competitors cannot copy
// without rebuilding the provenance discipline underneath it. That makes its
// filter semantics a product invariant, not an implementation detail: if the
// lens ever admits something weaker than the floor, the product is lying about
// evidence, which is worse than having no lens at all.

import { describe, expect, it } from 'vitest'
import { PROVENANCE_ORDER, passesLens, provenanceRank } from './workspace'
import type { Provenance } from '../lib/types'

const at = (p: Provenance) => ({ provenance: p })

describe('provenance ordering', () => {
  it('matches the backend rank order, strongest first', () => {
    expect(PROVENANCE_ORDER).toEqual([
      'user_created', 'verified', 'source_backed', 'ai_inferred',
    ])
  })

  it('ranks strongest as 0 and weakest as last', () => {
    expect(provenanceRank('user_created')).toBe(0)
    expect(provenanceRank('ai_inferred')).toBe(PROVENANCE_ORDER.length - 1)
    expect(provenanceRank('verified')).toBeLessThan(provenanceRank('source_backed'))
  })

  // The backend defaults every extraction to the weakest level. A provenance
  // the frontend does not recognise must be treated the same way — anything
  // else lets an unknown value slip past a strict floor and render as evidence.
  it('treats an unrecognised provenance as the weakest, never the strongest', () => {
    expect(provenanceRank('totally_made_up')).toBe(PROVENANCE_ORDER.length - 1)
    expect(passesLens({ provenance: 'totally_made_up' }, 'source_backed')).toBe(false)
    expect(passesLens({ provenance: '' }, 'verified')).toBe(false)
  })
})

describe('passesLens', () => {
  it('admits everything at the weakest floor', () => {
    for (const p of PROVENANCE_ORDER) {
      expect(passesLens(at(p), 'ai_inferred')).toBe(true)
    }
  })

  // "Cited" is the floor an analyst sets before acting on anything, so this is
  // the single most important assertion in the frontend suite.
  it('drops ai_inferred at the Cited floor and keeps everything stronger', () => {
    expect(passesLens(at('ai_inferred'), 'source_backed')).toBe(false)
    expect(passesLens(at('source_backed'), 'source_backed')).toBe(true)
    expect(passesLens(at('verified'), 'source_backed')).toBe(true)
    expect(passesLens(at('user_created'), 'source_backed')).toBe(true)
  })

  it('drops source_backed at the Verified floor', () => {
    expect(passesLens(at('source_backed'), 'verified')).toBe(false)
    expect(passesLens(at('verified'), 'verified')).toBe(true)
    expect(passesLens(at('user_created'), 'verified')).toBe(true)
  })

  it('admits only user_created at the strictest floor', () => {
    expect(passesLens(at('user_created'), 'user_created')).toBe(true)
    for (const p of ['verified', 'source_backed', 'ai_inferred'] as Provenance[]) {
      expect(passesLens(at(p), 'user_created')).toBe(false)
    }
  })

  // Monotonicity: tightening the floor can only ever remove things. Stated as a
  // property so a future reordering of PROVENANCE_ORDER cannot quietly invert
  // the lens while every individual case above still reads plausibly.
  it('is monotonic — a stricter floor never admits more', () => {
    for (const item of PROVENANCE_ORDER) {
      let previous = true
      for (const floor of [...PROVENANCE_ORDER].reverse()) {
        const now = passesLens(at(item), floor)
        expect(now && !previous).toBe(false)
        previous = now
      }
    }
  })
})
