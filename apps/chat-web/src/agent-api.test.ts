import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getAgentCapabilities,
  streamAgentMessage,
  validateAgentStreamEvent,
} from './agent-api'
import type { AgentStreamEvent } from './agent-api'


function bytes(value: string): Uint8Array {
  return new TextEncoder().encode(value)
}


function streamResponse(chunks: Uint8Array[]): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(chunk))
        controller.close()
      },
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}


const waitingResponse = {
  request_id: 'request-1',
  conversation_id: '42bc44e7-4ac8-4db1-a4d9-eef1b2334033',
  turn_id: '5203c163-ec0d-44e2-bd36-bc683e100e60',
  phase: 'waiting_user',
  intent: 'tracking',
  reply: '请提供邮件号。',
  next_action: 'collect_slots',
  required_inputs: [
    {
      name: 'mail_no',
      label: '邮件号',
      type: 'string',
      validation_hint: '13 位数字',
      choices: [],
    },
  ],
  result: null,
  failure: null,
  warnings: [],
}


describe('Agent V2 client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('parses versioned events across chunk boundaries and sends idempotency', async () => {
    const body = [
      ': heartbeat\n\n',
      'event: status\ndata: {"schema_version":"1","request_id":"request-1","stage":"accepted","message":"正在执行"}\n\n',
      'event: future_event\ndata: {"schema_version":"9","anything":true}\n\n',
      'event: state\ndata: {"schema_version":"1","request_id":"request-1","conversation_id":"42bc44e7-4ac8-4db1-a4d9-eef1b2334033","turn_id":"5203c163-ec0d-44e2-bd36-bc683e100e60","phase":"waiting_user","intent":"tracking","next_action":"collect_slots"}\n\n',
      'event: input_required\ndata: {"schema_version":"1","request_id":"request-1","conversation_id":"42bc44e7-4ac8-4db1-a4d9-eef1b2334033","turn_id":"5203c163-ec0d-44e2-bd36-bc683e100e60","required_inputs":[{"name":"mail_no","label":"邮件号","type":"string","validation_hint":"13 位数字","choices":[]}]}\n\n',
      'event: delta\ndata: {"schema_version":"1","request_id":"request-1","conversation_id":"42bc44e7-4ac8-4db1-a4d9-eef1b2334033","turn_id":"5203c163-ec0d-44e2-bd36-bc683e100e60","content":"请提供邮件号。"}\n\n',
      `event: done\ndata: ${JSON.stringify({ schema_version: '1', response: waitingResponse })}\n\n`,
    ].join('')
    const encoded = bytes(body)
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        encoded.slice(0, 23),
        encoded.slice(23, encoded.indexOf(0xe8) + 1),
        encoded.slice(encoded.indexOf(0xe8) + 1),
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const events: AgentStreamEvent[] = []

    await streamAgentMessage({
      payload: { message: '帮我查邮件' },
      idempotencyKey: 'message-key-1',
      requestId: 'request-1',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    const [, init] = fetchMock.mock.calls[0]
    expect(init?.headers).toMatchObject({
      'Idempotency-Key': 'message-key-1',
      'X-Request-ID': 'request-1',
    })
    expect(JSON.parse(String(init?.body))).toEqual({
      message: '帮我查邮件',
      stream: true,
    })
    expect(events.map((event) => event.type)).toEqual([
      'status',
      'state',
      'input_required',
      'delta',
      'done',
    ])
    expect(events.at(-1)).toMatchObject({
      type: 'done',
      data: { response: { phase: 'waiting_user' } },
    })
  })

  it('ignores unknown events but explicitly rejects malformed known events', () => {
    expect(validateAgentStreamEvent('future', { anything: true })).toBeNull()
    expect(() =>
      validateAgentStreamEvent('delta', {
        schema_version: '2',
        request_id: 'request-2',
      }),
    ).toThrowError(
      expect.objectContaining({ code: 'unsupported_agent_event_version' }),
    )
    expect(() =>
      validateAgentStreamEvent('delta', {
        schema_version: '1',
        request_id: 'request-2',
        conversation_id: 'conversation-2',
        turn_id: 'turn-2',
        content: 42,
      }),
    ).toThrowError(expect.objectContaining({ code: 'invalid_agent_event' }))
  })

  it('rejects a stream that ends without done or error as safely retryable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes(
            'event: status\ndata: {"schema_version":"1","request_id":"request-3","stage":"accepted","message":"执行中"}\n\n',
          ),
        ]),
      ),
    )

    await expect(
      streamAgentMessage({
        payload: { message: '测试' },
        idempotencyKey: 'message-key-3',
        requestId: 'request-3',
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({
      code: 'stream_interrupted',
      retryable: true,
      requestId: 'request-3',
    })
  })

  it('validates capability discovery at runtime', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify([
            {
              intent: 'postage',
              display_name: '邮费试算',
              available: true,
              capability_version: 'phase-3a',
              required_inputs: [
                { name: 'weight', label: '重量', type: 'number' },
              ],
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    await expect(getAgentCapabilities()).resolves.toEqual([
      expect.objectContaining({
        intent: 'postage',
        available: true,
        required_inputs: [expect.objectContaining({ name: 'weight' })],
      }),
    ])
  })

  it('exposes sanitized stream error data as a terminal event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes(
            'event: error\ndata: {"schema_version":"1","request_id":"request-4","code":"dependency_unavailable","message":"依赖暂不可用","http_status":503,"category":"upstream_unavailable","retryable":true,"retry_after_seconds":2}\n\n',
          ),
        ]),
      ),
    )
    const events: AgentStreamEvent[] = []

    await streamAgentMessage({
      payload: { message: '测试' },
      idempotencyKey: 'message-key-4',
      requestId: 'request-4',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    expect(events).toEqual([
      {
        type: 'error',
        data: expect.objectContaining({
          code: 'dependency_unavailable',
          retryable: true,
          http_status: 503,
        }),
      },
    ])
  })
})
