import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'

import { validateAgentResult, validateAgentStreamEvent } from './agent-api'
import type { AgentResult, RequiredInput } from './agent-api'
import { AGENT_SESSION_KEY, loadAgentSession } from './agent-session'
import { slotReply } from './agent-slot-input'
import AgentSlotForm from './components/AgentSlotForm.vue'
import AgentResultRenderer from './components/results/AgentResultRenderer.vue'

const result: AgentResult = {
  type: 'postage', status: 'success', provenance: [],
  data: { amount: '12.30', currency: 'CNY', product_code: 'SYN-A', queried_at: '2026-09-09T08:00:00Z' },
  quote_basis: {
    schema_version: '1', amount_kind: 'total', currency: 'CNY', product_code: 'SYN-A',
    scope: 'domestic_actual_weight_no_extras', is_estimate: true,
    fees: [{ kind: 'fuel', amount: '0.80', included_in_amount: 'unknown' }],
    source: { source_type: 'fake_gateway', source_name: 'synthetic-postal-postage', source_profile: 'postal-postage-synthetic-v1', queried_at: '2026-09-09T08:00:00Z' },
  },
}
const render = (value: AgentResult) => renderToString(createSSRApp({ render: () => h(AgentResultRenderer, { result: value }) }))

describe('postage basis contract, restore and renderer', () => {
  it('shows synthetic source and full estimate scope even without outer warnings', async () => {
    const html = await render(validateAgentResult(result)!)
    for (const label of ['12.30', '合成测试数据', '国内按实重、无增值服务', '总报价', '不是最终收费', '是否包含尚未确认', '不自动相加', '重放保留原观察时间']) expect(html).toContain(label)
    expect(html).not.toContain('13.10')
    expect(html).not.toContain('历史完整性')
  })

  it('does not invent legacy currency, source or fees', async () => {
    const old = { type: 'postage', status: 'success', data: { amount: '10.00' } }
    const parsed = validateAgentResult(old)!
    expect(parsed.quote_basis).toBeNull()
    const html = await render(parsed)
    expect(html).toContain('币种未记录')
    expect(html).toContain('历史结果未记录报价依据')
    expect(html).not.toContain('CNY')
    expect(html).not.toContain('合成测试数据')
  })

  it.each(['no_match', 'failed', 'partial'] as const)('does not render a success price for %s', async (status) => {
    const html = await render({ ...result, status, quote_basis: null })
    expect(html).toContain('未取得可展示的完整报价')
    expect(html).not.toContain('12.30')
  })

  it.each([
    ['type', 'tracking'], ['status', 'partial'], ['data', null],
    ['data.amount', 'NaN'], ['data.amount', 12.30], ['data.currency', 'USD'], ['data.product_code', 'SYN-B'],
    ['quote_basis.schema_version', '2'], ['quote_basis.schema_version', null],
    ['quote_basis.scope', 'international'], ['quote_basis.is_estimate', false],
    ['quote_basis.source.source_type', 'unknown'], ['quote_basis.source.source_name', 'https://private.test'],
    ['quote_basis.source.queried_at', '2026-09-09T08:00:00'],
    ['quote_basis.source.queried_at', '2026-09-10T08:00:00Z'],
    ['quote_basis.fees', null], ['quote_basis.fees.0.amount', '1.001'],
    ['quote_basis.fees', [result.quote_basis!.fees![0], result.quote_basis!.fees![0]]],
  ])('rejects malformed JSON/SSE/restored basis at %s', (path, value) => {
    const raw = JSON.parse(JSON.stringify(result))
    const keys = String(path).split('.')
    let target = raw
    for (const key of keys.slice(0, -1)) target = target[key]
    target[keys.at(-1)!] = value
    expect(() => validateAgentResult(raw)).toThrow()
    expect(() => validateAgentStreamEvent('result', {
      schema_version: '1', request_id: 'synthetic', conversation_id: 'synthetic', turn_id: 'synthetic', result: raw,
    })).toThrow()
    expect(restore(raw)).toBeNull()
  })

  function restore(value: unknown) {
    const snapshot = JSON.stringify({
      version: 1, conversationId: 'synthetic', selectedIntent: 'postage', updatedAt: 1, pendingRequest: null,
      messages: [{ id: 'synthetic', role: 'assistant', content: '合成结果', state: 'done', createdAt: 1, requiredInputs: [], warnings: [], result: value }],
    })
    return loadAgentSession({ getItem: (key) => key === AGENT_SESSION_KEY ? snapshot : null, removeItem: () => undefined }, 2)
  }

  it('restores exact basis/time and drops nonallowlisted source/context fields', () => {
    const raw = { ...result, quote_basis: { ...result.quote_basis!, context: 'PRIVATE', source: { ...result.quote_basis!.source, source_url: 'PRIVATE' } } }
    const parsed = validateAgentResult(raw)
    expect(parsed?.quote_basis).toEqual(result.quote_basis)
    expect(JSON.stringify(parsed)).not.toContain('PRIVATE')
    expect(restore(parsed)?.messages[0].result?.quote_basis).toEqual(result.quote_basis)
  })
})

describe('structured product and explicit confirmation inputs', () => {
  const product: RequiredInput = { name: 'product_code', label: '询价产品', type: 'choice', choices: ['SYN-A', 'SYN-B'] }
  const confirmation: RequiredInput = { name: 'postage_confirmation', label: '报价范围确认', type: 'choice', choices: ['确认基础询价'] }
  it('renders selects without a preselected product or implicit approval', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(AgentSlotForm, { inputs: [product], pending: false }) }))
    expect(html).toContain('<select')
    expect(html).toContain('请选择询价产品')
    expect(html).toContain('disabled')
    expect(slotReply([product], {})).toBe('')
    expect(slotReply([product], { product_code: 'UNKNOWN' })).toBe('')
    expect(slotReply([product], { product_code: 'SYN-A' })).toBe('询价产品：SYN-A')
  })
  it('sends confirmation as the exact standalone reply', () => {
    expect(slotReply([confirmation], {})).toBe('')
    expect(slotReply([confirmation], { postage_confirmation: '确认基础询价' })).toBe('确认基础询价')
    expect(slotReply([confirmation, product], { postage_confirmation: '确认基础询价', product_code: 'SYN-A' })).toBe('')
  })
})
