import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'
import { validateAgentResult, validateAgentStreamEvent, requiredInputs } from './agent-api'
import { loadAgentSession } from './agent-session'
import { validateProductPrice } from './product-price-contract'
import ProductPriceResult from './components/results/ProductPriceResult.vue'
import AgentSlotForm from './components/AgentSlotForm.vue'
import { slotReply } from './agent-slot-input'

// Independently authored consumer fixture, never generated from service output.
export const freshPrice = {
  type: 'product_price', schema_version: '1', truncated: false,
  items: [{
    identity: { kind: 'fresh', commodity_name: '合成苹果', source_specification: '合成品种', market_name: null },
    quote: { kind: 'priced', currency: 'CNY', current_price: '3.50', original_price: null,
      original_price_type: 'NONE', availability: 'UNKNOWN', price_nature: 'RETAIL_AVERAGE',
      quoted_unit: 'CNY_PER_500G', unit_price: { amount: '7.000000', unit: 'CNY_PER_KG' },
      observed_at: '2026-08-01T16:00:00Z', time_precision: 'DAY' },
    source: { name: '合成来源', profile: 'SYNTHETIC', url: 'https://example.test/price' },
    region: { scope: 'CITY', code: '310100' }, queried_at: '2026-09-11T00:00:00Z', freshness: 'unknown', last_known_price: null,
  }],
}
const result = () => ({ type: 'product_price', status: 'success', data: structuredClone(freshPrice) })
const render = (data: Record<string, unknown>) => renderToString(createSSRApp({ render: () => h(ProductPriceResult, { data }) }))
function restore(data: unknown) {
  const snapshot = JSON.stringify({ version: 1, conversationId: 'synthetic', selectedIntent: 'product_price', updatedAt: 1, pendingRequest: null,
    messages: [{ id: 'synthetic', role: 'assistant', content: '合成', state: 'done', createdAt: 1, requiredInputs: [], warnings: [], result: data }] })
  return loadAgentSession({ getItem: () => snapshot, removeItem: () => undefined }, 2)
}

describe('public catalog price contract and cards', () => {
  it('submits the chosen price nature without injecting opposite choices from its label', () => {
    const inputs = requiredInputs([
      { name: 'conditions.region_text', label: '报价地区', type: 'string' },
      { name: 'conditions.price_nature', label: '批发或零售口径', type: 'choice', choices: ['零售', '批发', '分别列出多个来源'] },
    ])
    expect(slotReply(inputs, { 'conditions.region_text': '上海', 'conditions.price_nature': '零售' })).toBe('报价地区：上海；零售')
  })
  it('preserves exact strings, source dates and unknown freshness across history', async () => {
    const data = validateAgentResult(result())!
    expect(restore(data)?.messages[0].result?.data).toEqual(freshPrice)
    const html = await render(freshPrice)
    for (const label of ['3.50', '元 / 500克', '7.000000', '元 / 公斤', '零售均价', '时效未知', '2026/08/02', '合成来源']) expect(html).toContain(label)
    expect(html).not.toContain('缺货')
    expect(html).not.toContain('即时报价')
  })

  it.each([
    ['schema_version', '2'], ['items', []], ['items.0.source_listing_id', 42],
    ['items.0.quote.current_price', 3.50], ['items.0.quote.current_price', '0.00'],
    ['items.0.quote.unit_price.amount', '8.00'], ['items.0.quote.unit_price', null],
    ['items.0.quote.quoted_unit', 'CNY_PER_PIECE'], ['items.0.quote.price_nature', 'RETAIL_OFFER'],
    ['items.0.quote.observed_at', '2026-08-01T00:00:00'], ['items.0.quote.observed_at', '2099-08-01T00:00:00Z'],
    ['items.0.source.url', 'javascript:alert(1)'], ['items.0.source.url', 'https://user:password@example.test'],
    ['items.0.identity.variant_id', 'private'], ['items.0.quote.kind', 'availability_only'],
    ['items.0.quote.original_price', '10.00'], ['items.0.quote.original_price_type', 'DISCOUNT'],
  ])('rejects malformed JSON/SSE/history at %s', (path, value) => {
    const raw: any = result()
    let target = raw.data
    const parts = String(path).split('.')
    for (const part of parts.slice(0, -1)) target = target[part]
    target[parts.at(-1)!] = value
    expect(() => validateAgentResult(raw)).toThrow()
    expect(() => validateAgentStreamEvent('result', { schema_version: '1', request_id: 's', conversation_id: 's', turn_id: 's', result: raw, warnings: [] })).toThrow()
    expect(restore(raw)).toBeNull()
  })

  it('renders amountless device state without zero or invented historical price', async () => {
    const data: any = structuredClone(freshPrice)
    data.items[0].identity = { kind: 'device', brand: '合成品牌', product_name: '合成型号', specification: { capacity: '256GB' } }
    Object.assign(data.items[0].quote, { kind: 'availability_only', current_price: null, unit_price: null,
      availability: 'OUT_OF_STOCK', price_nature: 'RETAIL_OFFER', quoted_unit: 'CNY_PER_PIECE' })
    expect(validateProductPrice(data).items[0].quote.current_price).toBeNull()
    const html = await render(data)
    expect(html).toContain('缺货'); expect(html).toContain('暂无当前价格')
    expect(html).not.toContain('0.00'); expect(html).not.toContain('历史补充')
  })

  it('renders escaped candidates with expiry but never places tokens in labels', async () => {
    const inputs = requiredInputs([{ name: 'price_selection', label: '商品', type: 'choice', choices: [],
      price_candidates: [{ candidate_token: 'a'.repeat(43), label: '<script>合成候选</script>', expires_at: '2020-01-01T00:00:00Z' }] }])
    const html = await renderToString(createSSRApp({ render: () => h(AgentSlotForm, { inputs, pending: false }) }))
    expect(html).toContain('已过期'); expect(html).toContain('disabled')
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('a'.repeat(43))
  })

  it.each(['raw-id', '', 'a'.repeat(44)])('rejects a malformed candidate token %s', (candidate_token) => {
    expect(() => requiredInputs([{ name: 'price_selection', label: '商品', type: 'choice', price_candidates: [{ candidate_token, label: '商品', expires_at: '2026-09-11T00:00:00Z' }] }])).toThrow()
  })
})
