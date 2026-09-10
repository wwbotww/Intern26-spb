import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  bootstrapAgentBrowserSession, clearAgentBrowserIdentity, deleteAgentConversation,
  getAgentCapabilities, streamAgentMessage,
} from './agent-api'

const reference = 'a'.repeat(64)
const identity = () => ({ session_ref: reference, expires_at: new Date(Date.now() + 60_000).toISOString() })
const message = () => streamAgentMessage({
  payload: { message: 'synthetic only' }, idempotencyKey: 'unchanged-idempotency',
  requestId: 'synthetic-request', signal: new AbortController().signal, onEvent: () => {},
})

describe('opt-in browser identity transport', () => {
  beforeEach(() => {
    clearAgentBrowserIdentity()
    vi.stubEnv('VITE_AGENT_BROWSER_SESSION', 'true')
  })
  afterEach(() => {
    clearAgentBrowserIdentity()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    vi.useRealTimers()
  })

  it.each(['capabilities', 'delete', 'message'])('blocks %s before verification without a request', async (operation) => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const promise = operation === 'capabilities' ? getAgentCapabilities() : operation === 'delete' ? deleteAgentConversation('old') : message()
    await expect(promise).rejects.toMatchObject({ code: 'browser_session_required' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sends cookies only through same-origin transport and attaches a non-secret binding', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(identity()))
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response('event: error\ndata: {"schema_version":"1","request_id":"synthetic-request","code":"synthetic","message":"safe","http_status":503,"retryable":false}\n\n'))
    vi.stubGlobal('fetch', fetchMock)
    await bootstrapAgentBrowserSession()
    await getAgentCapabilities()
    await deleteAgentConversation('conversation')
    await message()
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store', body: '{"reset":false}' })
    for (const [, init] of fetchMock.mock.calls.slice(1)) {
      expect(init).toMatchObject({ credentials: 'same-origin', cache: 'no-store', headers: { 'X-Agent-Session': reference } })
      expect(init.headers).not.toHaveProperty('Authorization')
      expect(init.headers).not.toHaveProperty('Cookie')
    }
    expect(fetchMock.mock.calls[3][1].headers['Idempotency-Key']).toBe('unchanged-idempotency')
  })

  it.each([
    { session_ref: 'not-valid' }, { expires_at: 'invalid' },
    { expires_at: '2020-01-01T00:00:00Z' }, { expires_at: '2999-01-01T00:00:00Z' },
    { expires_at: '2026-01-01T00:00:00' },
  ])('fails closed on malformed bootstrap response %j', async (changes) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...identity(), ...changes })))
    await expect(bootstrapAgentBrowserSession()).rejects.toMatchObject({ code: 'browser_session_invalid' })
    await expect(getAgentCapabilities()).rejects.toMatchObject({ code: 'browser_session_required' })
  })

  it('does not reset identity or replay messages automatically on an expired cookie', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ detail: { code: 'browser_session_expired', message: 'expired' } }, { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(bootstrapAgentBrowserSession()).rejects.toMatchObject({ code: 'browser_session_expired', retryable: false })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][1].body).toBe('{"reset":false}')
  })

  it('allows only explicit reset and invalidates the previous in-memory identity before retry', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json(identity())).mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetchMock)
    await bootstrapAgentBrowserSession()
    await expect(bootstrapAgentBrowserSession(true)).rejects.toThrow('offline')
    await expect(message()).rejects.toMatchObject({ code: 'browser_session_required' })
    expect(fetchMock.mock.calls[1][1].body).toBe('{"reset":true}')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('blocks requests at the absolute deadline without silently renewing', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json(identity()))
    vi.stubGlobal('fetch', fetchMock)
    await bootstrapAgentBrowserSession()
    vi.advanceTimersByTime(60_000)
    await expect(message()).rejects.toMatchObject({ code: 'browser_session_required' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('preserves direct V2 transport when the feature is disabled', async () => {
    vi.stubEnv('VITE_AGENT_BROWSER_SESSION', 'false')
    const fetchMock = vi.fn().mockResolvedValue(Response.json([]))
    vi.stubGlobal('fetch', fetchMock)
    await getAgentCapabilities()
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty('X-Agent-Session')
  })
})
