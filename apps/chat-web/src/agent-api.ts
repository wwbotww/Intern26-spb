import { parseSseBlock } from './api'
import type {
  AgentCapability,
  AgentFailure,
  AgentMessageRequest,
  AgentPhase,
  AgentResponse,
  AgentResult,
  AgentStreamDeltaEvent,
  AgentStreamDoneEvent,
  AgentStreamErrorEvent,
  AgentStreamInputRequiredEvent,
  AgentStreamResultEvent,
  AgentStreamStateEvent,
  AgentStreamStatusEvent,
  Intent,
  PublicIntent,
  RequiredInput,
} from './generated/agent-api'

export type {
  AgentCapability,
  AgentMessageRequest,
  AgentResponse,
  AgentResult,
  PublicIntent,
  RequiredInput,
} from './generated/agent-api'

export type AgentStreamEvent =
  | { type: 'status'; data: AgentStreamStatusEvent }
  | { type: 'state'; data: AgentStreamStateEvent }
  | { type: 'input_required'; data: AgentStreamInputRequiredEvent }
  | { type: 'result'; data: AgentStreamResultEvent }
  | { type: 'delta'; data: AgentStreamDeltaEvent }
  | { type: 'done'; data: AgentStreamDoneEvent }
  | { type: 'error'; data: AgentStreamErrorEvent }

export class AgentApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
    public readonly retryable = false,
    public readonly requestId?: string,
  ) {
    super(message)
    this.name = 'AgentApiError'
  }
}

const intents = new Set<Intent>([
  'policy',
  'device_price',
  'tracking',
  'delivery_time',
  'postage',
  'unknown',
])
const publicIntents = new Set<PublicIntent>([
  'policy',
  'device_price',
  'tracking',
  'delivery_time',
  'postage',
])
const phases = new Set<AgentPhase>([
  'new',
  'understanding',
  'clarifying',
  'collecting',
  'ready',
  'executing',
  'validating',
  'recovering',
  'responding',
  'waiting_user',
  'completed',
  'handoff',
  'failed',
])
const nextActions = new Set<AgentResponse['next_action']>([
  'collect_slots',
  'clarify_intent',
  'complete',
  'handoff',
  'failed',
])
const resultStatuses = new Set<AgentResult['status']>([
  'success',
  'partial',
  'need_more_info',
  'no_match',
  'failed',
])
const inputTypes = new Set<RequiredInput['type']>([
  'string',
  'number',
  'region',
  'choice',
])
const knownEvents = new Set([
  'status',
  'state',
  'input_required',
  'result',
  'delta',
  'done',
  'error',
])

function invalidContract(message: string): never {
  throw new AgentApiError('invalid_agent_event', message)
}

function record(value: unknown, context: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return invalidContract(`${context} 必须是对象。`)
  }
  return value as Record<string, unknown>
}

function stringValue(value: unknown, context: string): string {
  if (typeof value !== 'string') {
    return invalidContract(`${context} 必须是字符串。`)
  }
  return value
}

function booleanValue(value: unknown, context: string): boolean {
  if (typeof value !== 'boolean') {
    return invalidContract(`${context} 必须是布尔值。`)
  }
  return value
}

function numberValue(value: unknown, context: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return invalidContract(`${context} 必须是有限数字。`)
  }
  return value
}

function nullableString(value: unknown, context: string): string | null {
  return value === null || value === undefined
    ? null
    : stringValue(value, context)
}

function stringList(value: unknown, context: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    return invalidContract(`${context} 必须是字符串数组。`)
  }
  return [...value]
}

function intentValue(value: unknown, context: string): Intent {
  const candidate = stringValue(value, context) as Intent
  if (!intents.has(candidate)) return invalidContract(`${context} 不受支持。`)
  return candidate
}

function publicIntentValue(value: unknown, context: string): PublicIntent {
  const candidate = stringValue(value, context) as PublicIntent
  if (!publicIntents.has(candidate)) {
    return invalidContract(`${context} 不受支持。`)
  }
  return candidate
}

function phaseValue(value: unknown): AgentPhase {
  const candidate = stringValue(value, 'phase') as AgentPhase
  if (!phases.has(candidate)) return invalidContract('phase 不受支持。')
  return candidate
}

function nextActionValue(value: unknown): AgentResponse['next_action'] {
  const candidate = stringValue(value, 'next_action') as AgentResponse['next_action']
  if (!nextActions.has(candidate)) {
    return invalidContract('next_action 不受支持。')
  }
  return candidate
}

