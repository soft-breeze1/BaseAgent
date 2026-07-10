import apiClient from './client'

export interface ChatRequest {
  message: string
  kb_id?: string
  conversation_id?: string
}

export interface RAGSource {
  document_id: string
  filename: string
  content: string
  score: number
}

export interface ChatResponse {
  answer: string
  sources: RAGSource[]
  route_used: string
  conversation_id: string
  created_at: string
}

export interface ConversationItem {
  id: string
  title: string
  kb_id?: string
  created_at: string
  updated_at: string
}

export interface AbortMessageRequest {
  conversation_id: string
  content?: string
  steps?: string[]
  sources?: any[]
}

export const chatApi = {
  // Non-streaming
  send(params: ChatRequest) {
    return apiClient.post<ChatResponse>('/chat/send', params)
  },

  /**
   * v8.0: Streaming via fetch POST — 兼容 OpenAI SSE 格式 + BaseAgent 扩展事件
   * 
   * 支持的线格式:
   *   data: {"choices":[...]}    → OpenAI 兼容 token 块（从 delta.content 提取）
   *   data: [DONE]               → 流结束标记
   *   event: step\ndata:{...}    → BaseAgent 步骤事件
   *   event: meta\ndata:{...}    → BaseAgent 元数据事件
   *   : heartbeat 12345          → 心跳注释行（忽略）
   *   event: error\ndata:{...}   → 错误事件
   * 
   * 输出给 store 的格式（向后兼容）:
   *   { type: 'token', content: '...' }
   *   { type: 'step', content: '...' }
   *   { type: 'meta', route: '...', sources: [...] }
   *   { type: 'done', conversation_id: '...' }
   */
  stream(params: ChatRequest, onMessage: (data: any) => void, onError: () => void): AbortController {
    const controller = new AbortController()
    const token = localStorage.getItem('token')

    fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(params),
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        onError()
        return
      }
      const reader = response.body?.getReader()
      if (!reader) {
        onError()
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let currentEventType = ''  // 追踪当前 event 类型

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        
        // SSE 协议：事件以 \n\n 分隔
        const parts = buffer.split('\n\n')
        // 保留未完成的最后一个部分
        buffer = parts.pop() || ''

        for (const part of parts) {
          const lines = part.split('\n')
          let eventType = currentEventType
          let dataLine = ''

          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed.startsWith('event: ')) {
              eventType = trimmed.slice(7).trim()
            } else if (trimmed.startsWith('data: ')) {
              dataLine = trimmed.slice(6).trim()
            } else if (trimmed.startsWith(': ')) {
              // 心跳/注释行，忽略
            }
          }

          if (!dataLine) continue

          // Handle [DONE] marker
          if (dataLine === '[DONE]') {
            onMessage({ type: 'done' })
            continue
          }

          try {
            const parsed = JSON.parse(dataLine)

            if (eventType === 'step') {
              // BaseAgent 扩展事件：步骤
              onMessage({
                type: 'step',
                content: parsed.content || '',
              })
            } else if (eventType === 'meta') {
              // BaseAgent 扩展事件：元数据
              onMessage({
                type: 'meta',
                route: parsed.route || 'llm',
                sources: parsed.sources || [],
              })
            } else if (eventType === 'error') {
              // 错误事件
              onMessage({
                type: 'step',
                content: parsed.error?.message || '发生错误',
              })
            } else if (eventType === 'done' && parsed.conversation_id) {
              // 后端主动推送的 done 事件含 conversation_id
              onMessage({
                type: 'done',
                conversation_id: parsed.conversation_id,
              })
            } else if (parsed.object === 'chat.completion.chunk') {
              // OpenAI 兼容格式
              const choices = parsed.choices || []
              for (const choice of choices) {
                const delta = choice.delta || {}
                const content = delta.content || ''
                const finishReason = choice.finish_reason
                
                if (finishReason) {
                  // finish_reason 标记结束，但这里不做处理
                  // [DONE] 标记会稍后到来
                }
                
                if (content) {
                  onMessage({ type: 'token', content })
                }
              }
            } else {
              // 回退：尝试标准 token 格式（向后兼容）
              if (dataLine.includes('"type":"token"') || dataLine.includes("'type':'token'")) {
                onMessage({ type: 'token', content: parsed.content || '' })
              }
            }
          } catch {
            // 忽略 JSON 解析错误
          }
        }
      }

      // Process any remaining data in buffer
      if (buffer.trim()) {
        const trimmed = buffer.trim()
        if (trimmed === 'data: [DONE]' || trimmed === '[DONE]') {
          onMessage({ type: 'done' })
        }
      }
    }).catch((err) => {
      if (err.name !== 'AbortError') {
        console.error('[SSE] Stream error:', err)
        onError()
      }
    })

    return controller
  },

  // Save aborted/interrupted message
  saveAbortedMessage(params: AbortMessageRequest) {
    return apiClient.post('/chat/abort', params)
  },

  // Conversations
  createConversation() {
    return apiClient.post<ConversationItem>('/chat/conversations')
  },

  listConversations() {
    return apiClient.get<ConversationItem[]>('/chat/conversations')
  },

  getConversation(id: string) {
    return apiClient.get<{ kb_id?: string; messages: any[] }>(`/chat/conversations/${id}`)
  },

  deleteConversation(id: string) {
    return apiClient.delete(`/chat/conversations/${id}`)
  },

  deleteMessage(conversationId: string, messageId: string) {
    return apiClient.delete(`/chat/conversations/${conversationId}/messages/${messageId}`)
  },

  renameConversation(id: string, title: string) {
    return apiClient.put(`/chat/conversations/${id}`, { title })
  },

  /** 更新会话绑定的知识库 ID（即时持久化到数据库） */
  updateConversationKb(id: string, kb_id: string | null) {
    return apiClient.post(`/chat/conversations/${id}/kb`, { kb_id })
  },

}
