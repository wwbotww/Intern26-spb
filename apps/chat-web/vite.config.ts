import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiKey = env.CHAT_WEB_ASSISTANT_API_KEY ?? ''

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
        },
      },
    },
  }
})
