import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// Vexcel and Matterport are called through the dev/preview server so their keys stay on this machine and never
// reach the browser bundle. Put VEXCEL_API_KEY, MATTERPORT_API_KEY, MATTERPORT_API_TOKEN in web/.env.local
// (gitignored). Without keys the property sheet just hides the aerial and the 3D details.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxy: Record<string, object> = {}
  if (env.VEXCEL_API_KEY) {
    proxy['/vendor/vexcel'] = {
      target: 'https://api.vexcelgroup.com/v2',
      changeOrigin: true,
      rewrite: (p: string) => p.replace(/^\/vendor\/vexcel/, ''),
      headers: { 'X-API-KEY': env.VEXCEL_API_KEY },
    }
  }
  if (env.MATTERPORT_API_KEY && env.MATTERPORT_API_TOKEN) {
    const basic = Buffer.from(`${env.MATTERPORT_API_KEY}:${env.MATTERPORT_API_TOKEN}`).toString('base64')
    proxy['/vendor/matterport'] = {
      target: 'https://api.matterport.com/api/models/graph',
      changeOrigin: true,
      rewrite: () => '',
      headers: { Authorization: `Basic ${basic}` },
    }
  }
  return {
    plugins: [react()],
    server: { port: 5173, proxy },
    preview: { proxy },
    // maplibre-gl 6 loads its worker relative to its own module; pre-bundling breaks that path.
    optimizeDeps: { exclude: ['maplibre-gl'] },
    define: {
      __VEXCEL__: JSON.stringify(Boolean(env.VEXCEL_API_KEY)),
      __MATTERPORT__: JSON.stringify(Boolean(env.MATTERPORT_API_KEY && env.MATTERPORT_API_TOKEN)),
    },
  }
})
