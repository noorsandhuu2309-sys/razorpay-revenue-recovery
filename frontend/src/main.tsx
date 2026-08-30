// Entry point for the Intelligence Workspace, served at /.
//
// This app deliberately does NOT mount the older App.tsx experiment; that shell
// was reverted in August and is kept on disk for reference only.
//
// The two font families workspace.css is written against are imported HERE and
// nowhere else. Without them the whole design system silently fell back to
// system-ui and Consolas: every `--omx-mono` label, stat, table cell and code
// block rendered in a face the type scale was never tuned for. `src/index.css`
// is deliberately still not imported — it carries the abandoned
// shadcn/tailwind scaffold from that reverted experiment, and its :root tokens
// would fight workspace.css.
// The explicit `/index.css` suffix is load-bearing: a bare package specifier
// has no type declaration and fails `tsc -b`, while the extension matches
// vite/client's `*.css` module declaration.
import '@fontsource-variable/geist/index.css'
import '@fontsource-variable/geist-mono/index.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Workspace from './workspace/Workspace'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Workspace />
  </StrictMode>,
)
