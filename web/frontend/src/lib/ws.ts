/** WebSocket 事件类型定义 */

// 任务状态结构
export interface TaskState {
  goal: string
  completed: string[]
  pending: string[]
  tools_used: string[]
  key_findings: string[]
  missing_info: string[]
}

// 服务端 → 客户端
export type ServerEvent =
  | { type: 'text_delta'; content: string }
  | { type: 'reasoning_delta'; content: string }
  | { type: 'tool_start'; tool: string; params: Record<string, unknown> }
  | { type: 'tool_end'; tool: string; ok: boolean; summary: string; result: string; data_preview: unknown; warnings: string[]; sources: Array<Record<string, unknown>>; duration: number }
  | { type: 'tool_error'; tool: string; ok: boolean; summary: string; error: string; data_preview: unknown; warnings: string[] }
  | { type: 'task_state'; goal: string; completed: string[]; pending: string[]; tools_used: string[]; key_findings: string[]; missing_info: string[] }
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
  ok?: boolean
  summary?: string
  result?: string
  dataPreview?: unknown
  warnings?: string[]
  sources?: Array<Record<string, unknown>>
  duration?: number
  error?: string
}

// 消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCalls?: ToolCallState[]
  taskState?: TaskState
  isStreaming?: boolean
  timestamp: number
}
