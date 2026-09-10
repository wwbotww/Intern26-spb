import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiKey = env.CHAT_WEB_ASSISTANT_API_KEY ?? ''
  if (env.VITE_AGENT_BROWSER_SESSION === 'true') {
    if (env.VITE_ASSISTANT_UI_MODE !== 'agent') throw new Error('Browser identity requires the Agent UI')
    if (command === 'serve' && !apiKey.trim()) throw new Error('Browser identity requires a server-side proxy key')
  }

  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 3000,
      proxy: {
        '/api': {
          target:
            env.CHAT_WEB_ASSISTANT_API_URL ?? 'http://127.0.0.1:8081',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          headers: apiKey
            ? { Authorization: `Bearer ${apiKey}` }
            : {},
          configure(proxy) {
            proxy.on('proxyReq', (request) => {
              for (const name of ['X-API-Key', 'X-User-ID', 'X-Agent-Owner', 'X-Forwarded-User']) request.removeHeader(name)
            })
          },
        },
      },
    },
  }
})