function requiredInput(value: unknown): RequiredInput {
  const item = record(value, 'required_input')
  const type = stringValue(item.type, 'required_input.type') as RequiredInput['type']
  if (!inputTypes.has(type)) {
    return invalidContract('required_input.type 不受支持。')
  }
  return {
    name: stringValue(item.name, 'required_input.name'),
    label: stringValue(item.label, 'required_input.label'),
    type,
    validation_hint:
      item.validation_hint === undefined
        ? ''
        : stringValue(item.validation_hint, 'required_input.validation_hint'),
    choices:
      item.choices === undefined
        ? []
        : stringList(item.choices, 'required_input.choices'),
  }
}

function requiredInputs(value: unknown): RequiredInput[] {
  if (!Array.isArray(value)) {
    return invalidContract('required_inputs 必须是数组。')
  }
  return value.map(requiredInput)
}

function agentFailure(value: unknown): AgentFailure | null {
  if (value === null || value === undefined) return null
  const item = record(value, 'failure')
  const retryAfter = item.retry_after_seconds
  return {
    category: stringValue(item.category, 'failure.category'),
    code: stringValue(item.code, 'failure.code'),
    retryable: booleanValue(item.retryable, 'failure.retryable'),
    retry_after_seconds:
      retryAfter === null || retryAfter === undefined
        ? null
        : numberValue(retryAfter, 'failure.retry_after_seconds'),
  }
}

function agentResult(value: unknown): AgentResult | null {
  if (value === null || value === undefined) return null
  const item = record(value, 'result')
  const status = stringValue(item.status, 'result.status') as AgentResult['status']
  if (!resultStatuses.has(status)) {
    return invalidContract('result.status 不受支持。')
  }
  const data = item.data
  if (data !== null && data !== undefined) record(data, 'result.data')
  return {
    type: publicIntentValue(item.type, 'result.type'),
    status,
    data: data === undefined ? null : (data as Record<string, unknown> | null),
    reason_code:
      item.reason_code === undefined
        ? ''
        : stringValue(item.reason_code, 'result.reason_code'),
  }
}

export function validateAgentResponse(value: unknown): AgentResponse {
  const item = record(value, 'response')
  const rawIntent = nullableString(item.intent, 'intent')
  const result = agentResult(item.result)
  const response: AgentResponse = {
    request_id: stringValue(item.request_id, 'request_id'),
    conversation_id: stringValue(item.conversation_id, 'conversation_id'),
    turn_id: stringValue(item.turn_id, 'turn_id'),
    phase: phaseValue(item.phase),
    intent: rawIntent === null ? null : intentValue(rawIntent, 'intent'),
    reply: stringValue(item.reply, 'reply'),
    next_action: nextActionValue(item.next_action),
    required_inputs: requiredInputs(item.required_inputs),
    result,
    failure: agentFailure(item.failure),
    warnings: stringList(item.warnings, 'warnings'),
  }
  if (result && response.intent !== result.type) {
    return invalidContract('result.type 与 intent 不一致。')
  }
  if (response.phase === 'waiting_user' && !response.required_inputs.length) {
    return invalidContract('waiting_user 缺少 required_inputs。')
  }
  return response
}

function eventBase(value: unknown): Record<string, unknown> {
  const item = record(value, 'event.data')
  if (item.schema_version !== '1') {
    throw new AgentApiError(
      'unsupported_agent_event_version',
      '服务返回了当前页面不支持的 Agent 事件版本。',
    )
  }
  return item
}

export function validateAgentStreamEvent(
  eventName: string,
  value: unknown,
): AgentStreamEvent | null {
  if (!knownEvents.has(eventName)) return null
  const item = eventBase(value)

  if (eventName === 'done') {
    return {
      type: 'done',
      data: {
        schema_version: '1',
        response: validateAgentResponse(item.response),
      },
    }
  }

  const requestId = stringValue(item.request_id, 'request_id')

  if (eventName === 'status') {
    if (item.stage !== 'accepted') return invalidContract('status.stage 不受支持。')
    return {
      type: 'status',
      data: {
        schema_version: '1',
        request_id: requestId,
        stage: 'accepted',
        message: stringValue(item.message, 'status.message'),
      },
    }
  }

  if (eventName === 'error') {
    const retryAfter = item.retry_after_seconds
    return {
      type: 'error',
      data: {
        schema_version: '1',
        request_id: requestId,
        code: stringValue(item.code, 'error.code'),
        message: stringValue(item.message, 'error.message'),
        http_status: numberValue(item.http_status, 'error.http_status'),
        category: nullableString(item.category, 'error.category'),
        retryable: booleanValue(item.retryable, 'error.retryable'),
        retry_after_seconds:
          retryAfter === null || retryAfter === undefined
            ? null
            : numberValue(retryAfter, 'error.retry_after_seconds'),
      },
    }
  }

  const common = {
    schema_version: '1' as const,
    request_id: requestId,
    conversation_id: stringValue(item.conversation_id, 'conversation_id'),
    turn_id: stringValue(item.turn_id, 'turn_id'),
  }
  if (eventName === 'state') {
    const rawIntent = nullableString(item.intent, 'state.intent')
    return {
      type: 'state',
      data: {
        ...common,
        phase: phaseValue(item.phase),
        intent: rawIntent === null ? null : intentValue(rawIntent, 'state.intent'),
        next_action: nextActionValue(item.next_action),
      },
    }
  }
  if (eventName === 'input_required') {
    return {
      type: 'input_required',
      data: {
        ...common,
        required_inputs: requiredInputs(item.required_inputs),
      },
    }
  }
  if (eventName === 'result') {
    return {
      type: 'result',
      data: {
        ...common,
        result: agentResult(item.result),
        failure: agentFailure(item.failure),
        warnings: stringList(item.warnings, 'result.warnings'),
      },
    }
  }
  return {
    type: 'delta',
    data: {
      ...common,
      content: stringValue(item.content, 'delta.content'),
    },
  }
}

