import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build emits to ../static, which the FastAPI app serves as the SPA (U23 §4 / D36).
// Dev proxies the API to the local FastAPI backend (uvicorn main:app on :8000).
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../static', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
