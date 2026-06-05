/** 聊天交互 Hook — 连接 WebSocket 事件到 chatStore */

import { useCallback, useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useChatStore } from '@/stores/chatStore'
import { type ServerEvent, type ToolCallState } from '@/lib/ws'
import { generateId } from '@/lib/utils'

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
    isStreaming,
    streamingMessageId,
  } = useChatStore()

  const assistantMsgId = useRef<string | null>(null)
  const toolCounter = useRef(0)

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
        break

      case 'error':
        if (assistantMsgId.current) {
          appendText(assistantMsgId.current, `\n\n❌ 错误: ${event.message}`)
          finishMessage(assistantMsgId.current)
          assistantMsgId.current = null
        }
        setStreaming(false)
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
