<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { ChatApiError, streamChat } from './api'
import type { ChatEvent, Evidence, QueryMode } from './api'
import ChatComposer from './components/ChatComposer.vue'
import ChatMessage from './components/ChatMessage.vue'
import { createId } from './id'

type MessageState =
  | 'preparing'
  | 'streaming'
  | 'done'
  | 'partial'
  | 'needs_info'
  | 'refused'
  | 'error'
  | 'aborted'

interface Message {
  id: string
  role: 'user' | 'assistant'
  mode: QueryMode
  content: string
  state: MessageState
  evidence: Evidence[]
  warnings: string[]
  missingFields: string[]
  statusText?: string
  requestId?: string
  reasonCode?: string
  error?: string
  createdAt: number
}

interface ModeOption {
  label: string
  shortLabel: string
  icon: string
  description: string
  placeholder: string
  suggestions: string[]
}

const modeOptions: Record<QueryMode, ModeOption> = {
  policy: {
    label: '政策、材料与流程',
    shortLabel: '政策查询',
    icon: '政',
    description: '查询公开理赔政策、所需材料和基础办理流程，并查看原文来源。',
    placeholder: '例如：办理快件理赔通常需要准备哪些证明材料？',
    suggestions: [
      '办理快件理赔通常需要准备哪些证明材料？',
      '快递服务发生丢失后有哪些相关规定？',
      '用户申诉和基础办理流程有哪些公开规定？',
    ],
  },
  device_price: {
    label: '设备参考价格',
    shortLabel: '设备价格',
    icon: '价',
    description: '按品牌、完整型号和规格查询已采集的设备价格候选记录。',
    placeholder: '例如：某品牌某型号 256GB 的参考价格是多少？',
    suggestions: [
      '查询某品牌某型号 256GB 的参考价格',
      '某品牌某型号 16GB+512GB 有哪些价格记录？',
      '某品牌某型号不同容量的价格分别是多少？',
    ],
  },
}
const modeChoices: QueryMode[] = ['policy', 'device_price']

const messages = ref<Message[]>([])
const selectedMode = ref<QueryMode | null>(null)
const draft = ref('')
const pending = ref(false)
const messageList = ref<HTMLElement | null>(null)
let activeController: AbortController | null = null
let scrollFrame: number | null = null

const currentMode = computed(() =>
  selectedMode.value ? modeOptions[selectedMode.value] : null,
)

function queueScroll(): void {
  if (scrollFrame !== null) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    const element = messageList.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function finishState(reason: string): MessageState {
  if (reason === 'partial') return 'partial'
  if (reason === 'insufficient_information') return 'needs_info'
  if (['no_match', 'out_of_scope'].includes(reason)) return 'refused'
  if (reason === 'tool_error') return 'error'
  return 'done'
}

function selectMode(mode: QueryMode): void {
  if (pending.value) return
  selectedMode.value = mode
  draft.value = ''
  void nextTick(queueScroll)
}

function changeMode(): void {
  if (pending.value) return
  selectedMode.value = null
  draft.value = ''
  void nextTick(() => {
    if (messageList.value) messageList.value.scrollTop = 0
  })
}

async function submit(): Promise<void> {
  const question = draft.value.trim()
  const mode = selectedMode.value
  if (!question || !mode || pending.value) return

  const now = Date.now()
  const assistantIndex = messages.value.length + 1
  messages.value.push({
    id: createId(),
    role: 'user',
    mode,
    content: question,
    state: 'done',
    evidence: [],
    warnings: [],
    missingFields: [],
    createdAt: now,
  })
  messages.value.push({
    id: createId(),
    role: 'assistant',
    mode,
    content: '',
    state: 'preparing',
    evidence: [],
    warnings: [],
    missingFields: [],
    statusText: `正在准备${modeOptions[mode].shortLabel}`,
    createdAt: Date.now(),
  })

  draft.value = ''
  pending.value = true
  activeController = new AbortController()
  const requestId = `web-${createId()}`
  await nextTick()
  queueScroll()

  const handleEvent = (event: ChatEvent): void => {
    const assistant = messages.value[assistantIndex]
    if (!assistant) return

    if (event.type === 'status') {
      assistant.statusText = event.message
      assistant.state = 'preparing'
    } else if (event.type === 'evidence') {
      assistant.evidence = event.items
      assistant.state = 'streaming'
    } else if (event.type === 'delta') {
      assistant.content += event.content
      assistant.state = 'streaming'
    } else if (event.type === 'done') {
      assistant.requestId = event.requestId || requestId
      assistant.warnings = event.warnings
      assistant.missingFields = event.missingFields
      assistant.reasonCode = event.reasonCode
      assistant.state = finishState(event.finishReason)
      if (assistant.state === 'error' && !assistant.error) {
        assistant.error = '查询工具执行失败，请稍后重试。'
      }
    } else if (event.type === 'error') {
      assistant.requestId = event.requestId || assistant.requestId
      assistant.error = event.message
      assistant.state = 'error'
    }
    queueScroll()
  }

  try {
    await streamChat({
      mode,
      question,
      requestId,
      signal: activeController.signal,
      onEvent: handleEvent,
    })
  } catch (error) {
    const assistant = messages.value[assistantIndex]
    if (assistant) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        assistant.state = 'aborted'
      } else {
        assistant.state = 'error'
        assistant.error =
          error instanceof ChatApiError
            ? error.message
            : '连接理赔咨询服务失败，请稍后重试。'
      }
    }
  } finally {
    pending.value = false
    activeController = null
    queueScroll()
  }
}

