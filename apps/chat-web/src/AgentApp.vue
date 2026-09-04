<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  AgentApiError,
  deleteAgentConversation,
  getAgentCapabilities,
  streamAgentMessage,
} from './agent-api'
import type {
  AgentCapability,
  AgentMessageRequest,
  AgentResponse,
  AgentStreamEvent,
  PublicIntent,
  RequiredInput,
} from './agent-api'
import { clearAgentSession, loadAgentSession, saveAgentSession } from './agent-session'
import type {
  AgentMessageState,
  AgentUiMessage,
  PendingAgentRequest,
} from './agent-ui-model'
import AgentComposer from './components/AgentComposer.vue'
import AgentMessage from './components/AgentMessage.vue'
import AgentSlotForm from './components/AgentSlotForm.vue'
import { createId } from './id'


interface CapabilityPresentation {
  icon: string
  description: string
  example: string
}


const intentOrder: PublicIntent[] = [
  'tracking',
  'delivery_time',
  'postage',
  'policy',
  'device_price',
]
const presentation: Record<PublicIntent, CapabilityPresentation> = {
  tracking: {
    icon: '轨',
    description: '识别或补充 13 位邮件号，查询最新状态与轨迹节点。',
    example: '帮我查一下邮件 1234567890123',
  },
  delivery_time: {
    icon: '时',
    description: '采集寄件地与收件地，查询预计寄递时长。',
    example: '北京寄到上海一般需要多久？',
  },
  postage: {
    icon: '费',
    description: '结合寄收地区与重量，执行类型化邮费试算。',
    example: '北京寄到上海 2 公斤要多少钱？',
  },
  policy: {
    icon: '政',
    description: '复用现有 RAG 能力，查询政策、材料与办理流程。',
    example: '快件丢失理赔通常需要哪些材料？',
  },
  device_price: {
    icon: '价',
    description: '复用设备价格查询，核对型号、规格与参考价格。',
    example: '查询 iPhone 16 Pro 256GB 的参考价格',
  },
}
const fallbackNames: Record<PublicIntent, string> = {
  policy: '政策查询',
  device_price: '设备价格',
  tracking: '邮件轨迹',
  delivery_time: '寄递时限',
  postage: '邮费试算',
}

const restored = loadAgentSession()
const messages = ref<AgentUiMessage[]>(restored?.messages ?? [])
const conversationId = ref<string | null>(restored?.conversationId ?? null)
const pendingRequest = ref<PendingAgentRequest | null>(
  restored?.pendingRequest ?? null,
)
const selectedIntent = ref<PublicIntent | null>(restored?.selectedIntent ?? null)
const capabilities = ref<AgentCapability[]>([])
const draft = ref('')
const pending = ref(false)
const clearing = ref(false)
const capabilityLoading = ref(true)
const banner = ref(
  restored?.conversationId ? '已从本机恢复可继续的 Agent 会话。' : '',
)
const messageList = ref<HTMLElement | null>(null)
let activeController: AbortController | null = null
let capabilityController: AbortController | null = null
let scrollFrame: number | null = null

if (pendingRequest.value) {
  const interrupted = messages.value.find(
    (item) => item.id === pendingRequest.value?.assistantMessageId,
  )
  if (interrupted) {
    interrupted.state = 'error'
    interrupted.error = '页面上次在响应完成前关闭，可使用原幂等键安全重试。'
  }
}

const capabilityMap = computed(
  () => new Map(capabilities.value.map((item) => [item.intent, item])),
)
const selectedCapability = computed(() =>
  selectedIntent.value
    ? capabilityMap.value.get(selectedIntent.value) ?? null
    : null,
)
const lastAssistant = computed(() =>
  [...messages.value].reverse().find((item) => item.role === 'assistant'),
)
const activeInputs = computed<RequiredInput[]>(() =>
  lastAssistant.value?.state === 'waiting_user'
    ? lastAssistant.value.requiredInputs
    : [],
)
const waitingForInput = computed(() => activeInputs.value.length > 0)
const shortConversationId = computed(() =>
  conversationId.value ? conversationId.value.slice(0, 8) : '',
)


function queueScroll(): void {
  if (scrollFrame !== null) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    const element = messageList.value
    if (element) element.scrollTop = element.scrollHeight
  })
}


