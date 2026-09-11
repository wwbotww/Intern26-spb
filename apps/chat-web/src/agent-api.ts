import { parseSseBlock } from './api'
import { validatePriceCandidates, validateProductPrice } from './product-price-contract'
import type {
  BrowserSessionResponse,
  AgentCapability,
  AgentFailure,
  AgentMessageRequest,
  AgentPhase,
  AgentResponse,
  AgentResult,
  AgentSourceResponse,
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
  AgentSourceResponse,
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

let browserIdentity: BrowserSessionResponse | null = null

export function browserSessionEnabled(): boolean {
  return import.meta.env.VITE_AGENT_BROWSER_SESSION === 'true'
}

export function clearAgentBrowserIdentity(): void {
  browserIdentity = null
}

export async function bootstrapAgentBrowserSession(
  reset = false, signal?: AbortSignal,
): Promise<BrowserSessionResponse> {
  clearAgentBrowserIdentity()
  const response = await fetch('/api/v2/agent/browser-session', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ reset }), signal,
  })
  if (!response.ok) throw await responseError(response)
  const value = record(await response.json(), 'browser session')
  const reference = stringValue(value.session_ref, 'session_ref')
  const expires = stringValue(value.expires_at, 'expires_at')
  const expiry = Date.parse(expires)
  if (!/^[a-f0-9]{64}$/.test(reference) || !/(?:Z|[+-]\d{2}:\d{2})$/.test(expires)
    || !Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.now() + 86_430_000) {
    throw new AgentApiError('browser_session_invalid', '访客会话响应无效，请重新核验身份。')
  }
  browserIdentity = { session_ref: reference, expires_at: expires }
  return browserIdentity
}

function browserSessionHeaders(): Record<string, string> {
  if (!browserSessionEnabled()) return { 'X-Agent-Contract': 'product-price-v1' }
  if (!browserIdentity || Date.parse(browserIdentity.expires_at) <= Date.now()) {
    clearAgentBrowserIdentity()
    throw new AgentApiError('browser_session_required', '请先核验浏览器访客身份。', 401)
  }
  return { 'X-Agent-Session': browserIdentity.session_ref, 'X-Agent-Contract': 'product-price-v1' }
}

