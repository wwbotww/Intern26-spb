// Synthetic loopback 6A-1 QA only. No dotenv; proxy key never enters the JS bundle.
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()], envDir: false,
  define: {
    'import.meta.env.VITE_ASSISTANT_UI_MODE': JSON.stringify('agent'),
    'import.meta.env.VITE_AGENT_BROWSER_SESSION': JSON.stringify('true'),
  },
  server: {
    host: '127.0.0.1', port: 13006, strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18086', changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        headers: { Authorization: 'Bearer postage-p3-synthetic-key' },
        configure(proxy) {
          proxy.on('proxyReq', (request) => {
            for (const name of ['X-API-Key', 'X-User-ID', 'X-Agent-Owner', 'X-Forwarded-User']) request.removeHeader(name)
          })
        },
      },
    },
  },
})