async function responseError(response: Response): Promise<AgentApiError> {
  let code = `http_${response.status}`
  let message = `请求失败（HTTP ${response.status}）。`
  let retryable = response.status >= 500
  let requestId: string | undefined
  try {
    const payload = record(await response.json(), 'error response')
    const detail = record(payload.detail, 'error detail')
    code = stringValue(detail.code, 'error.code')
    message = stringValue(detail.message, 'error.message')
    retryable =
      detail.retryable === undefined
        ? retryable
        : booleanValue(detail.retryable, 'error.retryable')
    requestId = detail.request_id
      ? stringValue(detail.request_id, 'error.request_id')
      : undefined
  } catch {
    // Fall back to the HTTP status when an error body is absent or non-standard.
  }
  return new AgentApiError(code, message, response.status, retryable, requestId)
}

function capability(value: unknown): AgentCapability {
  const item = record(value, 'capability')
  return {
    intent: publicIntentValue(item.intent, 'capability.intent'),
    display_name: stringValue(item.display_name, 'capability.display_name'),
    available: booleanValue(item.available, 'capability.available'),
    capability_version: nullableString(
      item.capability_version,
      'capability.capability_version',
    ),
    required_inputs: requiredInputs(item.required_inputs),
  }
}

export async function getAgentCapabilities(
  signal?: AbortSignal,
): Promise<AgentCapability[]> {
  const response = await fetch('/api/v2/agent/capabilities', {
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) throw await responseError(response)
  const payload: unknown = await response.json()
  if (!Array.isArray(payload)) return invalidContract('capabilities 必须是数组。')
  return payload.map(capability)
}

export async function deleteAgentConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `/api/v2/agent/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE', signal },
  )
  if (!response.ok) throw await responseError(response)
}

export async function streamAgentMessage(options: {
  payload: Omit<AgentMessageRequest, 'stream'>
  idempotencyKey: string
  requestId: string
  signal: AbortSignal
  onEvent: (event: AgentStreamEvent) => void
}): Promise<void> {
  const response = await fetch('/api/v2/agent/messages', {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      'Idempotency-Key': options.idempotencyKey,
      'X-Request-ID': options.requestId,
    },
    body: JSON.stringify({ ...options.payload, stream: true }),
    signal: options.signal,
  })
  if (!response.ok) throw await responseError(response)
  if (!response.body) {
    throw new AgentApiError('empty_stream', '服务没有返回可读取的响应流。', 502, true)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false

  const dispatch = (block: string): void => {
    let raw
    try {
      raw = parseSseBlock(block)
    } catch {
      throw new AgentApiError(
        'invalid_sse',
        '服务返回了无法解析的 Agent 流式数据。',
      )
    }
    if (!raw) return
    const event = validateAgentStreamEvent(raw.event, raw.data)
    if (!event) return
    options.onEvent(event)
    terminal = event.type === 'done' || event.type === 'error'
  }

  while (!terminal) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary = buffer.match(/\r?\n\r?\n/)
    while (boundary?.index !== undefined) {
      const block = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      dispatch(block)
      if (terminal) break
      boundary = buffer.match(/\r?\n\r?\n/)
    }
    if (done) break
  }

  if (terminal) {
    await reader.cancel()
    return
  }
  if (buffer.trim()) dispatch(buffer)
  if (!terminal) {
    throw new AgentApiError(
      'stream_interrupted',
      'Agent 响应流意外中断，可使用同一幂等键安全重试。',
      undefined,
      true,
      options.requestId,
    )
  }
}
