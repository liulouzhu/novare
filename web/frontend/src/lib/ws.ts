/** WebSocket 事件类型定义 */

// 服务端 → 客户端
export type ServerEvent =
  | { type: 'text_delta'; content: string }
  | { type: 'reasoning_delta'; content: string }
  | { type: 'tool_start'; tool: string; params: Record<string, unknown> }
  | { type: 'tool_end'; tool: string; result: string; duration: number }
  | { type: 'tool_error'; tool: string; error: string }
  | { type: 'done'; usage?: Record<string, number> }
  | { type: 'error'; message: string }

// 客户端 → 服务端
export type ClientMessage =
  | { type: 'send'; content: string }
  | { type: 'send_with_refs'; content: string; references: Array<{ type: string; id: string; title?: string }> }

// 工具调用状态
export interface ToolCallState {
  id: string
  name: string
  params: Record<string, unknown>
  status: 'running' | 'success' | 'error'
  result?: string
  duration?: number
  error?: string
}

// 消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCalls?: ToolCallState[]
  isStreaming?: boolean
  timestamp: number
}
