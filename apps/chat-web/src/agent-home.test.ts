import { createRenderer, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentApp from './AgentApp.vue'
import { AgentApiError, bootstrapAgentBrowserSession, getAgentCapabilities, streamAgentMessage } from './agent-api'
import { AGENT_EXAMPLES } from './agent-examples'
import { NOTICE_DURATION_MS } from './use-transient-notice'

vi.mock('./agent-api', async (original) => ({
  ...await original<typeof import('./agent-api')>(),
  bootstrapAgentBrowserSession: vi.fn(), getAgentCapabilities: vi.fn(), streamAgentMessage: vi.fn(),
}))
vi.mock('./agent-session', () => ({
  AGENT_SESSION_KEY: 'synthetic-session', loadAgentSession: () => null,
  saveAgentSession: vi.fn(), clearAgentSession: vi.fn(),
}))
// Test the parent lifecycle without a DOM dependency. The real notice's live
// region/escaping is covered by SSR; timers are covered with an effect scope.
vi.mock('./components/AgentNotice.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ props: ['message'], setup: (props) => () => h('aside', { class: 'notice-stub' }, props.message) }) }
})
vi.mock('./components/AgentMessage.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ props: ['message'], setup: (props) => () => h('article', props.message.content) }) }
})

interface TestNode {
  tag: string
  text: string
  props: Record<string, unknown>
  children: TestNode[]
  parent: TestNode | null
}
const node = (tag: string, text = ''): TestNode => ({ tag, text, props: {}, children: [], parent: null })
function insert(child: TestNode, parent: TestNode, anchor: TestNode | null = null) {
  remove(child)
  child.parent = parent
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
}
function remove(child: TestNode) {
  if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
  child.parent = null
}
const renderer = createRenderer<TestNode, TestNode>({
  createElement: (tag) => node(tag), createText: (text) => node('text', text), createComment: (text) => node('comment', text),
  insert, remove, setText: (item, text) => { item.text = text },
  setElementText: (item, text) => { item.text = text; item.children = [] },
  patchProp: (item, key, _previous, value) => { item.props[key] = value },
  parentNode: (item) => item.parent,
  nextSibling: (item) => item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null,
  insertStaticContent: (html, parent, anchor) => {
    const item = node('static', html)
    insert(item, parent, anchor)
    return [item, item]
  },
})
function find(root: TestNode, predicate: (item: TestNode) => boolean): TestNode | undefined {
  if (predicate(root)) return root
  for (const child of root.children) {
    const found = find(child, predicate)
    if (found) return found
  }
  return undefined
}
function textOf(item: TestNode): string { return item.text + item.children.map(textOf).join('') }
async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
}
const apps: ReturnType<typeof renderer.createApp>[] = []
async function mountHome() {
  const root = node('root')
  const app = renderer.createApp(AgentApp)
  apps.push(app)
  app.mount(root)
  await flush()
  return root
}

describe('Agent homepage identity, notices and real request wiring', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_AGENT_BROWSER_SESSION', 'true')
    vi.stubGlobal('document', { hidden: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })
    vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() })
    vi.stubGlobal('localStorage', {})
    vi.stubGlobal('requestAnimationFrame', vi.fn().mockReturnValue(1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.mocked(bootstrapAgentBrowserSession).mockResolvedValue({ session_ref: 'a'.repeat(64), expires_at: new Date(Date.now() + 60_000).toISOString() })
    vi.mocked(getAgentCapabilities).mockResolvedValue([
      { intent: 'policy', display_name: '政策查询', available: true, required_inputs: [] },
      { intent: 'device_price', display_name: '设备价格', available: true, required_inputs: [] },
    ])
    vi.mocked(streamAgentMessage).mockResolvedValue(undefined)
  })
  afterEach(() => {
    apps.splice(0).forEach((app) => app.unmount())
    vi.resetAllMocks()
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('shows verification success outside main, then dismisses it without hiding the composer', async () => {
    const root = await mountHome()
    const notice = find(root, (item) => item.tag === 'aside')!
    const main = find(root, (item) => item.tag === 'main')!
    expect(textOf(notice)).toContain('已建立匿名访客会话')
    expect(find(main, (item) => item === notice)).toBeUndefined()
    expect(textOf(main)).not.toContain('已建立匿名访客会话')
    vi.advanceTimersByTime(NOTICE_DURATION_MS)
    await flush()
    expect(textOf(notice)).toBe('')
    expect(find(root, (item) => item.tag === 'textarea')).toBeDefined()
    expect(bootstrapAgentBrowserSession).toHaveBeenCalledTimes(1)
  })

  it('keeps failed identity verification and recovery actions visible, never sends a request', async () => {
    vi.mocked(bootstrapAgentBrowserSession).mockRejectedValue(new AgentApiError('browser_session_expired', '访客身份已过期', 401))
    const root = await mountHome()
    vi.advanceTimersByTime(NOTICE_DURATION_MS * 2)
    await flush()
    expect(textOf(root)).toContain('访客身份已过期')
    expect(find(root, (item) => item.tag === 'button' && textOf(item) === '重新核验')).toBeDefined()
    expect(find(root, (item) => item.tag === 'textarea')).toBeUndefined()
    expect(getAgentCapabilities).not.toHaveBeenCalled()
    expect(streamAgentMessage).not.toHaveBeenCalled()
  })

  it.each(AGENT_EXAMPLES)('submits $id through the standard stream with its explicit intent', async (example) => {
    const root = await mountHome()
    // A previously selected different card must not hijack an example's route.
    const other = example.intent === 'policy' ? '设备价格' : '政策查询'
    const card = find(root, (item) => item.tag === 'button' && String(item.props.class).includes('agent-capability-card') && textOf(item).includes(other))!
    ;(card.props.onClick as () => void)()
    const button = find(root, (item) => item.tag === 'button' && textOf(item) === example.message)!
    expect(button.props.disabled).toBe(false)
    ;(button.props.onClick as () => void)()
    await flush()
    expect(streamAgentMessage).toHaveBeenCalledTimes(1)
    expect(vi.mocked(streamAgentMessage).mock.calls[0][0]).toMatchObject({
      payload: { message: example.message, explicit_intent: example.intent },
      idempotencyKey: expect.stringMatching(/^web-message-/),
    })
  })

  it('does not offer example buttons when their services are unavailable', async () => {
    vi.mocked(getAgentCapabilities).mockResolvedValue([])
    const root = await mountHome()
    expect(find(root, (item) => item.props.class === 'agent-examples')).toBeUndefined()
  })

  it('cleans both the identity expiry timer and the notice timer on unmount', async () => {
    await mountHome()
    expect(vi.getTimerCount()).toBe(2)
    apps.pop()!.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
