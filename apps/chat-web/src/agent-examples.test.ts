import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { describe, expect, it } from 'vitest'

import type { AgentCapability, PublicIntent } from './agent-api'
import { AGENT_EXAMPLES, availableAgentExamples } from './agent-examples'
import AgentNotice from './components/AgentNotice.vue'

const capability = (intent: PublicIntent, available = true): AgentCapability => ({
  intent, available, display_name: intent, required_inputs: [],
})

describe('curated homepage examples', () => {
  it('keeps a grounded policy question and an observed price model, without hardcoded answers', () => {
    expect(AGENT_EXAMPLES.map((item) => [item.intent, item.message])).toEqual([
      ['policy', '快件丢失后，消费者可以通过哪些渠道投诉？'],
      ['device_price', '查询 iPhone 17 256GB 的参考价格'],
    ])
    expect(new Set(AGENT_EXAMPLES.map((item) => item.id)).size).toBe(AGENT_EXAMPLES.length)
    expect(AGENT_EXAMPLES.every((item) => !('answer' in item))).toBe(true)
  })

  it('fails closed before capability loading and when its service is unavailable', () => {
    expect(availableAgentExamples([])).toEqual([])
    expect(availableAgentExamples([capability('policy', false), capability('device_price', false)])).toEqual([])
    expect(availableAgentExamples([capability('policy'), capability('device_price', false)]).map((item) => item.intent)).toEqual(['policy'])
  })

  it('does not manufacture sample questions just because another provider is enabled', () => {
    expect(availableAgentExamples([capability('tracking'), capability('postage'), capability('delivery_time')])).toEqual([])
  })
})

describe('floating notice markup', () => {
  it('keeps a polite live region and accessible close control, escaping message text', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(AgentNotice, { message: '<script>untrusted()</script>' }) }))
    expect(html).toContain('class="agent-notice-region"')
    expect(html).toContain('role="status"')
    expect(html).toContain('aria-live="polite"')
    expect(html).toContain('aria-atomic="true"')
    expect(html).toContain('aria-label="关闭提示"')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>untrusted()')
  })

  it('leaves no close control or visible toast when dismissed', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(AgentNotice, { message: '' }) }))
    expect(html).toContain('role="status"')
    expect(html).not.toContain('aria-label="关闭提示"')
    expect(html).not.toContain('class="agent-notice"')
  })
})
