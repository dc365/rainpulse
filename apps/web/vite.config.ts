import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/.pnpm/ol@') || id.includes('/node_modules/ol/')) return 'gis'
          if (id.includes('/node_modules/')) return 'vendor'
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
  test: {
    environment: 'jsdom',
  },
})
