import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Locally, VITE_* settings come from the repo's .env.local (vercel env pull) and /api is
// proxied to `uvicorn backend.app:app --port 8000`. On Vercel, both are on one domain.
export default defineConfig({
  plugins: [react()],
  envDir: '..',
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
