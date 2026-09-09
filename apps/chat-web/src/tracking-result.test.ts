import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'

import { validateAgentResult, validateAgentStreamEvent } from './agent-api'
import type { AgentResult } from './agent-api'
import { AGENT_SESSION_KEY, loadAgentSession } from './agent-session'
import AgentResultRenderer from './components/results/AgentResultRenderer.vue'

const source = {
  source_type: 'external_api' as const,
  source_name: 'postal-tracking',
  source_profile: 'postal-tracking-doc-2019-v1',
  queried_at: '2026-09-09T02:00:00Z',
  history_completeness: 'unknown' as const,
}
const result: AgentResult = {
  type: 'tracking', status: 'success', provenance: [source],
  data: {
    mail_no: '1234567890123', current_status: '运输', queried_at: source.queried_at,
    events: [{ description: '合成运输节点', occurred_at: source.queried_at, location: '测试站' }],
  },
}
const render = (value: AgentResult) => renderToString(createSSRApp({
  render: () => h(AgentResultRenderer, { result: value }),
}))

describe('tracking source contract and renderer', () => {
  it('renders an external source, its original time, profile, and unknown completeness', async () => {
    const html = await render(result)
    for (const label of ['外部接口（配置声明）', source.source_profile, '观察时间', '未确认', '重放保留原观察时间', '合成运输节点']) {
      expect(html).toContain(label)
    }
    expect(html).not.toContain('最新状态')
  })

  it('preserves no_match provenance instead of inventing tracking facts', async () => {
    const html = await render({ ...result, status: 'no_match', data: null })
    expect(html).toContain('未返回记录不代表邮件不存在')
    expect(html).toContain(source.source_profile)
    expect(html).not.toContain('1234567890123')
  })

  it('labels synthetic sources and old source-less responses explicitly', async () => {
    const synthetic = await render({ ...result, provenance: [{ ...source, source_type: 'fake_gateway' }] })
    expect(synthetic).toContain('合成测试数据')
    const legacy = { ...result }
    delete legacy.provenance
    const parsed = validateAgentResult(legacy)
    expect(parsed?.provenance).toEqual([])
    expect(await render(parsed!)).toContain('该历史结果未记录来源信息')
  })

  it('renders partial history and escapes event text', async () => {
    const html = await render({
      ...result, status: 'partial',
      provenance: [{ ...source, history_completeness: 'partial' }],
      data: { ...result.data, events: [{ description: '<img src=x onerror=alert(1)>' }] },
    })
    expect(html).toContain('部分历史')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it.each([
    { source_type: 'forged' }, { source_name: 'https://private.test' },
    { source_profile: 'x'.repeat(129) }, { history_completeness: 'maybe' },
    { queried_at: '2026-09-09T10:00:00' }, { queried_at: 'not-a-date' },
  ])('rejects malformed source fields in JSON and SSE: %j', (changes) => {
    const malformed = { ...result, provenance: [{ ...source, ...changes }] }
    expect(() => validateAgentResult(malformed)).toThrow()
    expect(() => validateAgentStreamEvent('result', {
      schema_version: '1', request_id: 'synthetic-request',
      conversation_id: '11111111-1111-4111-8111-111111111111',
      turn_id: '22222222-2222-4222-8222-222222222222', result: malformed,
    })).toThrow()
  })

  it('projects only allowed fields and retains the exact replay observation time', () => {
    const raw = { ...result, provenance: [{ ...source, source_url: 'private-canary' }] }
    const parsed = validateAgentResult(raw)
    expect(parsed?.provenance?.[0]).toEqual(source)
    expect(JSON.stringify(parsed)).not.toContain('private-canary')
  })

  it('rejects malformed restored sources while accepting historical missing provenance', () => {
    const snapshot = (value: unknown) => JSON.stringify({
      version: 1, conversationId: 'synthetic', selectedIntent: 'tracking',
      updatedAt: 1, pendingRequest: null,
      messages: [{
        id: 'synthetic', role: 'assistant', content: '合成结果', state: 'done',
        createdAt: 1, requiredInputs: [], warnings: [], result: value,
      }],
    })
    let raw = snapshot({ ...result, provenance: [null] })
    const storage = { getItem: (key: string) => key === AGENT_SESSION_KEY ? raw : null, removeItem: () => undefined }
    expect(loadAgentSession(storage, 2)).toBeNull()
    const legacy = { ...result }
    delete legacy.provenance
    raw = snapshot(legacy)
    expect(loadAgentSession(storage, 2)?.messages[0]?.result?.provenance).toEqual([])
    raw = snapshot(result)
    expect(loadAgentSession(storage, 2)?.messages[0]?.result?.provenance?.[0]?.queried_at).toBe(source.queried_at)
  })
})
