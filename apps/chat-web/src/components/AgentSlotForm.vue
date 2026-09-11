<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import type { PublicIntent, RequiredInput } from '../agent-api'
import { slotReply } from '../agent-slot-input'


const props = defineProps<{
  inputs: RequiredInput[]
  pending: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { message: string; confirmOverwrite: boolean }]
  'select-intent': [intent: PublicIntent]
  'select-price': [payload: { token: string; label: string }]
}>()

const values = reactive<Record<string, string>>({})
const confirmOverwrite = reactive({ value: false })

const intentInput = computed(() =>
  props.inputs.find((item) => item.name === 'intent' && item.type === 'choice'),
)
const fieldInputs = computed(() =>
  props.inputs.filter((item) => item.name !== 'intent' && item.name !== 'price_selection'),
)
const priceCandidates = computed(() => props.inputs.find((item) => item.name === 'price_selection')?.price_candidates ?? [])
function selectPrice(token: string, label: string, expires: string): void {
  if (!props.pending && Date.parse(expires) > Date.now()) emit('select-price', { token, label })
}
const complete = computed(() =>
  Boolean(slotReply(fieldInputs.value, values)),
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
      product_price: '商品价格',
      tracking: '邮件轨迹',
      delivery_time: '寄递时限',
      postage: '邮费试算',
    }[intent] ?? intent
  )
}


function chooseIntent(value: string): void {
  if (
    ['policy', 'device_price', 'product_price', 'tracking', 'delivery_time', 'postage'].includes(
      value,
    )
  ) {
    emit('select-intent', value as PublicIntent)
  }
}


function submit(): void {
  if (!complete.value || props.pending || (confirmationRequired.value && !confirmOverwrite.value)) return
  const message = slotReply(fieldInputs.value, values)
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

    <div v-if="priceCandidates.length" class="agent-intent-choices" aria-label="选择商品候选">
      <button v-for="candidate in priceCandidates" :key="candidate.candidate_token" type="button"
        :disabled="pending || Date.parse(candidate.expires_at) <= Date.now()"
        @click="selectPrice(candidate.candidate_token, candidate.label, candidate.expires_at)">
        {{ candidate.label }}{{ Date.parse(candidate.expires_at) <= Date.now() ? '（已过期，请重新查询）' : '' }}
      </button>
    </div>

    <form v-if="fieldInputs.length" class="agent-slot-form" @submit.prevent="submit">
      <label v-for="input in fieldInputs" :key="input.name">
        <span>{{ input.label }}</span>
        <select
          v-if="input.type === 'choice'"
          v-model="values[input.name]"
          :disabled="pending"
          :aria-label="input.label"
        >
          <option disabled value="">请选择{{ input.label }}</option>
          <option v-for="choice in input.choices" :key="choice" :value="choice">{{ choice }}</option>
        </select>
        <input
          v-else
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
