// TEMP — analysis-only config. Second dev server on 5174 proxying to the
// auth-off backend on 8010, so the instance on 5173/8000 stays untouched.
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: '/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8010', changeOrigin: true },
      '/static': { target: 'http://127.0.0.1:8010', changeOrigin: true },
    },
  },
  worker: { format: 'es' },
})
