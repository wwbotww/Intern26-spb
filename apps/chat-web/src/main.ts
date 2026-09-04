import { createApp } from 'vue'

import LegacyApp from './App.vue'
import AgentApp from './AgentApp.vue'
import './style.css'

const rootComponent =
  import.meta.env.VITE_ASSISTANT_UI_MODE === 'agent' ? AgentApp : LegacyApp

createApp(rootComponent).mount('#app')
