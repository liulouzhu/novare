/** 聊天交互 Hook — 连接 WebSocket 事件到 chatStore */

import { useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useChatStore } from '@/stores/chatStore'
import { useSessionStore } from '@/stores/sessionStore'
import { type ServerEvent, type ToolCallState, type Message } from '@/lib/ws'
import { generateId } from '@/lib/utils'
import { fetchSession } from '@/lib/api'

/** 将后端 OpenAI 格式的消息转为前端 Message 格式 */
function convertBackendMessages(raw: Array<{ role: string; content: string; tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>; tool_call_id?: string }>): Message[] {
  const messages: Message[] = []
  let toolCallMap: Record<string, { name: string; args: Record<string, unknown> }> = {}

  for (const msg of raw) {
    if (msg.role === 'tool') {
      // tool result — 关联到之前的 tool call
      const tc = toolCallMap[msg.tool_call_id || '']
      if (tc) {
        // 找到上一条 assistant 消息，给它的 toolCall 补上 result
        for (let i = messages.length - 1; i >= 0; i--) {
          const m = messages[i]
          if (m.role === 'assistant' && m.toolCalls) {
            const idx = m.toolCalls.findIndex((t) => t.name === tc.name && t.status === 'running')
            if (idx !== -1) {
              const isError = msg.content.startsWith('Error') || msg.content.startsWith('错误') || msg.content.startsWith('搜索失败')
              m.toolCalls[idx] = {
                ...m.toolCalls[idx],
                status: isError ? 'error' : 'success',
                result: msg.content.slice(0, 500),
                error: isError ? msg.content : undefined,
              }
            }
            break
          }
        }
      }
      continue
    }

    if (msg.role === 'user') {
      messages.push({
        id: generateId(),
        role: 'user',
        content: msg.content,
        timestamp: Date.now(),
      })
    } else if (msg.role === 'assistant') {
      const toolCalls: ToolCallState[] = []
      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          let args: Record<string, unknown> = {}
          try { args = JSON.parse(tc.function.arguments) } catch {}
          toolCalls.push({
            id: tc.id,
            name: tc.function.name,
            params: args,
            status: 'running', // 后续会被 tool result 更新
          })
          toolCallMap[tc.id] = { name: tc.function.name, args }
        }
      }
      messages.push({
        id: generateId(),
        role: 'assistant',
        content: msg.content || '',
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        timestamp: Date.now(),
      })
    }
  }

  return messages
}

export function useChat(sessionId: string) {
  const {
    getMessages,
    addUserMessage,
    startAssistantMessage,
    appendText,
    addToolCall,
    updateToolCall,
    finishMessage,
    setStreaming,
    setMessages,
    isStreaming,
    streamingMessageId,
  } = useChatStore()

  const assistantMsgId = useRef<string | null>(null)
  const toolCounter = useRef(0)

  // 加载历史消息
  useEffect(() => {
    const existing = getMessages(sessionId)
    if (existing.length > 0) return // 已有消息，不重复加载

    fetchSession(sessionId)
      .then((detail) => {
        if (detail.messages.length > 0) {
          const converted = convertBackendMessages(detail.messages)
          setMessages(sessionId, converted)
        }
      })
      .catch(() => {
        // 新会话或加载失败，忽略
      })
  }, [sessionId])

  const handleEvent = useCallback((event: ServerEvent) => {
    switch (event.type) {
      case 'text_delta':
        if (assistantMsgId.current) {
          appendText(assistantMsgId.current, event.content)
        }
        break

      case 'tool_start': {
        if (assistantMsgId.current) {
          toolCounter.current++
          const tool: ToolCallState = {
            id: `tool-${toolCounter.current}`,
            name: event.tool,
            params: event.params,
            status: 'running',
          }
          addToolCall(assistantMsgId.current, tool)
        }
        break
      }

      case 'tool_end':
        if (assistantMsgId.current) {
          updateToolCall(assistantMsgId.current, event.tool, {
            status: 'success',
            result: event.result,
            duration: event.duration,
          })
        }
        break

      case 'tool_error':
        if (assistantMsgId.current) {
          updateToolCall(assistantMsgId.current, event.tool, {
            status: 'error',
            error: event.error,
          })
        }
        break

      case 'done':
        if (assistantMsgId.current) {
          finishMessage(assistantMsgId.current)
          assistantMsgId.current = null
        }
        setStreaming(false)
        // 刷新会话列表（新会话可能有了消息）
        useSessionStore.getState().loadSessions()
        break

      case 'error':
        if (assistantMsgId.current) {
          appendText(assistantMsgId.current, `\n\n❌ 错误: ${event.message}`)
          finishMessage(assistantMsgId.current)
          assistantMsgId.current = null
        }
        setStreaming(false)
        useSessionStore.getState().loadSessions()
        break
    }
  }, [appendText, addToolCall, updateToolCall, finishMessage, setStreaming])

  const { connected, sendMessage } = useWebSocket({ sessionId, onEvent: handleEvent })

  const send = useCallback((content: string, references?: Array<{ type: string; id: string; title?: string }>) => {
    if (!content.trim() || isStreaming) return

    // 添加用户消息
    addUserMessage(sessionId, content)

    // 创建 assistant 消息占位
    assistantMsgId.current = startAssistantMessage(sessionId)
    toolCounter.current = 0
    setStreaming(true)

    // 发送 WebSocket 消息
    if (references && references.length > 0) {
      sendMessage({ type: 'send_with_refs', content, references })
    } else {
      sendMessage({ type: 'send', content })
    }
  }, [sessionId, isStreaming, addUserMessage, startAssistantMessage, setStreaming, sendMessage])

  return {
    messages: getMessages(sessionId),
    isStreaming,
    connected,
    send,
  }
}