const intents = new Set<Intent>([
  'product_price',
  'policy',
  'device_price',
  'tracking',
  'delivery_time',
  'postage',
  'unknown',
])
const publicIntents = new Set<PublicIntent>([
  'product_price',
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

export function isPublicIntent(value: unknown): value is PublicIntent {
  return typeof value === 'string' && publicIntents.has(value as PublicIntent)
}

function publicIntentValue(value: unknown, context: string): PublicIntent {
  const candidate = stringValue(value, context)
  if (!isPublicIntent(candidate)) {
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
  let candidates
  try {
    candidates = validatePriceCandidates(item.price_candidates)
    if (candidates.length && (item.name !== 'price_selection' || type !== 'choice')) throw new Error()
  } catch { return invalidContract('价格候选未通过契约校验。') }
  return {
    name: stringValue(item.name, 'required_input.name'),
    label: stringValue(item.label, 'required_input.label'),
    type,
    ...(item.price_candidates === undefined ? {} : { price_candidates: candidates }),
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

export function requiredInputs(value: unknown): RequiredInput[] {
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

function postageQuoteBasis(value: unknown): NonNullable<AgentResult['quote_basis']> | null {
  if (value === undefined || value === null) return null
  const basis = record(value, 'quote_basis')
  const amountKind = stringValue(basis.amount_kind, 'quote_basis.amount_kind')
  const currency = stringValue(basis.currency, 'quote_basis.currency')
  const product = stringValue(basis.product_code, 'quote_basis.product_code')
  if ((basis.schema_version === undefined ? '1' : basis.schema_version) !== '1' || basis.is_estimate !== true
    || basis.scope !== 'domestic_actual_weight_no_extras'
    || !['total', 'standard', 'customer'].includes(amountKind)
    || !/^[A-Z]{3}$/.test(currency) || !/^[A-Za-z0-9_.-]{1,128}$/.test(product)) {
    return invalidContract('报价依据不符合公开契约。')
  }
  const source = record(basis.source, 'quote_basis.source')
  const sourceType = stringValue(source.source_type, 'quote.source_type')
  const name = stringValue(source.source_name, 'quote.source_name')
  const profile = stringValue(source.source_profile, 'quote.source_profile')
  const queriedAt = stringValue(source.queried_at, 'quote.queried_at')
  if (!['fake_gateway', 'external_api'].includes(sourceType)
    || !/^[A-Za-z0-9_.-]{1,128}$/.test(name) || !/^[A-Za-z0-9_.-]{1,128}$/.test(profile)
    || !/(?:Z|[+-]\d{2}:\d{2})$/.test(queriedAt) || !Number.isFinite(Date.parse(queriedAt))) {
    return invalidContract('报价来源不符合公开契约。')
  }
  const rawFees = basis.fees === undefined ? [] : basis.fees
  if (!Array.isArray(rawFees)) return invalidContract('报价费用项必须为数组。')
  type Fee = NonNullable<AgentResult['quote_basis']>['fees']
  const kinds = new Set<string>()
  const parsedFees = rawFees.map((raw) => {
    const fee = record(raw, 'quote.fee')
    const kind = stringValue(fee.kind, 'quote.fee.kind')
    const amount = stringValue(fee.amount, 'quote.fee.amount')
    const included = stringValue(fee.included_in_amount, 'quote.fee.included_in_amount')
    if (!['registration', 'insurance', 'declared_value', 'inspection', 'customs', 'fuel',
      'return_receipt', 'password_delivery', 'printing', 'handling'].includes(kind)
      || kinds.has(kind) || !/^(0|[1-9][0-9]{0,8})\.[0-9]{2}$/.test(amount)
      || !['yes', 'no', 'unknown'].includes(included)) return invalidContract('报价费用项不符合公开契约。')
    kinds.add(kind)
    return { kind, amount, included_in_amount: included } as NonNullable<Fee>[number]
  })
  return {
    schema_version: '1', amount_kind: amountKind as 'total' | 'standard' | 'customer',
    currency, product_code: product, scope: 'domestic_actual_weight_no_extras', is_estimate: true,
    fees: parsedFees, source: {
      source_type: sourceType as 'fake_gateway' | 'external_api', source_name: name,
      source_profile: profile, queried_at: queriedAt,
    },
  }
}

export function validateAgentResult(value: unknown): AgentResult | null {
  if (value === null || value === undefined) return null
  const item = record(value, 'result')
  const status = stringValue(item.status, 'result.status') as AgentResult['status']
  if (!resultStatuses.has(status)) {
    return invalidContract('result.status 不受支持。')
  }
  const data = item.data
  if (data !== null && data !== undefined) record(data, 'result.data')
  if (item.type === 'product_price') {
    try {
      if (status === 'success' || status === 'partial') validateProductPrice(data)
      else if (data != null) throw new Error()
    } catch { return invalidContract('商品价格结果未通过契约校验。') }
  }
  const basis = postageQuoteBasis(item.quote_basis)
  if (basis) {
    const quote = record(data, 'postage.data')
    if (item.type !== 'postage' || status !== 'success'
      || quote.currency !== basis.currency || quote.product_code !== basis.product_code
      || typeof quote.amount !== 'string' || !/^(0|[1-9][0-9]{0,8})\.[0-9]{2}$/.test(quote.amount)
      || typeof quote.queried_at !== 'string'
      || !/(?:Z|[+-]\d{2}:\d{2})$/.test(quote.queried_at)
      || Date.parse(quote.queried_at) !== Date.parse(basis.source.queried_at)) {
      return invalidContract('报价依据与结果不一致。')
    }
  }
  return {
    type: publicIntentValue(item.type, 'result.type'),
    status,
    quote_basis: basis,
    provenance: agentSources(item.provenance),
    data: data === undefined ? null : (data as Record<string, unknown> | null),
    reason_code:
      item.reason_code === undefined
        ? ''
        : stringValue(item.reason_code, 'result.reason_code'),
  }
}

function agentSources(value: unknown): AgentSourceResponse[] {
  // Historical responses/session snapshots predate the additive source field.
  if (value === undefined) return []
  if (!Array.isArray(value)) return invalidContract('result.provenance 必须是数组。')
  return value.map((raw) => {
    const source = record(raw, 'source')
    const sourceType = stringValue(source.source_type, 'source.source_type')
    if (!['fake_gateway', 'external_api', 'unknown'].includes(sourceType)) {
      return invalidContract('source.source_type 不受支持。')
    }
    const sourceName = stringValue(source.source_name, 'source.source_name')
    const profile = source.source_profile === undefined
      ? '' : stringValue(source.source_profile, 'source.source_profile')
    if (!/^[A-Za-z0-9_.-]{1,128}$/.test(sourceName) || !/^[A-Za-z0-9_.-]{0,128}$/.test(profile)) {
      return invalidContract('来源标识不符合契约。')
    }
    const queriedAt = nullableString(source.queried_at, 'source.queried_at')
    if (queriedAt !== null && (
      !/(?:Z|[+-]\d{2}:\d{2})$/.test(queriedAt) || !Number.isFinite(Date.parse(queriedAt))
    )) return invalidContract('来源查询时间必须包含时区。')
    const completeness = source.history_completeness === undefined
      ? 'unknown' : stringValue(source.history_completeness, 'source.history_completeness')
    if (!['complete', 'partial', 'unknown'].includes(completeness)) {
      return invalidContract('历史完整性不受支持。')
    }
    return {
      source_type: sourceType as AgentSourceResponse['source_type'],
      source_name: sourceName,
      source_profile: profile,
      queried_at: queriedAt,
      history_completeness: completeness as AgentSourceResponse['history_completeness'],
    }
  })
}

export function validateAgentResponse(value: unknown): AgentResponse {
  const item = record(value, 'response')
  const rawIntent = nullableString(item.intent, 'intent')
  const result = validateAgentResult(item.result)
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
        result: validateAgentResult(item.result),
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

export async function getAgentSnapshot(conversationId: string, signal?: AbortSignal): Promise<AgentResponse> {
  const response = await fetch(`/api/v2/agent/conversations/${encodeURIComponent(conversationId)}`, {
    headers: { Accept: 'application/json', ...browserSessionHeaders() }, credentials: 'same-origin', cache: 'no-store', signal,
  })
  if (!response.ok) throw await responseError(response)
  return validateAgentResponse(await response.json())
}

export async function getAgentCapabilities(
  signal?: AbortSignal,
): Promise<AgentCapability[]> {
  const response = await fetch('/api/v2/agent/capabilities', {
    headers: { Accept: 'application/json', ...browserSessionHeaders() },
    credentials: 'same-origin', cache: 'no-store',
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
    { method: 'DELETE', signal, headers: browserSessionHeaders(), credentials: 'same-origin', cache: 'no-store' },
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
    credentials: 'same-origin', cache: 'no-store',
    headers: {
      ...browserSessionHeaders(),
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
