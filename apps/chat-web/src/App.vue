<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'

import { ChatApiError, streamChat } from './api'
import type { ChatEvent, Citation } from './api'
import ChatComposer from './components/ChatComposer.vue'
import ChatMessage from './components/ChatMessage.vue'

type MessageState =
  | 'preparing'
  | 'streaming'
  | 'done'
  | 'refused'
  | 'error'
  | 'aborted'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  state: MessageState
  citations: Citation[]
  requestId?: string
  error?: string
  createdAt: number
}

const suggestions = [
  '快递业务经营许可需要符合哪些条件？',
  '邮政行政处罚程序有哪些主要规定？',
  '快递服务国家标准对投递有哪些要求？',
]

const messages = ref<Message[]>([])
const draft = ref('')
const pending = ref(false)
const messageList = ref<HTMLElement | null>(null)
let activeController: AbortController | null = null
let scrollFrame: number | null = null

function newId(): string {
  return crypto.randomUUID()
}

function queueScroll(): void {
  if (scrollFrame !== null) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    const element = messageList.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function isRefusal(reason: string): boolean {
  return ['no_context', 'reranker_rejected', 'llm_rejected'].includes(reason)
}

async function submit(): Promise<void> {
  const question = draft.value.trim()
  if (!question || pending.value) return

  const now = Date.now()
  const assistantIndex = messages.value.length + 1
  messages.value.push({
    id: newId(),
    role: 'user',
    content: question,
    state: 'done',
    citations: [],
    createdAt: now,
  })
  messages.value.push({
    id: newId(),
    role: 'assistant',
    content: '',
    state: 'preparing',
    citations: [],
    createdAt: Date.now(),
  })

  draft.value = ''
  pending.value = true
  activeController = new AbortController()
  const requestId = `web-${newId()}`
  await nextTick()
  queueScroll()

  const handleEvent = (event: ChatEvent): void => {
    const assistant = messages.value[assistantIndex]
    if (!assistant) return

    if (event.type === 'metadata') {
      assistant.requestId = event.requestId || requestId
      assistant.citations = event.citations
      assistant.state = 'streaming'
    } else if (event.type === 'delta') {
      assistant.content += event.content
      assistant.state = 'streaming'
    } else if (event.type === 'done') {
      assistant.requestId = event.requestId || assistant.requestId
      assistant.state = isRefusal(event.finishReason) ? 'refused' : 'done'
    } else if (event.type === 'error') {
      assistant.requestId = event.requestId || assistant.requestId
      assistant.error = event.message
      assistant.state = 'error'
    }
    queueScroll()
  }

  try {
    await streamChat({
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
            : '连接问答服务失败，请稍后重试。'
      }
    }
  } finally {
    pending.value = false
    activeController = null
    queueScroll()
  }
}

function askSuggestion(question: string): void {
  if (pending.value) return
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
          <h1>邮政政策知识助手</h1>
          <p>国家邮政局政策法规标准知识库</p>
        </div>
      </div>
      <button
        v-if="messages.length"
        type="button"
        class="text-button"
        :disabled="pending"
        @click="clearMessages"
      >
        清空对话
      </button>
    </header>

    <main ref="messageList" class="message-list" aria-live="polite">
      <section v-if="!messages.length" class="welcome">
        <div class="welcome__icon" aria-hidden="true">知</div>
        <p class="welcome__eyebrow">政策法规标准知识库</p>
        <h2>你好，我是邮政政策知识助手</h2>
        <p class="welcome__description">
          我会检索国家邮政局公开政策资料，并在回答中列出可追溯的原文来源。
        </p>
        <div class="suggestions">
          <button
            v-for="suggestion in suggestions"
            :key="suggestion"
            type="button"
            :disabled="pending"
            @click="askSuggestion(suggestion)"
          >
            {{ suggestion }}
          </button>
        </div>
      </section>

      <div v-else class="messages">
        <ChatMessage
          v-for="message in messages"
          :key="message.id"
          :role="message.role"
          :content="message.content"
          :state="message.state"
          :citations="message.citations"
          :request-id="message.requestId"
          :error="message.error"
          :created-at="message.createdAt"
        />
      </div>
    </main>

    <ChatComposer
      v-model="draft"
      :pending="pending"
      @send="submit"
      @stop="stop"
    />
  </div>
</template>