function persist(): void {
  saveAgentSession({
    version: 1,
    conversationId: conversationId.value,
    messages: messages.value,
    pendingRequest: pendingRequest.value,
    selectedIntent: selectedIntent.value,
    updatedAt: Date.now(),
  })
}


function responseState(response: AgentResponse): AgentMessageState {
  if (response.phase === 'waiting_user') return 'waiting_user'
  if (response.phase === 'handoff') return 'handoff'
  if (response.phase === 'failed') return 'failed'
  if (response.result?.status === 'partial') return 'partial'
  if (response.result?.status === 'failed') return 'failed'
  return 'done'
}


function applyResponse(
  assistant: AgentUiMessage,
  response: AgentResponse,
): void {
  conversationId.value = response.conversation_id
  assistant.content = response.reply
  assistant.state = responseState(response)
  assistant.intent =
    response.intent && response.intent !== 'unknown' ? response.intent : null
  assistant.nextAction = response.next_action
  assistant.requiredInputs = response.required_inputs
  assistant.result = response.result
  assistant.failure = response.failure
  assistant.warnings = response.warnings
  assistant.requestId = response.request_id
  assistant.error = undefined
}


function applyEvent(
  assistant: AgentUiMessage,
  event: AgentStreamEvent,
): void {
  if (event.type === 'status') {
    assistant.statusText = event.data.message
    assistant.state = 'preparing'
  } else if (event.type === 'state') {
    conversationId.value = event.data.conversation_id
    assistant.requestId = event.data.request_id
    assistant.intent =
      event.data.intent && event.data.intent !== 'unknown'
        ? event.data.intent
        : null
    assistant.nextAction = event.data.next_action
    assistant.state = 'streaming'
  } else if (event.type === 'input_required') {
    conversationId.value = event.data.conversation_id
    assistant.requiredInputs = event.data.required_inputs
  } else if (event.type === 'result') {
    assistant.result = event.data.result
    assistant.failure = event.data.failure
    assistant.warnings = event.data.warnings
  } else if (event.type === 'delta') {
    assistant.content += event.data.content
    assistant.state = 'streaming'
  } else if (event.type === 'done') {
    applyResponse(assistant, event.data.response)
    pendingRequest.value = null
  } else {
    assistant.state = 'error'
    assistant.error = event.data.message
    assistant.requestId = event.data.request_id
    assistant.failure = {
      category: event.data.category ?? 'internal_error',
      code: event.data.code,
      retryable: event.data.retryable,
      retry_after_seconds: event.data.retry_after_seconds,
    }
    if (!event.data.retryable) pendingRequest.value = null
    if (
      ['conversation_expired', 'conversation_not_available'].includes(
        event.data.code,
      )
    ) {
      conversationId.value = null
      pendingRequest.value = null
      banner.value = '原会话已失效，请重新描述需求开始新会话。'
    }
  }
  persist()
  queueScroll()
}


