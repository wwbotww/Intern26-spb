import type { Environment } from 'vitest/environments'

// Compile SFCs for their client lifecycle while using Vue's in-memory renderer.
// No real browser, DOM package, network proxy, or dotenv is needed by this test.
export default {
  name: 'vue-memory',
  viteEnvironment: 'client',
  setup: () => ({ teardown: () => {} }),
} satisfies Environment
