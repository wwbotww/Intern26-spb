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
    environment: 'node',
    watch: false,
    api: false,
    passWithNoTests: false,
    projects: [
      {
        extends: true,
        test: {
          name: 'contracts',
          include: ['src/**/*.test.ts'],
          exclude: ['src/agent-home.test.ts'],
          environment: 'node',
        },
      },
      {
        extends: true,
        test: {
          name: 'homepage',
          include: ['src/agent-home.test.ts'],
          environment: './vue-client-environment.ts',
        },
      },
    ],
  },
})
