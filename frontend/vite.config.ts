import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// OMNIX React frontend — the canonical UI, served at `/`.
// - dev: proxies backend routes to the FastAPI server on 127.0.0.1:8000
//   (so /api/* fetches + SSE streams work with `npm run dev`).
// - build: emits to ../omnix/webapp, which FastAPI serves at the root.
export default defineConfig({
  // Root-served since the 2.65MB vanilla bundle was retired (2026-08-04).
  // `/workspace` still resolves — the server redirects it here — so existing
  // links and bookmarks keep working.
  base: '/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/static': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  // MapLibre's worker is imported as `?worker&url` in MapView.tsx and is an ES
  // module that imports maplibre's shared chunk. Vite's default IIFE worker
  // output cannot express that, so workers are emitted as ES modules.
  worker: {
    format: 'es',
  },
  build: {
    outDir: path.resolve(__dirname, '../omnix/webapp'),
    emptyOutDir: true,
  },
})
