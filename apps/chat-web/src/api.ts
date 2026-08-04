export interface Citation {
  index: number
  chunk_id: string
  document_id: string
  title: string
  source_url: string
  document_no: string
  published_at: string
  source_org: string
  section_path: string
  score: number
  rerank_score: number | null
  excerpt: string
}

export type ChatEvent =
  | {
      type: 'metadata'
      requestId: string
      model: string
      citations: Citation[]
    }
  | { type: 'delta'; content: string }
  | { type: 'usage'; usage: Record<string, unknown> }
  | { type: 'done'; requestId: string; finishReason: string }
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

interface RawSseEvent {
  event: string
  data: unknown
}

function parseSseBlock(block: string): RawSseEvent | null {
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

function toChatEvent(raw: RawSseEvent): ChatEvent | null {
  const data = asRecord(raw.data)

  if (raw.event === 'metadata') {
    return {
      type: 'metadata',
      requestId: String(data.request_id ?? ''),
      model: String(data.model ?? ''),
      citations: Array.isArray(data.citations)
        ? (data.citations as Citation[])
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
      finishReason: String(data.finish_reason ?? 'stop'),
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
    body: JSON.stringify({ question: options.question, stream: true }),
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
