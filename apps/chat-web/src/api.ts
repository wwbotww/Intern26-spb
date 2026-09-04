export type QueryMode = 'policy' | 'device_price'

export interface PolicyEvidence {
  evidence_id: string
  type: 'policy'
  title: string
  source_url: string
  excerpt: string
  document_no: string
  published_at: string
  source_org: string
  section_path: string
  chunk_id: string
  document_id: string
  score: number
  rerank_score: number | null
}

export interface DevicePriceEvidence {
  evidence_id: string
  type: 'device_price'
  title: string
  brand: string
  model: string
  specification: string
  price: string
  currency: string
  source: string
  observed_at: string
  availability: string
  source_url: string
  original_price: string | null
  original_price_type: string
  official_product_id: string
  official_sku_id: string
  match_score: number
}

export type Evidence = PolicyEvidence | DevicePriceEvidence

export type ChatEvent =
  | {
      type: 'status'
      stage: string
      mode: QueryMode
      message: string
    }
  | { type: 'evidence'; items: Evidence[] }
  | { type: 'delta'; content: string }
  | { type: 'usage'; usage: Record<string, unknown> }
  | {
      type: 'done'
      requestId: string
      mode: QueryMode
      usedTool: string
      finishReason: string
      reasonCode: string
      warnings: string[]
      missingFields: string[]
    }
  | {
      type: 'error'
      requestId?: string
      code: string
      message: string
    }

export class ChatApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
  ) {
    super(message)
    this.name = 'ChatApiError'
  }
}

export interface RawSseEvent {
  event: string
  data: unknown
}

export function parseSseBlock(block: string): RawSseEvent | null {
  let event = 'message'
  const data: string[] = []

  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue

    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    if (field === 'data') data.push(value)
  }

  if (data.length === 0) return null

  try {
    return { event, data: JSON.parse(data.join('\n')) }
  } catch {
    throw new ChatApiError('invalid_sse', '服务返回了无法解析的流式数据。')
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : {}
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : []
}

function asMode(value: unknown): QueryMode {
  return value === 'device_price' ? 'device_price' : 'policy'
}

function asEvidence(value: unknown): Evidence | null {
  const item = asRecord(value)
  if (item.type === 'policy') return item as unknown as PolicyEvidence
  if (item.type === 'device_price') {
    return item as unknown as DevicePriceEvidence
  }
  return null
}

function toChatEvent(raw: RawSseEvent): ChatEvent | null {
  const data = asRecord(raw.data)

  if (raw.event === 'status') {
    return {
      type: 'status',
      stage: String(data.stage ?? ''),
      mode: asMode(data.mode),
      message: String(data.message ?? '正在查询资料'),
    }
  }
  if (raw.event === 'evidence') {
    return {
      type: 'evidence',
      items: Array.isArray(data.items)
        ? data.items
            .map((item) => asEvidence(item))
            .filter((item): item is Evidence => item !== null)
        : [],
    }
  }
  if (raw.event === 'delta') {
    return { type: 'delta', content: String(data.content ?? '') }
  }
  if (raw.event === 'usage') {
    return { type: 'usage', usage: data }
  }
  if (raw.event === 'done') {
    return {
      type: 'done',
      requestId: String(data.request_id ?? ''),
      mode: asMode(data.mode),
      usedTool: String(data.used_tool ?? ''),
      finishReason: String(data.finish_reason ?? 'stop'),
      reasonCode: String(data.reason_code ?? ''),
      warnings: asStringList(data.warnings),
      missingFields: asStringList(data.missing_fields),
    }
  }
  if (raw.event === 'error') {
    return {
      type: 'error',
      requestId: data.request_id
        ? String(data.request_id)
        : undefined,
      code: String(data.code ?? 'chat_failed'),
      message: String(data.message ?? '问答服务异常'),
    }
  }
  return null
}

async function responseError(response: Response): Promise<ChatApiError> {
  let code = `http_${response.status}`
  let message = `请求失败（HTTP ${response.status}）。`

  try {
    const payload = asRecord(await response.json())
    const detail = asRecord(payload.detail)
    if (detail.code) code = String(detail.code)
    if (detail.message) message = String(detail.message)
  } catch {
    // The status still gives the caller a useful error when the body is empty.
  }

  return new ChatApiError(code, message, response.status)
}

export async function streamChat(options: {
  mode: QueryMode
  question: string
  requestId: string
  signal: AbortSignal
  onEvent: (event: ChatEvent) => void
}): Promise<void> {
  const response = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
      'X-Request-ID': options.requestId,
    },
    body: JSON.stringify({
      mode: options.mode,
      question: options.question,
      stream: true,
    }),
    signal: options.signal,
  })

  if (!response.ok) throw await responseError(response)
  if (!response.body) {
    throw new ChatApiError('empty_stream', '服务没有返回可读取的响应流。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminalEventReceived = false

  const dispatch = (block: string): void => {
    const raw = parseSseBlock(block)
    if (!raw) return
    const event = toChatEvent(raw)
    if (!event) return
    options.onEvent(event)
    if (event.type === 'done' || event.type === 'error') {
      terminalEventReceived = true
    }
  }

  while (!terminalEventReceived) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })

    let boundary = buffer.match(/\r?\n\r?\n/)
    while (boundary?.index !== undefined) {
      const block = buffer.slice(0, boundary.index)
      buffer = buffer.slice(boundary.index + boundary[0].length)
      dispatch(block)
      if (terminalEventReceived) break
      boundary = buffer.match(/\r?\n\r?\n/)
    }

    if (done) break
  }

  if (terminalEventReceived) {
    await reader.cancel()
    return
  }

  if (buffer.trim()) dispatch(buffer)
  if (!terminalEventReceived) {
    throw new ChatApiError(
      'stream_interrupted',
      '回答流意外中断，请稍后重试。',
    )
  }
}
