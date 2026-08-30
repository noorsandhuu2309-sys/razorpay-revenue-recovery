import { describe, expect, it } from 'vitest'
import { GROUPS, groupDefaultOpen, groupOpen, viewDef } from './views'

// `groupOpen` weighs three inputs, and every consumer has to weigh them in the
// same order or the rail and the fold toggle disagree. These lock that order.
describe('groupOpen', () => {
  it('opens the group containing the current view, even when it defaults shut', () => {
    // The case that shipped broken: the World Map is the landing view and it
    // lives in TERRA, which starts folded. Without this the app opened on a
    // view whose own rail entry was off screen.
    expect(groupDefaultOpen('TERRA')).toBe(false)
    expect(viewDef('map').group).toBe('TERRA')
    expect(groupOpen('TERRA', 'map', {})).toBe(true)
  })

  it('leaves other groups on their registered default', () => {
    expect(groupOpen('TERRA', 'claims', {})).toBe(false)
    expect(groupOpen('More', 'claims', {})).toBe(false)
    expect(groupOpen('Research', 'claims', {})).toBe(true)
  })

  it('lets an explicit fold win in both directions', () => {
    // Closing the group you are in must be possible...
    expect(groupOpen('TERRA', 'map', { TERRA: false })).toBe(false)
    // ...and opening one you are not in must stick.
    expect(groupOpen('TERRA', 'claims', { TERRA: true })).toBe(true)
    expect(groupOpen('Research', 'map', { Research: false })).toBe(false)
  })

  // This is what stops the fold toggle swallowing the first click: it negates
  // whatever this function returns, so a single call site is the only way the
  // rendered state and the toggled state can agree.
  it('is stable under a toggle round trip from any starting point', () => {
    for (const { label } of GROUPS) {
      for (const view of ['map', 'claims'] as const) {
        const before = groupOpen(label, view, {})
        const toggled = { [label]: !before }
        expect(groupOpen(label, view, toggled)).toBe(!before)
      }
    }
  })

  it('falls back to open for a group nobody registered', () => {
    expect(groupOpen('Nonexistent', 'claims', {})).toBe(true)
  })
})

describe('the view registry', () => {
  it('gives the World Map the chrome its own sidebar needs', () => {
    // The map's right edge is its own panel stack, which is exactly where the
    // Inspector would sit, and it owns two text inputs of its own.
    const map = viewDef('map')
    expect(map.inspector).toBe(false)
    expect(map.novaBar).toBe(false)
    expect(map.trustLens).toBe(false)
  })

  it('never throws on an unregistered view', () => {
    // @ts-expect-error deliberately not a ViewId
    expect(viewDef('nope')).toBe(viewDef('home'))
  })
})
