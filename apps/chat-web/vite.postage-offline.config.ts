// Explicit local-only QA profile. Does not load .env or expose keys to the client.
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const port = Number(process.env.POSTAGE_OFFLINE_API_PORT ?? '18083')
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid offline API port')
const uiMode = process.env.POSTAGE_OFFLINE_UI_MODE ?? 'agent'
if (!['agent', 'legacy'].includes(uiMode)) throw new Error('Invalid offline UI mode')

export default defineConfig({
  plugins: [vue()], envDir: false,
  define: {
    'import.meta.env.VITE_ASSISTANT_UI_MODE': JSON.stringify(uiMode),
    'import.meta.env.VITE_AGENT_BROWSER_SESSION': JSON.stringify('false'),
  },
  server: {
    host: '127.0.0.1', port: 13003, strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${port}`, changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        headers: { Authorization: 'Bearer postage-p3-synthetic-key' },
      },
    },
  },
})
