import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { ViewBoundary } from './ViewBoundary'

function Boom({ when = true }: { when?: boolean }) {
  if (when) throw new Error('blocks.find is not a function')
  return <div>view content</div>
}

describe('ViewBoundary', () => {
  beforeEach(() => {
    // React logs caught render errors; the boundary logs its own too. Neither
    // is a test failure, and letting them through buries real output.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  it('shows the failure instead of unmounting the tree', () => {
    render(
      <div>
        <span>shell survives</span>
        <ViewBoundary resetKey="challenge"><Boom /></ViewBoundary>
      </div>,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('shell survives')).toBeInTheDocument()
  })

  it('surfaces the error message so the fault is diagnosable', () => {
    render(<ViewBoundary resetKey="challenge"><Boom /></ViewBoundary>)
    expect(
      screen.getByText(/blocks\.find is not a function/),
    ).toBeInTheDocument()
  })

  it('renders children normally when nothing throws', () => {
    render(
      <ViewBoundary resetKey="home"><Boom when={false} /></ViewBoundary>,
    )
    expect(screen.getByText('view content')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('clears when the user navigates to another view', async () => {
    function Harness() {
      const [view, setView] = useState('challenge')
      return (
        <div>
          <button onClick={() => setView('home')}>go home</button>
          <ViewBoundary resetKey={view}>
            <Boom when={view === 'challenge'} />
          </ViewBoundary>
        </div>
      )
    }
    render(<Harness />)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    await userEvent.click(screen.getByText('go home'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('view content')).toBeInTheDocument()
  })

  it('lets the user retry the same view', async () => {
    let shouldThrow = true
    function Flaky() {
      if (shouldThrow) throw new Error('transient')
      return <div>recovered</div>
    }
    render(<ViewBoundary resetKey="challenge"><Flaky /></ViewBoundary>)
    expect(screen.getByRole('alert')).toBeInTheDocument()

    shouldThrow = false
    await userEvent.click(screen.getByText('Try again'))
    expect(screen.getByText('recovered')).toBeInTheDocument()
  })
})
