import { describe, expect, it } from 'vitest'

import {
  AGENT_SESSION_KEY,
  clearAgentSession,
  loadAgentSession,
  saveAgentSession,
} from './agent-session'
import type { AgentSessionSnapshot } from './agent-ui-model'


class MemoryStorage {
  values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }
}


const snapshot: AgentSessionSnapshot = {
  version: 1,
  conversationId: 'conversation-1',
  messages: [
    {
      id: 'message-1',
      role: 'assistant',
      content: '请提供邮件号。',
      state: 'waiting_user',
      createdAt: 1,
      requiredInputs: [
        { name: 'mail_no', label: '邮件号', type: 'string' },
      ],
      warnings: [],
    },
  ],
  pendingRequest: {
    payload: { message: '帮我查邮件' },
    idempotencyKey: 'idempotency-1',
    requestId: 'request-1',
    assistantMessageId: 'message-1',
  },
  selectedIntent: 'tracking',
  updatedAt: 2,
}


describe('Agent session persistence', () => {
  it('round-trips resumable workflow and idempotency state', () => {
    const storage = new MemoryStorage()
    saveAgentSession(snapshot, storage)

    expect(loadAgentSession(storage, 3)).toEqual(snapshot)

    clearAgentSession(storage)
    expect(storage.values.has(AGENT_SESSION_KEY)).toBe(false)
  })

  it('rejects corrupted, oversized, or unknown-version state', () => {
    const storage = new MemoryStorage()
    storage.values.set(AGENT_SESSION_KEY, '{bad-json')
    expect(loadAgentSession(storage, 3)).toBeNull()

    storage.values.set(
      AGENT_SESSION_KEY,
      JSON.stringify({ ...snapshot, version: 2 }),
    )
    expect(loadAgentSession(storage, 3)).toBeNull()

    storage.values.set(
      AGENT_SESSION_KEY,
      JSON.stringify({
        ...snapshot,
        messages: Array.from({ length: 101 }, () => snapshot.messages[0]),
      }),
    )
    expect(loadAgentSession(storage, 3)).toBeNull()
  })

  it('removes a local snapshot after the server-aligned TTL', () => {
    const storage = new MemoryStorage()
    saveAgentSession(snapshot, storage)

    expect(loadAgentSession(storage, 30 * 60 * 1000 + 3)).toBeNull()
    expect(storage.values.has(AGENT_SESSION_KEY)).toBe(false)
  })
})
