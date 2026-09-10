// Default unit tests must not inherit the development proxy or load local secrets.
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  envDir: false,
  define: {
    'import.meta.env.VITE_ASSISTANT_UI_MODE': JSON.stringify('agent'),
    'import.meta.env.VITE_AGENT_BROWSER_SESSION': JSON.stringify('false'),
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
    watch: false,
    api: false,
    passWithNoTests: false,
  },
})
