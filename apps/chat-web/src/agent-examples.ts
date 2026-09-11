import type { AgentCapability, PublicIntent } from './agent-api'

export interface AgentExample {
  readonly id: string
  readonly intent: PublicIntent
  readonly message: string
}

// Curated against the deployed data on 2026-09-10. Capability readiness alone
// does not prove that an arbitrary question/model has supporting records.
export const AGENT_EXAMPLES: readonly AgentExample[] = [
  {
    id: 'policy-complaint-channels',
    intent: 'policy',
    message: '快件丢失后，消费者可以通过哪些渠道投诉？',
  },
  {
    id: 'price-iphone-17-256gb',
    intent: 'device_price',
    message: '查询 iPhone 17 256GB 的参考价格',
  },
]

export function availableAgentExamples(capabilities: readonly AgentCapability[]): readonly AgentExample[] {
  const available = new Set(capabilities.filter((item) => item.available).map((item) => item.intent))
  return AGENT_EXAMPLES.map((item): AgentExample => item.intent === 'device_price' && available.has('product_price')
    ? { ...item, intent: 'product_price' } : item).filter((item) => available.has(item.intent))
}
