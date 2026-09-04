<script setup lang="ts">
defineProps<{
  modelValue: string
  pending: boolean
  selectedLabel?: string
  waitingForInput: boolean
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
  const value = (event.target as HTMLTextAreaElement).value.trim()
  if (value) emit('send')
}
</script>

<template>
  <div class="composer-wrap agent-composer-wrap">
    <div class="composer">
      <textarea
        :value="modelValue"
        :disabled="pending"
        maxlength="2000"
        rows="3"
        :placeholder="
          waitingForInput
            ? '补充所需信息，也可以使用上方结构化输入'
            : '直接描述需求，Agent 会识别意图并补充缺失信息'
        "
        aria-label="向 Agent 发送消息"
        @input="handleInput"
        @keydown="handleKeydown"
      />
      <div class="composer__footer">
        <div class="composer__meta">
          <span class="composer__mode">
            {{ selectedLabel || '自动识别意图' }}
          </span>
          <span>{{ modelValue.length }} / 2000</span>
        </div>
        <button
          v-if="pending"
          type="button"
          class="send-button send-button--stop"
          aria-label="停止本轮 Agent 执行"
          title="停止"
          @click="emit('stop')"
        >
          ■
        </button>
        <button
          v-else
          type="button"
          class="send-button"
          :disabled="!modelValue.trim()"
          aria-label="发送消息"
          title="发送"
          @click="emit('send')"
        >
          ↑
        </button>
      </div>
    </div>
    <p class="composer-note">
      会话由 LangGraph checkpoint 持久化；浏览器不保存服务端或上游密钥。
    </p>
  </div>
</template>
