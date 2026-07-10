import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { chatApi, type ConversationItem, type RAGSource } from '../api/chat'
import { knowledgeApi, type KnowledgeBaseItem } from '../api/knowledge'

export interface ChatMessage {
  id?: string          // 后端消息 ID，用于删除
  role: 'user' | 'assistant'
  content: string
  steps?: string[]   // 持久化的思考步骤
  sources?: RAGSource[]
  route_used?: string
  aborted?: boolean  // true = 用户终止/被新问题中断
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<ConversationItem[]>([])
  const loadingConversations = ref(false)
  // 初始化时尝试从 localStorage 恢复 activeConversationId
  const _savedId = localStorage.getItem('activeConvId')
  const activeConversationId = ref<string | null>(_savedId || null)
  const messages = ref<ChatMessage[]>([])
  const inputText = ref('')
  const isStreaming = ref(false)
  const streamingMessage = ref('')
  const thinkingSteps = ref<string[]>([])
  const sending = ref(false)
  const knowledgeBases = ref<KnowledgeBaseItem[]>([])
  const selectedKbId = ref<string | null>(null)
  const renamingId = ref<string | null>(null)
  const renameText = ref('')
  const renaming = ref(false)

  let streamController: AbortController | null = null
  let ragSources: RAGSource[] = []
  let currentAssistantMsgIndex: number | null = null
  let autoScrollTimer: ReturnType<typeof setTimeout> | null = null

  /** Scroll chat to bottom */
  function scrollToBottom() {
    const container = document.querySelector('.chat-messages')
    if (container) container.scrollTop = container.scrollHeight
  }

  /**
   * Save the partial/interrupted assistant content as a message in the list,
   * AND persist it to the backend DB so it survives page refresh.
   */
  function savePartialResponse() {
    if (!isStreaming.value) return

    const msg: ChatMessage = {
      role: 'assistant',
      content: streamingMessage.value || '',
      steps: [...thinkingSteps.value],
      sources: [...ragSources],
      aborted: true,
    }
    messages.value.push(msg)

    // Persist to backend ASAP
    const convId = activeConversationId.value
    if (convId) {
      chatApi.saveAbortedMessage({
        conversation_id: convId,
        content: streamingMessage.value || '',
        steps: [...thinkingSteps.value],
        sources: [...ragSources],
      }).then(() => {
        // 保存成功后从后端重新加载消息以获得服务器端 ID
        _reloadCurrentConvMessages()
      }).catch(() => {})
    }
  }

  /** 从后端重新加载消息，仅用于填充缺失的 id（不替换数组，只补充 id 到已有消息对象） */
  async function _reloadCurrentConvMessages() {
    const convId = activeConversationId.value
    if (!convId) return
    try {
      const res = await chatApi.getConversation(convId)
      const serverMsgs = (res.data.messages || []).filter((m: any) => m.role !== 'tool')
      for (let i = 0; i < messages.value.length && i < serverMsgs.length; i++) {
        const local = messages.value[i]
        const server = serverMsgs[i]
        if (!local.id && server.id) local.id = server.id
      }
    } catch {}
  }

  /**
   * Stop the current stream immediately.
   * Called by stop button → saves partial, aborts, resets.
   */
  function stopStreaming() {
    if (!isStreaming.value || !streamController) return
    savePartialResponse()
    streamController.abort()
    streamController = null
    isStreaming.value = false
    sending.value = false
    streamingMessage.value = ''
    thinkingSteps.value = []
    currentAssistantMsgIndex = null
  }

  async function loadConversations() {
    loadingConversations.value = true
    try {
      const res = await chatApi.listConversations()
      conversations.value = res.data
    } catch {
    } finally {
      loadingConversations.value = false
    }
  }

  async function loadKBList() {
    try {
      const res = await knowledgeApi.listKBs()
      // 显示所有知识库（无论状态），非 ready 的会在 UI 上标记提示
      knowledgeBases.value = res.data
      if (selectedKbId.value && !knowledgeBases.value.find(kb => kb.id === selectedKbId.value)) {
        selectedKbId.value = null
      }
    } catch {
    }
  }

  /** 同步当前对话 ID 到 URL 和 localStorage */
  function _syncConvIdToUrl(id: string | null) {
    // 写入 localStorage
    if (id) {
      localStorage.setItem('activeConvId', id)
    } else {
      localStorage.removeItem('activeConvId')
    }
    // 写入 URL 参数（不触发路由跳转）
    try {
      const url = new URL(window.location.href)
      if (id) {
        url.searchParams.set('conv', id)
      } else {
        url.searchParams.delete('conv')
      }
      window.history.replaceState({}, '', url.toString())
    } catch {}
  }

