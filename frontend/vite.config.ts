import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// In the cloud, CloudFront forwards /api/* to the HTTP API and strips the /api prefix
// (infra/stacks/web_stack.py). Locally scripts/local_api.py plays CloudFront + API Gateway:
// it expects the /api prefix and strips it itself, so the dev proxy forwards /api/* as is.
// (Stripping it here too made /api/health reach local_api.py as /health, which serves the
// SPA's index.html: found in the M1 integration run, 28 sep.)
const LOCAL_API = process.env.LOCAL_API_URL ?? 'http://127.0.0.1:8000'

// index.html carries the production CSP (script-src 'self', style-src 'self').
// The dev server needs inline scripts (React refresh preamble) and injected <style>
// tags, so the meta tag is removed only while serving. `vite build` keeps it.
function stripCspInDev(): Plugin {
  return {
    name: 'ev-strip-csp-in-dev',
    apply: 'serve',
    transformIndexHtml(html) {
      return html.replace(/\s*<meta\s+http-equiv="Content-Security-Policy"[^>]*>/i, '')
    },
  }
}

export default defineConfig({
  plugins: [react(), stripCspInDev()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: LOCAL_API,
        changeOrigin: true,
      },
    },
  },
})