async function executeRequest(
  request: PendingAgentRequest,
  { reset = false }: { reset?: boolean } = {},
): Promise<void> {
  const assistant = messages.value.find(
    (item) => item.id === request.assistantMessageId,
  )
  if (!assistant || pending.value) return

  if (reset) {
    assistant.content = ''
    assistant.error = undefined
    assistant.result = null
    assistant.failure = null
    assistant.warnings = []
    assistant.requiredInputs = []
  }
  assistant.state = 'preparing'
  assistant.statusText = reset
    ? '正在使用原幂等键恢复请求'
    : '正在建立 Agent 事件流'
  pendingRequest.value = request
  pending.value = true
  activeController = new AbortController()
  persist()
  queueScroll()

  try {
    await streamAgentMessage({
      payload: request.payload,
      idempotencyKey: request.idempotencyKey,
      requestId: request.requestId,
      signal: activeController.signal,
      onEvent: (event) => applyEvent(assistant, event),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      assistant.state = 'aborted'
      assistant.error = undefined
    } else {
      assistant.state = 'error'
      assistant.error =
        error instanceof AgentApiError
          ? error.message
          : '连接 Agent 服务失败，可安全重试本轮请求。'
      if (error instanceof AgentApiError) {
        assistant.requestId = error.requestId ?? assistant.requestId
        if (!error.retryable) pendingRequest.value = null
        if (
          ['conversation_expired', 'conversation_not_available'].includes(
            error.code,
          )
        ) {
          conversationId.value = null
          pendingRequest.value = null
        }
      }
    }
  } finally {
    pending.value = false
    activeController = null
    persist()
    queueScroll()
  }
}


async function submitRequest(
  payload: Omit<AgentMessageRequest, 'stream'>,
  userText: string,
): Promise<void> {
  if (pending.value) return
  if (lastAssistant.value?.state === 'waiting_user') {
    lastAssistant.value.state = 'done'
    lastAssistant.value.requiredInputs = []
  }
  const assistantId = createId()
  const now = Date.now()
  messages.value.push(
    {
      id: createId(),
      role: 'user',
      content: userText,
      state: 'done',
      createdAt: now,
      intent: null,
      requiredInputs: [],
      warnings: [],
    },
    {
      id: assistantId,
      role: 'assistant',
      content: '',
      state: 'preparing',
      createdAt: now + 1,
      intent: selectedIntent.value,
      requiredInputs: [],
      warnings: [],
    },
  )
  const request: PendingAgentRequest = {
    payload: {
      ...payload,
      conversation_id: conversationId.value ?? undefined,
    },
    idempotencyKey: `web-message-${createId()}`,
    requestId: `web-agent-${createId()}`,
    assistantMessageId: assistantId,
  }
  await nextTick()
  await executeRequest(request)
}


function submitText(): void {
  const message = draft.value.trim()
  if (!message || pending.value) return
  draft.value = ''
  const explicitIntent = waitingForInput.value
    ? undefined
    : selectedIntent.value ?? undefined
  void submitRequest(
    { message, explicit_intent: explicitIntent },
    message,
  )
}


function submitStructured(payload: {
  message: string
  confirmOverwrite: boolean
}): void {
  void submitRequest(
    {
      message: payload.message,
      confirm_overwrite: payload.confirmOverwrite,
    },
    payload.message,
  )
}


function selectRequiredIntent(intent: PublicIntent): void {
  selectedIntent.value = intent
  void submitRequest(
    { explicit_intent: intent },
    `选择查询类型：${fallbackNames[intent]}`,
  )
}


function selectCapability(intent: PublicIntent): void {
  if (pending.value || waitingForInput.value) return
  const capability = capabilityMap.value.get(intent)
  if (!capability?.available) return
  selectedIntent.value = selectedIntent.value === intent ? null : intent
  draft.value = ''
}


function useExample(intent: PublicIntent): void {
  const capability = capabilityMap.value.get(intent)
  if (!capability?.available || pending.value) return
  selectedIntent.value = intent
  draft.value = presentation[intent].example
  submitText()
}


function stop(): void {
  activeController?.abort()
}


function retryLast(): void {
  if (pendingRequest.value && !pending.value) {
    void executeRequest(pendingRequest.value, { reset: true })
  }
}


function discardSession(): void {
  conversationId.value = null
  pendingRequest.value = null
  messages.value = []
  selectedIntent.value = null
  draft.value = ''
  banner.value = ''
  clearAgentSession()
}


async function startOver(): Promise<void> {
  if (pending.value || clearing.value) return
  const id = conversationId.value
  if (!id) {
    discardSession()
    return
  }
  clearing.value = true
  try {
    await deleteAgentConversation(id)
    discardSession()
  } catch (error) {
    if (error instanceof AgentApiError && error.status === 404) {
      discardSession()
    } else {
      banner.value =
        error instanceof AgentApiError
          ? `暂时无法清理服务端会话：${error.message}`
          : '暂时无法清理服务端会话，请稍后重试。'
    }
  } finally {
    clearing.value = false
  }
}


onMounted(async () => {
  capabilityController = new AbortController()
  try {
    capabilities.value = await getAgentCapabilities(capabilityController.signal)
  } catch (error) {
    banner.value =
      error instanceof AgentApiError
        ? `能力目录加载失败：${error.message}`
        : '能力目录加载失败，请确认 V2 Agent 服务已启用。'
  } finally {
    capabilityLoading.value = false
  }
})


onBeforeUnmount(() => {
  activeController?.abort()
  capabilityController?.abort()
  if (scrollFrame !== null) cancelAnimationFrame(scrollFrame)
})
</script>

<template>
  <div class="app-shell agent-app">
    <header class="app-header">
      <div class="brand">
        <span class="brand__mark" aria-hidden="true">邮</span>
        <div>
          <h1>中国邮政理赔 Agent <small>LangGraph</small></h1>
          <p>意图理解 · 持久化工作流 · 类型化工具</p>
        </div>
      </div>
      <div class="header-actions">
        <span v-if="conversationId" class="agent-session-chip">
          会话 {{ shortConversationId }}
        </span>
        <button
          v-if="messages.length"
          type="button"
          class="text-button"
          :disabled="pending || clearing"
          @click="startOver"
        >
          {{ clearing ? '清理中…' : '重新开始' }}
        </button>
      </div>
    </header>

    <main ref="messageList" class="message-list" aria-live="polite">
      <div v-if="banner" class="agent-banner" role="status">
        <span>{{ banner }}</span>
        <button type="button" aria-label="关闭提示" @click="banner = ''">×</button>
      </div>

      <section v-if="!messages.length" class="agent-welcome">
        <p class="welcome__eyebrow">STATEFUL CLAIMS WORKFLOW</p>
        <h2>描述需求，或选择一个 Agent 能力</h2>
        <p class="welcome__description">
          可自由输入让 Query Understanding 自动路由，也可显式选择能力；缺少字段时工作流会暂停补槽。
        </p>

        <div class="agent-capability-grid" :aria-busy="capabilityLoading">
          <button
            v-for="intent in intentOrder"
            :key="intent"
            type="button"
            class="agent-capability-card"
            :class="{
              'agent-capability-card--selected': selectedIntent === intent,
              'agent-capability-card--unavailable': !capabilityMap.get(intent)?.available,
            }"
            :disabled="capabilityLoading || !capabilityMap.get(intent)?.available"
            @click="selectCapability(intent)"
          >
            <span class="agent-capability-card__icon" aria-hidden="true">
              {{ presentation[intent].icon }}
            </span>
            <span>
              <strong>{{ capabilityMap.get(intent)?.display_name || fallbackNames[intent] }}</strong>
              <small>{{ presentation[intent].description }}</small>
              <em v-if="capabilityLoading">读取能力目录…</em>
              <em v-else-if="!capabilityMap.get(intent)?.available">当前未装配</em>
              <em v-else>{{ selectedIntent === intent ? '已作为显式意图' : '选择此能力' }}</em>
            </span>
          </button>
        </div>

        <div class="agent-examples">
          <span>快速体验</span>
          <button
            v-for="intent in intentOrder.filter((item) => capabilityMap.get(item)?.available)"
            :key="intent"
            type="button"
            @click="useExample(intent)"
          >
            {{ presentation[intent].example }}
          </button>
        </div>
      </section>

      <div v-else class="messages agent-messages">
        <AgentMessage
          v-for="message in messages"
          :key="message.id"
          :message="message"
        />
      </div>
    </main>

    <footer class="agent-action-area">
      <div v-if="pendingRequest && !pending" class="agent-retry-bar">
        <span>本轮请求尚未收到终止事件，可复用原幂等键继续。</span>
        <button type="button" @click="retryLast">安全重试</button>
      </div>
      <nav
        v-if="messages.length && !waitingForInput"
        class="agent-capability-shortcuts"
        aria-label="Agent 能力快捷选择"
      >
        <button
          type="button"
          :class="{ 'is-selected': selectedIntent === null }"
          :disabled="pending"
          @click="selectedIntent = null"
        >
          自动识别
        </button>
        <button
          v-for="intent in intentOrder"
          :key="intent"
          type="button"
          :class="{ 'is-selected': selectedIntent === intent }"
          :disabled="pending || !capabilityMap.get(intent)?.available"
          @click="selectCapability(intent)"
        >
          {{ fallbackNames[intent] }}
        </button>
      </nav>
      <AgentSlotForm
        v-if="activeInputs.length && !pendingRequest"
        :inputs="activeInputs"
        :pending="pending"
        @submit="submitStructured"
        @select-intent="selectRequiredIntent"
      />
      <AgentComposer
        v-model="draft"
        :pending="pending"
        :selected-label="selectedCapability?.display_name"
        :waiting-for-input="waitingForInput"
        @send="submitText"
        @stop="stop"
      />
    </footer>
  </div>
</template>
