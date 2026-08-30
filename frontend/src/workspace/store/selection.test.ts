// The shared selection bus. Every view reads one selection, so the rules below
// are what stop the workspace from either looping forever or quietly losing the
// user's place. `origin` is the load-bearing field: without it view A selects,
// B reacts, B re-emits, A reacts, forever. Single selection hides this because
// it converges on itself; a selection SET does not.

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../lib/api', () => ({
  api: {
    object: vi.fn(async (_ws: string, id: string) => ({
      id, name: id, type: 'country', provenance: 'verified',
    })),
    graph: vi.fn(async () => ({ nodes: [], edges: [] })),
    summary: vi.fn(async () => null),
    timeline: vi.fn(async () => ({ events: [] })),
    thread: vi.fn(async () => ({ turns: [] })),
  },
}))

import { contextIds, useWorkspace } from './workspace'

const reset = () => useWorkspace.setState({
  workspaceId: 'ws1',
  primary: null, selected: [], pinned: [], origin: 'system',
  contextObjects: {}, focus: [], graph: null,
})

beforeEach(reset)

describe('selection records its origin', () => {
  // Views compare this against their own id and ignore the echo. If a mutation
  // ever forgets to stamp it, the originating view reacts to itself.
  it('stamps the causing view on select, toggle and selectMany', () => {
    useWorkspace.getState().select('a', 'graph')
    expect(useWorkspace.getState().origin).toBe('graph')

    useWorkspace.getState().toggle('b', 'map')
    expect(useWorkspace.getState().origin).toBe('map')

    useWorkspace.getState().selectMany(['c', 'd'], 'table')
    expect(useWorkspace.getState().origin).toBe('table')
  })

  it('defaults to system when no view claims the change', () => {
    useWorkspace.getState().select('a')
    expect(useWorkspace.getState().origin).toBe('system')
  })
})

describe('select and toggle', () => {
  it('select replaces the whole selection', () => {
    useWorkspace.getState().selectMany(['a', 'b', 'c'], 'graph')
    useWorkspace.getState().select('z', 'graph')
    expect(useWorkspace.getState().selected).toEqual(['z'])
    expect(useWorkspace.getState().primary).toBe('z')
  })

  it('toggle adds an absent id and removes a present one', () => {
    useWorkspace.getState().toggle('a', 'graph')
    expect(useWorkspace.getState().selected).toEqual(['a'])
    useWorkspace.getState().toggle('a', 'graph')
    expect(useWorkspace.getState().selected).toEqual([])
  })

  // Removing the focused object must hand focus to whatever remains. Blanking
  // the Inspector instead is how a graph product loses the user's place.
  it('hands primary to the survivors when the focused object is toggled off', () => {
    useWorkspace.getState().selectMany(['a', 'b'], 'graph')
    expect(useWorkspace.getState().primary).toBe('b')
    useWorkspace.getState().toggle('b', 'graph')
    expect(useWorkspace.getState().selected).toEqual(['a'])
    expect(useWorkspace.getState().primary).toBe('a')
  })

  it('keeps primary when an unrelated object is toggled off', () => {
    useWorkspace.getState().selectMany(['a', 'b'], 'graph')
    useWorkspace.getState().toggle('a', 'graph')
    expect(useWorkspace.getState().primary).toBe('b')
  })

  it('empties primary when the last object leaves', () => {
    useWorkspace.getState().select('a', 'graph')
    useWorkspace.getState().toggle('a', 'graph')
    expect(useWorkspace.getState().primary).toBeNull()
  })
})

describe('deselect and clear', () => {
  it('deselect hands primary to the survivors', () => {
    useWorkspace.getState().selectMany(['a', 'b'], 'graph')
    useWorkspace.getState().deselect('b')
    expect(useWorkspace.getState().primary).toBe('a')
  })

  it('clearSelection empties everything and returns origin to system', () => {
    useWorkspace.getState().selectMany(['a', 'b'], 'graph')
    useWorkspace.getState().clearSelection()
    expect(useWorkspace.getState().selected).toEqual([])
    expect(useWorkspace.getState().primary).toBeNull()
    expect(useWorkspace.getState().origin).toBe('system')
  })
})

describe('pinning and context', () => {
  it('pin is idempotent', () => {
    useWorkspace.getState().pin('a')
    useWorkspace.getState().pin('a')
    expect(useWorkspace.getState().pinned).toEqual(['a'])
  })

  it('unpin removes only the named id', () => {
    useWorkspace.getState().pin('a')
    useWorkspace.getState().pin('b')
    useWorkspace.getState().unpin('a')
    expect(useWorkspace.getState().pinned).toEqual(['b'])
  })

  // The Context Lens is what the AI is told the user means. An id both selected
  // and pinned must appear once, or it is weighted twice in the prompt.
  it('contextIds unions selected and pinned without duplicates', () => {
    useWorkspace.getState().selectMany(['a', 'b'], 'graph')
    useWorkspace.getState().pin('b')
    useWorkspace.getState().pin('c')
    const ids = contextIds(useWorkspace.getState())
    expect([...ids].sort()).toEqual(['a', 'b', 'c'])
  })

  it('pinned objects survive a selection change', () => {
    useWorkspace.getState().pin('keep')
    useWorkspace.getState().select('a', 'graph')
    useWorkspace.getState().select('b', 'graph')
    expect(useWorkspace.getState().pinned).toEqual(['keep'])
    expect(contextIds(useWorkspace.getState())).toContain('keep')
  })
})
