<script setup lang="ts">
const props = defineProps<{
  modelValue: string
  pending: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  stop: []
}>()

function handleInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  if (!props.pending && props.modelValue.trim()) emit('send')
}
</script>

<template>
  <div class="composer-wrap">
    <div class="composer">
      <textarea
        :value="modelValue"
        :disabled="pending"
        maxlength="2000"
        rows="3"
        placeholder="请输入政策法规相关问题，Enter 发送，Shift+Enter 换行"
        aria-label="请输入问题"
        @input="handleInput"
        @keydown="handleKeydown"
      />
      <div class="composer__footer">
        <span>{{ modelValue.length }} / 2000</span>
        <button
          v-if="pending"
          type="button"
          class="send-button send-button--stop"
          aria-label="停止生成"
          title="停止生成"
          @click="emit('stop')"
        >
          ■
        </button>
        <button
          v-else
          type="button"
          class="send-button"
          :disabled="!modelValue.trim()"
          aria-label="发送问题"
          title="发送问题"
          @click="emit('send')"
        >
          ↑
        </button>
      </div>
    </div>
    <p class="composer-note">回答由知识库资料生成，请以政策原文为准。</p>
  </div>
</template>
