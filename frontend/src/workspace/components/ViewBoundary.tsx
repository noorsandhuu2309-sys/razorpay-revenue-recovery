// A crash in one view must not take the workspace with it.
//
// React unmounts the whole tree when a render throws, so a single bad
// assumption in one view blanked the entire app — sidebar, tabs, spaces and
// all — leaving a black rectangle and no way back except a reload. That
// happened for real: CHALLENGE read `output.blocks` as an array when the
// execution API returns a count there, and one `.find` on a number ended the
// session.
//
// The blast radius should be the view, not the product. This boundary keeps
// the shell alive and gives the user somewhere to go, and it resets when the
// user navigates, so a transient failure does not strand them on the error.

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  /** Changing this resets the boundary — pass the active view id. */
  resetKey: string
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ViewBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props) {
    // Navigating away is the user's own "try something else", so clear the
    // error rather than making them reload to escape a view they have left.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept as console output on purpose: there is no error-reporting backend,
    // and swallowing it silently would make the next one just as hard to find.
    console.error('[omnix] view crashed:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="omx-view-error" role="alert">
        <h3>Something went wrong</h3>
        <p>
          The rest of the workspace is fine — switch to another view, or try
          this one again.
        </p>
        <button
          className="omx-btn"
          onClick={() => this.setState({ error: null })}
        >
          Retry
        </button>
      </div>
    )
  }
}
