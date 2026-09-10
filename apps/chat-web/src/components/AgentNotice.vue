<script setup lang="ts">
defineProps<{ message: string }>()
const emit = defineEmits<{
  dismiss: []
  pause: [reason: 'pointer' | 'focus']
  resume: [reason: 'pointer' | 'focus']
}>()
</script>

<template>
  <!-- Keep the live region mounted, outside the scrolling conversation. -->
  <div class="agent-notice-region" role="status" aria-live="polite" aria-atomic="true">
    <Transition name="agent-notice">
      <div
        v-if="message"
        class="agent-notice"
        @mouseenter="emit('pause', 'pointer')"
        @mouseleave="emit('resume', 'pointer')"
        @focusin="emit('pause', 'focus')"
        @focusout="emit('resume', 'focus')"
      >
        <span class="agent-notice__icon" aria-hidden="true">i</span>
        <span class="agent-notice__text">{{ message }}</span>
        <button type="button" aria-label="关闭提示" @click="emit('dismiss')">×</button>
      </div>
    </Transition>
  </div>
</template>
