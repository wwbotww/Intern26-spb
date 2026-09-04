import type {
  AgentFailure,
  AgentMessageRequest,
  AgentResponse,
  AgentResult,
  PublicIntent,
  RequiredInput,
} from './generated/agent-api'


export type AgentMessageState =
  | 'preparing'
  | 'streaming'
  | 'waiting_user'
  | 'done'
  | 'partial'
  | 'handoff'
  | 'failed'
  | 'error'
  | 'aborted'


export interface AgentUiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  state: AgentMessageState
  createdAt: number
  intent?: PublicIntent | null
  nextAction?: AgentResponse['next_action']
  requiredInputs: RequiredInput[]
  result?: AgentResult | null
  failure?: AgentFailure | null
  warnings: string[]
  statusText?: string
  requestId?: string
  error?: string
}


export interface PendingAgentRequest {
  payload: Omit<AgentMessageRequest, 'stream'>
  idempotencyKey: string
  requestId: string
  assistantMessageId: string
}


export interface AgentSessionSnapshot {
  version: 1
  conversationId: string | null
  messages: AgentUiMessage[]
  pendingRequest: PendingAgentRequest | null
  selectedIntent: PublicIntent | null
  updatedAt: number
}
