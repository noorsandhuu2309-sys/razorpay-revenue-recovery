// Switching Space starts a new request without cancelling the one in flight.
// Nothing guarantees the replies come back in the order they were sent, so the
// slower one lands last — and before these effects were guarded, that reply
// won and the view showed the Space the user had just left.
//
// The test drives exactly that order: request A is held open, the Space changes
// to B, B answers, and only then does A. B's rows must survive.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void }
const defer = <T,>(): Deferred<T> => {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const pending: Record<string, Deferred<{ objects: unknown[] }>> = {}

const objectsFor = (ws: string) => {
  pending[ws] = defer<{ objects: unknown[] }>()
  return pending[ws].promise
}

vi.mock('../lib/api', () => ({
  api: {
    objects: (ws: string) => objectsFor(ws),
    sources: async () => ({ sources: [] }),
    claims: async () => ({ claims: [] }),
    timeline: async () => ({ events: [] }),
    brief: async () => null,
  },
}))

import { TableView } from './Views'
import { useWorkspace } from '../store/workspace'

const row = (id: string, name: string) => ({
  id, name, glyph: '◆', color: '#d3ad55', type: 'country',
  provenance: 'verified', degree: 1, externalId: `terra:country:${id}`,
})

describe('a reply for the previous Space cannot overwrite the current one', () => {
  beforeEach(() => {
    for (const k of Object.keys(pending)) delete pending[k]
    useWorkspace.setState({ workspaceId: 'space-a' })
  })

  it('keeps the newer Space when the older reply arrives last', async () => {
    render(<TableView />)
    await waitFor(() => expect(pending['space-a']).toBeDefined())

    // The user switches Space before A has answered.
    useWorkspace.setState({ workspaceId: 'space-b' })
    await waitFor(() => expect(pending['space-b']).toBeDefined())

    // B answers first...
    pending['space-b'].resolve({ objects: [row('bb', 'BELONGS TO B')] })
    await screen.findByText('BELONGS TO B')

    // ...and the abandoned request for A answers afterwards.
    pending['space-a'].resolve({ objects: [row('aa', 'BELONGS TO A')] })

    // Give the late reply every chance to land before asserting it did not.
    await new Promise((r) => setTimeout(r, 50))

    expect(screen.queryByText('BELONGS TO A')).toBeNull()
    expect(screen.getByText('BELONGS TO B')).toBeTruthy()
  })
})
