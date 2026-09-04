<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import type { PublicIntent, RequiredInput } from '../agent-api'


const props = defineProps<{
  inputs: RequiredInput[]
  pending: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { message: string; confirmOverwrite: boolean }]
  'select-intent': [intent: PublicIntent]
}>()

const values = reactive<Record<string, string>>({})
const confirmOverwrite = reactive({ value: false })

const intentInput = computed(() =>
  props.inputs.find((item) => item.name === 'intent' && item.type === 'choice'),
)
const fieldInputs = computed(() =>
  props.inputs.filter((item) => item.name !== 'intent'),
)
const complete = computed(() =>
  fieldInputs.value.every((item) => (values[item.name] ?? '').trim()),
)
const confirmationRequired = computed(() =>
  props.inputs.some((item) =>
    (item.validation_hint ?? '').includes('confirm_overwrite=true'),
  ),
)

watch(
  () => props.inputs,
  (inputs) => {
    for (const key of Object.keys(values)) delete values[key]
    for (const input of inputs) values[input.name] = ''
    confirmOverwrite.value = false
  },
  { immediate: true },
)


function intentLabel(intent: string): string {
  return (
    {
      policy: '政策查询',
      device_price: '设备价格',
      tracking: '邮件轨迹',
      delivery_time: '寄递时限',
      postage: '邮费试算',
    }[intent] ?? intent
  )
}


function chooseIntent(value: string): void {
  if (
    ['policy', 'device_price', 'tracking', 'delivery_time', 'postage'].includes(
      value,
    )
  ) {
    emit('select-intent', value as PublicIntent)
  }
}


function submit(): void {
  if (!complete.value || props.pending) return
  const message = fieldInputs.value
    .map((item) => `${item.label}：${values[item.name].trim()}`)
    .join('；')
  emit('submit', {
    message,
    confirmOverwrite: confirmationRequired.value && confirmOverwrite.value,
  })
}
</script>

<template>
  <section class="agent-slot-panel" aria-label="补充 Agent 所需信息">
    <div class="agent-slot-panel__heading">
      <div>
        <strong>继续这一步</strong>
        <p>这些字段来自服务端的结构化 interrupt。</p>
      </div>
      <span>{{ inputs.length }} 项待补充</span>
    </div>

    <div v-if="intentInput" class="agent-intent-choices">
      <button
        v-for="choice in intentInput.choices"
        :key="choice"
        type="button"
        :disabled="pending"
        @click="chooseIntent(choice)"
      >
        {{ intentLabel(choice) }}
      </button>
    </div>

    <form v-if="fieldInputs.length" class="agent-slot-form" @submit.prevent="submit">
      <label v-for="input in fieldInputs" :key="input.name">
        <span>{{ input.label }}</span>
        <input
          v-model="values[input.name]"
          type="text"
          :inputmode="input.type === 'number' ? 'decimal' : 'text'"
          :disabled="pending"
          :placeholder="input.validation_hint || `请输入${input.label}`"
          :aria-label="input.label"
        />
        <small v-if="input.validation_hint">{{ input.validation_hint }}</small>
      </label>
      <label v-if="confirmationRequired" class="agent-confirm-overwrite">
        <input v-model="confirmOverwrite.value" type="checkbox" />
        <span>确认以本次输入覆盖此前识别值</span>
      </label>
      <button
        type="submit"
        class="agent-primary-button"
        :disabled="pending || !complete || (confirmationRequired && !confirmOverwrite.value)"
      >
        提交并继续
      </button>
    </form>
  </section>
</template>
