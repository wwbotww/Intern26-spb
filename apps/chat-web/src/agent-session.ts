import type {
  AgentSessionSnapshot,
  AgentUiMessage,
  PendingAgentRequest,
} from './agent-ui-model'


export const AGENT_SESSION_KEY = 'spb-agent-session-v1'
const MAX_MESSAGES = 100
const MAX_SESSION_AGE_MS = 30 * 60 * 1000


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}


function validMessage(value: unknown): value is AgentUiMessage {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    (value.role === 'user' || value.role === 'assistant') &&
    typeof value.content === 'string' &&
    typeof value.state === 'string' &&
    typeof value.createdAt === 'number' &&
    Array.isArray(value.requiredInputs) &&
    Array.isArray(value.warnings)
  )
}


function validPending(value: unknown): value is PendingAgentRequest {
  if (!isRecord(value) || !isRecord(value.payload)) return false
  return (
    typeof value.idempotencyKey === 'string' &&
    typeof value.requestId === 'string' &&
    typeof value.assistantMessageId === 'string'
  )
}


export function loadAgentSession(
  storage: Pick<Storage, 'getItem' | 'removeItem'> = localStorage,
  now = Date.now(),
): AgentSessionSnapshot | null {
  try {
    const raw = storage.getItem(AGENT_SESSION_KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (!isRecord(value) || value.version !== 1) return null
    if (
      value.conversationId !== null &&
      typeof value.conversationId !== 'string'
    ) {
      return null
    }
    if (!Array.isArray(value.messages) || !value.messages.every(validMessage)) {
      return null
    }
    if (value.messages.length > MAX_MESSAGES) return null
    if (value.pendingRequest !== null && !validPending(value.pendingRequest)) {
      return null
    }
    if (
      value.selectedIntent !== null &&
      ![
        'policy',
        'device_price',
        'tracking',
        'delivery_time',
        'postage',
      ].includes(String(value.selectedIntent))
    ) {
      return null
    }
    if (typeof value.updatedAt !== 'number') return null
    if (
      value.updatedAt > now + 60_000 ||
      now - value.updatedAt > MAX_SESSION_AGE_MS
    ) {
      storage.removeItem(AGENT_SESSION_KEY)
      return null
    }
    return value as unknown as AgentSessionSnapshot
  } catch {
    return null
  }
}


export function saveAgentSession(
  snapshot: AgentSessionSnapshot,
  storage: Pick<Storage, 'setItem'> = localStorage,
): void {
  try {
    storage.setItem(AGENT_SESSION_KEY, JSON.stringify(snapshot))
  } catch {
    // Storage can be disabled or full; the active in-memory session still works.
  }
}


export function clearAgentSession(
  storage: Pick<Storage, 'removeItem'> = localStorage,
): void {
  try {
    storage.removeItem(AGENT_SESSION_KEY)
  } catch {
    // Clearing the in-memory session remains useful when storage is unavailable.
  }
}
