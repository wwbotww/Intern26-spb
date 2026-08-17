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

  it('sends one selected mode and parses the assistant event contract', async () => {
    const payload = [
      ': keep-alive\n\n',
      'event: status\ndata: {"stage":"retrieving","mode":"device_price","message":"正在查询设备参考价格"}\n\n',
      'event: evidence\ndata: {"items":[{"evidence_id":"price-1","type":"device_price","title":"示例设备","brand":"示例品牌","model":"示例型号","specification":"256GB","price":"3999.00","currency":"CNY","source":"官方商城","observed_at":"2026-08-01T00:00:00Z","availability":"ON_SALE","source_url":"https://example.test/device","original_price":"4299.00","original_price_type":"LIST_PRICE","official_product_id":"product-1","official_sku_id":"sku-1","match_score":98.5}]}\n\n',
      'event: delta\ndata: {"content":"价格回答"}\n\n',
      'event: usage\ndata: {"total_tokens":3}\n\n',
      'event: done\ndata: {"request_id":"r1","mode":"device_price","used_tool":"device_price","finish_reason":"partial","reason_code":"","warnings":["存在多个候选"],"missing_fields":["capacity"]}\n\n',
    ].join('')
    const encoded = bytes(payload)
    const splitAt = encoded.indexOf(0xe8) + 1
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        encoded.slice(0, 17),
        encoded.slice(17, splitAt),
        encoded.slice(splitAt),
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)
    const events: ChatEvent[] = []

    await streamChat({
      mode: 'device_price',
      question: '示例型号 256GB 多少钱？',
      requestId: 'r1',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: 'device_price',
      question: '示例型号 256GB 多少钱？',
      stream: true,
    })
    expect(events.map((event) => event.type)).toEqual([
      'status',
      'evidence',
      'delta',
      'usage',
      'done',
    ])
    expect(events[0]).toMatchObject({
      type: 'status',
      mode: 'device_price',
      message: '正在查询设备参考价格',
    })
    expect(events[1]).toMatchObject({
      type: 'evidence',
      items: [{ type: 'device_price', official_sku_id: 'sku-1' }],
    })
    expect(events[4]).toMatchObject({
      type: 'done',
      finishReason: 'partial',
      warnings: ['存在多个候选'],
      missingFields: ['capacity'],
    })
  })

  it('parses policy evidence without the retired metadata event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes(
            'event: evidence\ndata: {"items":[{"evidence_id":"policy-1","type":"policy","title":"示例政策","source_url":"https://example.test/policy","excerpt":"示例条文","document_no":"示例文号","published_at":"2026-08-01","source_org":"示例机构","section_path":"第一条","chunk_id":"chunk-1","document_id":"doc-1","score":0.8,"rerank_score":0.9}]}\n\n' +
              'event: done\ndata: {"request_id":"r2","mode":"policy","used_tool":"policy_knowledge","finish_reason":"stop","reason_code":"","warnings":[],"missing_fields":[]}\n\n',
          ),
        ]),
      ),
    )
    const events: ChatEvent[] = []

    await streamChat({
      mode: 'policy',
      question: '需要哪些材料？',
      requestId: 'r2',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    expect(events[0]).toMatchObject({
      type: 'evidence',
      items: [{ type: 'policy', chunk_id: 'chunk-1' }],
    })
  })

  it('surfaces an SSE error event', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          bytes(
            'event: error\ndata: {"request_id":"r3","code":"tool_unavailable","message":"所选查询能力暂不可用"}\n\n',
          ),
        ]),
      ),
    )
    const events: ChatEvent[] = []

    await streamChat({
      mode: 'policy',
      question: '测试问题',
      requestId: 'r3',
      signal: new AbortController().signal,
      onEvent: (event) => events.push(event),
    })

    expect(events).toEqual([
      {
        type: 'error',
        requestId: 'r3',
        code: 'tool_unavailable',
        message: '所选查询能力暂不可用',
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
        mode: 'policy',
        question: '测试问题',
        requestId: 'r4',
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
        mode: 'policy',
        question: '测试问题',
        requestId: 'r5',
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({ code: 'stream_interrupted' })
  })
})
