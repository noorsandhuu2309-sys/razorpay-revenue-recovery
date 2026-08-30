// The lens is only meaningful because it reports what survives. A filter that
// silently drops 30% of a Space without saying so is worse than no filter, so
// the count is asserted against real proportions from the demo workspace.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../lib/api', () => ({ api: {} }))

import { TrustLens } from './TrustLens'
import { useWorkspace } from '../store/workspace'

// The live Geopolitical Space, so the arithmetic below is the arithmetic a
// user actually sees rather than round numbers chosen to make it pass.
const SUMMARY = {
  objects: 632,
  byProvenance: {
    user_created: 0, verified: 346, source_backed: 99, ai_inferred: 187,
  },
} as never

beforeEach(() => {
  useWorkspace.setState({ provenanceFloor: 'ai_inferred', summary: SUMMARY })
})

describe('TrustLens', () => {
  it('offers every provenance level as a radio', () => {
    render(<TrustLens />)
    for (const label of ['Asserted', 'Verified', 'Cited', 'Everything']) {
      expect(screen.getByRole('radio', { name: label })).toBeInTheDocument()
    }
  })

  it('marks the active floor and no other', () => {
    render(<TrustLens />)
    expect(screen.getByRole('radio', { name: 'Everything' }))
      .toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Cited' }))
      .toHaveAttribute('aria-checked', 'false')
  })

  // At the weakest floor nothing is hidden, so a count would be noise implying
  // a filter is active when none is.
  it('shows no survival count while everything is visible', () => {
    render(<TrustLens />)
    expect(screen.queryByText(/shown/)).not.toBeInTheDocument()
  })

  it('reports what survives the Cited floor', async () => {
    render(<TrustLens />)
    await userEvent.click(screen.getByRole('radio', { name: 'Cited' }))

    expect(useWorkspace.getState().provenanceFloor).toBe('source_backed')
    // 0 asserted + 346 verified + 99 cited = 445 of 632; the 187 the model
    // merely inferred are gone.
    expect(screen.getByText('445/632 shown')).toBeInTheDocument()
  })

  it('reports a smaller survival at the stricter Verified floor', async () => {
    render(<TrustLens />)
    await userEvent.click(screen.getByRole('radio', { name: 'Verified' }))
    expect(screen.getByText('346/632 shown')).toBeInTheDocument()
  })

  it('counts nothing when the Space asserts nothing itself', async () => {
    render(<TrustLens />)
    await userEvent.click(screen.getByRole('radio', { name: 'Asserted' }))
    expect(screen.getByText('0/632 shown')).toBeInTheDocument()
  })

  it('survives an empty Space without dividing by nothing', () => {
    useWorkspace.setState({
      provenanceFloor: 'source_backed',
      summary: { objects: 0, byProvenance: {} } as never,
    })
    render(<TrustLens />)
    expect(screen.queryByText(/shown/)).not.toBeInTheDocument()
  })
})
