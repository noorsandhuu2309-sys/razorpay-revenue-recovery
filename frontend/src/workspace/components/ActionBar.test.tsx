// Two rules the bar exists to enforce, both asserted here:
//   * arity HIDES a verb rather than disabling it
//   * every verb offered is real — the Create menu is built from the server's
//     own list of styles, so a "coming soon" button cannot be introduced by
//     editing the frontend alone.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Shaped exactly like a real `/api/outputs/styles` row, `formats` included —
// a fixture that drops a field the component renders turns a contract change
// into a crash in the test rather than a caught regression.
const outputStyles = vi.fn(async () => ({
  styles: [
    { key: 'report', label: 'Report', glyph: '▤', hint: 'Full report',
      minObjects: 1, formats: ['md', 'html'] },
    { key: 'chart', label: 'Chart', glyph: '◫', hint: 'A series',
      minObjects: 3, formats: ['html', 'csv'] },
  ],
}))

vi.mock('../lib/api', () => ({
  api: {
    outputStyles: () => outputStyles(),
    createOutput: vi.fn(async () => ({ output: { id: 'out1' } })),
    createIntent: vi.fn(async () => ({
      intent: { title: 'Monitor A', cadenceMinutes: 30 },
    })),
    object: vi.fn(async (_w: string, id: string) => ({ id, name: id })),
    graph: vi.fn(async () => ({ nodes: [], edges: [] })),
    summary: vi.fn(async () => null),
    timeline: vi.fn(async () => ({ events: [] })),
    thread: vi.fn(async () => ({ turns: [] })),
  },
}))

import { ActionBar } from './ActionBar'
import { useWorkspace } from '../store/workspace'

const object = (id: string) => ({
  id, name: id.toUpperCase(), glyph: '◆', color: '#d3ad55',
  type: 'country', provenance: 'verified',
})

/** Put `n` objects in hand, hydrated, the way a real selection would be. */
function hold(n: number) {
  const ids = ['a', 'b', 'c'].slice(0, n)
  useWorkspace.setState({
    workspaceId: 'ws1',
    selected: ids, pinned: [], primary: ids[n - 1] ?? null,
    contextObjects: Object.fromEntries(
      ids.map((id) => [id, object(id) as never]),
    ),
  })
}

beforeEach(() => {
  outputStyles.mockClear()
  useWorkspace.setState({
    workspaceId: 'ws1', selected: [], pinned: [], primary: null,
    contextObjects: {},
  })
})

describe('ActionBar visibility', () => {
  it('renders nothing at all when nothing is held', () => {
    const { container } = render(<ActionBar />)
    expect(container).toBeEmptyDOMElement()
  })

  it('reports how many objects are held', () => {
    hold(2)
    render(<ActionBar />)
    expect(screen.getByText('2 held')).toBeInTheDocument()
  })
})

describe('arity gating', () => {
  // Hidden, not disabled. A disabled button with a tooltip explaining why is
  // still a button the user has to read and dismiss.
  it('HIDES Compare and Connect with one object rather than disabling them', () => {
    hold(1)
    render(<ActionBar />)
    expect(screen.queryByRole('button', { name: /Compare/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect/ })).not.toBeInTheDocument()
  })

  it('offers the single-object verbs with one object', () => {
    hold(1)
    render(<ActionBar />)
    for (const verb of ['Focus', 'Research', 'Expand', 'Track', 'Create', 'Watch']) {
      expect(screen.getByRole('button', { name: new RegExp(verb) })).toBeInTheDocument()
    }
  })

  it('reveals Compare and Connect once a second object is held', () => {
    hold(2)
    render(<ActionBar />)
    expect(screen.getByRole('button', { name: /Compare/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Connect/ })).toBeInTheDocument()
  })
})

describe('Create menu is server-driven', () => {
  it('asks the server what it can build instead of hard-coding styles', async () => {
    hold(1)
    render(<ActionBar />)
    await userEvent.click(screen.getByRole('button', { name: /Create/ }))

    expect(outputStyles).toHaveBeenCalled()
    expect(await screen.findByRole('menuitem', { name: /Report/ })).toBeInTheDocument()
  })

  // A style the held selection cannot satisfy is disabled with the real reason,
  // which is honest; inventing a style the server never offered is not.
  it('disables a style whose minimum the selection does not meet', async () => {
    hold(1)
    render(<ActionBar />)
    await userEvent.click(screen.getByRole('button', { name: /Create/ }))

    expect(await screen.findByRole('menuitem', { name: /Report/ })).toBeEnabled()
    expect(screen.getByRole('menuitem', { name: /Chart/ })).toBeDisabled()
    expect(screen.getByRole('menuitem', { name: /Chart/ }))
      .toHaveAttribute('title', expect.stringContaining('at least 3'))
  })

  it('offers no styles of its own when the server returns none', async () => {
    outputStyles.mockResolvedValueOnce({ styles: [] } as never)
    hold(1)
    render(<ActionBar />)
    await userEvent.click(screen.getByRole('button', { name: /Create/ }))

    expect(await screen.findByText('Loading styles…')).toBeInTheDocument()
    expect(screen.queryAllByRole('menuitem')).toHaveLength(0)
  })
})