  async function selectConversation(id: string) {
    activeConversationId.value = id
    _syncConvIdToUrl(id)
    messages.value = []
    streamingMessage.value = ''
    selectedKbId.value = null
    try {
      const res = await chatApi.getConversation(id)
      // 恢复该对话绑定的知识库 ID（从数据库持久化的 kb_id）
      if (res.data.kb_id) {
        selectedKbId.value = res.data.kb_id
      }
      // 过滤掉 tool 角色的消息（原始工具输出仅用于 ReAct 上下文恢复，不应显示给用户）
      // assistant 消息的 steps 字段已包含步骤摘要
      messages.value = (res.data.messages || [])
        .filter((m: any) => m.role !== 'tool')
        .map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          steps: m.steps || [],
          sources: m.sources || [],
          route_used: m.route_used,
          aborted: m.aborted || false,
        }))
    } catch {
    }
    const { nextTick } = await import('vue')
    await nextTick()
    const container = document.querySelector('.chat-messages')
    if (container) container.scrollTop = container.scrollHeight
  }

  async function newConversation() {
    activeConversationId.value = null
    _syncConvIdToUrl(null)
    messages.value = []
    streamingMessage.value = ''
    try {
      const res = await chatApi.createConversation()
      const newConv = res.data
      conversations.value.unshift(newConv)
      activeConversationId.value = newConv.id
      _syncConvIdToUrl(newConv.id)
    } catch {
    }
  }

  async function sendMessage(text: string) {
    if (!text) return

    // --- If currently streaming: save partial response before sending new question ---
    if (isStreaming.value) {
      savePartialResponse()
      if (streamController) {
        streamController.abort()
        streamController = null
      }
      isStreaming.value = false
      sending.value = false
      streamingMessage.value = ''
      thinkingSteps.value = []
      currentAssistantMsgIndex = null
    }

    // Prevent double-send race
    if (sending.value) return

    // Add user message
    messages.value.push({ role: 'user', content: text })

    sending.value = true
    isStreaming.value = true
    streamingMessage.value = ''
    thinkingSteps.value = []

    let fullAnswer = ''
    ragSources = []

    streamController = chatApi.stream(
      {
        message: text,
        kb_id: selectedKbId.value ?? undefined,
        conversation_id: activeConversationId.value ?? undefined,
      },
      (data) => {
        if (data.type === 'step') {
          thinkingSteps.value = [...thinkingSteps.value, data.content]
        } else if (data.type === 'meta') {
          ragSources = data.sources || []
        } else if (data.type === 'token') {
          streamingMessage.value += data.content
          fullAnswer += data.content
          // Auto-scroll during streaming
          if (!autoScrollTimer) {
            autoScrollTimer = setTimeout(() => {
              scrollToBottom()
              autoScrollTimer = null
            }, 30)
          }
        } else if (data.type === 'done') {
          // ── 防止 data: [DONE] 和 event: done 重复触发 ──
          if (!isStreaming.value) {
            // 已经处理过 done 事件，跳过重复
            return
          }
          if (autoScrollTimer) {
            clearTimeout(autoScrollTimer)
            autoScrollTimer = null
          }
          scrollToBottom()
          streamController = null

          activeConversationId.value = data.conversation_id || activeConversationId.value
          _syncConvIdToUrl(activeConversationId.value)

          // 先将完整回答推入消息列表（确保界面立即显示），再异步补充后端 ID
          messages.value.push({
            role: 'assistant',
            content: fullAnswer,
            steps: [...thinkingSteps.value],
            sources: [...ragSources],
          })

          // 异步从后端加载 ID 填充到已有消息对象中（用于删除操作）
          _reloadCurrentConvMessages()

          streamingMessage.value = ''
          thinkingSteps.value = []
          isStreaming.value = false
          sending.value = false
          currentAssistantMsgIndex = null

          loadConversations()
        }
      },
      () => {
        streamController = null
        if (fullAnswer) {
          messages.value.push({
            role: 'assistant',
            content: fullAnswer,
            steps: [...thinkingSteps.value],
            sources: [...ragSources],
          })
        } else {
          messages.value.push({ role: 'assistant', content: '抱歉，请求失败，请稍后重试。' })
        }
        streamingMessage.value = ''
        thinkingSteps.value = []
        isStreaming.value = false
        sending.value = false
        currentAssistantMsgIndex = null
        scrollToBottom()
      }
    )
  }

  async function deleteConversation(id: string) {
    try {
      await chatApi.deleteConversation(id)
      if (activeConversationId.value === id) {
        activeConversationId.value = null
        messages.value = []
      }
      await loadConversations()
    } catch {
    }
  }

  async function renameConversation(id: string, newTitle: string) {
    if (!newTitle || renaming.value) return
    renaming.value = true
    try {
      await chatApi.renameConversation(id, newTitle)
      const conv = conversations.value.find((c: ConversationItem) => c.id === id)
      if (conv) conv.title = newTitle
    } catch {
      await loadConversations()
      ElMessage.error('重命名失败，请稍后重试')
    } finally {
      renaming.value = false
      renamingId.value = null
      renameText.value = ''
    }
  }

  function startRename(convId: string, currentTitle: string) {
    renamingId.value = convId
    renameText.value = currentTitle
  }

  function cancelRename() {
    renamingId.value = null
    renameText.value = ''
  }

  /** 即时持久化当前会话的知识库选择到数据库 */
  async function updateConversationKb(kbId: string | null) {
    const convId = activeConversationId.value
    if (!convId) return
    selectedKbId.value = kbId
    try {
      await chatApi.updateConversationKb(convId, kbId)
    } catch {
      // 静默失败 — 选择仍然在本地生效，下次发送消息时也会持久化
    }
  }

  return {
    conversations,
    loadingConversations,
    activeConversationId,
    messages,
    inputText,
    isStreaming,
    streamingMessage,
    thinkingSteps,
    sending,
    knowledgeBases,
    selectedKbId,
    renamingId,
    renameText,
    renaming,
    loadConversations,
    loadKBList,
    selectConversation,
    newConversation,
    sendMessage,
    stopStreaming,
    deleteConversation,
    renameConversation,
    startRename,
    cancelRename,
    updateConversationKb,
  }
})