function askSuggestion(question: string): void {
  if (pending.value || !selectedMode.value) return
  draft.value = question
  void submit()
}

function stop(): void {
  activeController?.abort()
}

function clearMessages(): void {
  if (!pending.value) messages.value = []
}

onBeforeUnmount(() => {
  activeController?.abort()
  if (scrollFrame !== null) cancelAnimationFrame(scrollFrame)
})
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="brand">
        <span class="brand__mark" aria-hidden="true">邮</span>
        <div>
          <h1>中国邮政理赔助手 <small>Demo</small></h1>
          <p>政策依据与设备参考价格智能咨询</p>
        </div>
      </div>
      <div class="header-actions">
        <button
          v-if="selectedMode"
          type="button"
          class="mode-chip"
          :disabled="pending"
          @click="changeMode"
        >
          <span>{{ currentMode?.icon }}</span>
          {{ currentMode?.shortLabel }} · 更换
        </button>
        <button
          v-if="messages.length"
          type="button"
          class="text-button"
          :disabled="pending"
          @click="clearMessages"
        >
          清空记录
        </button>
      </div>
    </header>

    <main ref="messageList" class="message-list" aria-live="polite">
      <section v-if="!selectedMode" class="mode-selection">
        <div class="welcome__icon" aria-hidden="true">理</div>
        <p class="welcome__eyebrow">外部智能咨询窗口</p>
        <h2>您要查询哪方面的问题？</h2>
        <p class="welcome__description">
          请选择本次查询类别。每次问题独立处理，不会使用此前对话作为上下文。
        </p>
        <div class="mode-options">
          <button
            v-for="mode in modeChoices"
            :key="mode"
            type="button"
            class="mode-option"
            @click="selectMode(mode)"
          >
            <span class="mode-option__icon" aria-hidden="true">
              {{ modeOptions[mode].icon }}
            </span>
            <span class="mode-option__content">
              <strong>{{ modeOptions[mode].label }}</strong>
              <small>{{ modeOptions[mode].description }}</small>
              <em>进入{{ modeOptions[mode].shortLabel }} →</em>
            </span>
          </button>
        </div>
        <p v-if="messages.length" class="mode-selection__history-note">
          已有查询记录会保留在页面中，但不会随下一次请求发送。
        </p>
      </section>

      <section v-else-if="!messages.length" class="welcome">
        <div class="welcome__icon" aria-hidden="true">
          {{ currentMode?.icon }}
        </div>
        <p class="welcome__eyebrow">当前类别 · {{ currentMode?.shortLabel }}</p>
        <h2>{{ currentMode?.label }}</h2>
        <p class="welcome__description">
          {{ currentMode?.description }}
        </p>
        <div class="suggestions">
          <button
            v-for="suggestion in currentMode?.suggestions"
            :key="suggestion"
            type="button"
            :disabled="pending"
            @click="askSuggestion(suggestion)"
          >
            {{ suggestion }}
          </button>
        </div>
      </section>

      <div v-if="messages.length" class="messages">
        <ChatMessage
          v-for="message in messages"
          :key="message.id"
          :role="message.role"
          :mode="message.mode"
          :content="message.content"
          :state="message.state"
          :evidence="message.evidence"
          :warnings="message.warnings"
          :missing-fields="message.missingFields"
          :status-text="message.statusText"
          :request-id="message.requestId"
          :reason-code="message.reasonCode"
          :error="message.error"
          :created-at="message.createdAt"
        />
      </div>
    </main>

    <ChatComposer
      v-if="selectedMode"
      v-model="draft"
      :mode="selectedMode"
      :pending="pending"
      @send="submit"
      @stop="stop"
    />
  </div>
</template>
