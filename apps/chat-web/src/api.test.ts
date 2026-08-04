import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamChat } from './api'
import type { ChatEvent } from './api'

function streamResponse(chunks: Uint8Array[], status = 200): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(chunk))
        controller.close()
      },
    }),
    {
      status,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

function bytes(value: string): Uint8Array {
  return new TextEncoder().encode(value)
}

describe('streamChat', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('parses split UTF-8 data, keep-alives and multiple events', async () => {
    const payload = [
      ': keep-alive\n\n',
      'event: metadata\ndata: {"request_id":"r1","model":"deepseek","citations":[]}\n\n',
      'event: delta\ndata: {"content":"政策回答"}\n\n',
      'event: usage\ndata: {"total_tokens":3}\n\n',
      'event: done\ndata: {"request_id":"r1","finish_reason":"stop"}\n\n',
    ].join('')
    const encoded = bytes(payload)
    const splitAt = encoded.indexOf(0xe7) + 1
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          encoded.slice(0, 17),
          encoded.slice(17, splitAt),
          encoded.slice(splitAt),
        ]),
      ),
    )
    const events: ChatEvent[] = []

    await streamChat({
      question: '测试问题',
      requestId: 'r1',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    expect(events.map((event) => event.type)).toEqual([
      'metadata',
      'delta',
      'usage',
      'done',
    ])
    expect(events[1]).toEqual({ type: 'delta', content: '政策回答' })
  })

  it('surfaces an SSE error event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes(
            'event: error\ndata: {"request_id":"r2","code":"chat_failed","message":"模型失败"}\n\n',
          ),
        ]),
      ),
    )
    const events: ChatEvent[] = []

    await streamChat({
      question: '测试问题',
      requestId: 'r2',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    expect(events).toEqual([
      {
        type: 'error',
        requestId: 'r2',
        code: 'chat_failed',
        message: '模型失败',
      },
    ])
  })

  it('extracts structured non-2xx errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: 'unauthorized', message: '无效密钥' },
          }),
          {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    )

    await expect(
      streamChat({
        question: '测试问题',
        requestId: 'r3',
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({
      code: 'unauthorized',
      message: '无效密钥',
      status: 401,
    })
  })

  it('rejects a stream that ends without a terminal event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes('event: delta\ndata: {"content":"未完成"}\n\n'),
        ]),
      ),
    )

    await expect(
      streamChat({
        question: '测试问题',
        requestId: 'r4',
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({ code: 'stream_interrupted' })
  })
})
