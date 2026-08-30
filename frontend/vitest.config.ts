import path from 'node:path'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Test config is deliberately separate from vite.config.ts: the build config
// carries `base: '/'` and the tailwind plugin, neither of which a jsdom test
// run needs, and coupling them means a build tweak can break the suite.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Components import workspace.css for its side effects only; parsing ~46KB
    // of it per test file buys nothing, so stub CSS imports.
    css: false,
    restoreMocks: true,
  },
})
