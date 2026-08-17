<script setup lang="ts">
import { computed } from 'vue'

import type { QueryMode } from '../api'

const props = defineProps<{
  modelValue: string
  mode: QueryMode
  pending: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  stop: []
}>()

const modeLabel = computed(() =>
  props.mode === 'policy' ? '政策、材料与流程' : '设备参考价格',
)

const placeholder = computed(() =>
  props.mode === 'policy'
    ? '请输入政策、所需材料或基础办理流程问题，Enter 发送'
    : '请输入设备品牌、完整型号及容量或内存规格，Enter 发送',
)

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
        :placeholder="placeholder"
        aria-label="请输入本次查询问题"
        @input="handleInput"
        @keydown="handleKeydown"
      />
      <div class="composer__footer">
        <div class="composer__meta">
          <span class="composer__mode">{{ modeLabel }}</span>
          <span>{{ modelValue.length }} / 2000</span>
        </div>
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
    <p class="composer-note">
      当前为单轮查询；页面记录不会作为上下文发送。回答仅供咨询参考。
    </p>
  </div>
</template>
