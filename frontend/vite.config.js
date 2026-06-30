import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward /api to the FastAPI backend so the browser doesn't hit CORS in dev.
      '/api': 'http://localhost:8000',
    },
  },
})
