<script setup lang="ts">
import { computed, onMounted, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import { useChatStore } from '../stores/chat'
import { useAuthStore } from '../stores/auth'
import { ElMessage } from 'element-plus'
import { chatApi } from '../api/chat'
import ThinkingTimeline from '../components/ThinkingTimeline.vue'

marked.setOptions({
  breaks: true,
  gfm: true,
})

defineOptions({ name: 'ChatView' })

function copyText(text: string) {
  if (!text) return
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success('已复制')
  }).catch(() => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    ElMessage.success('已复制')
  })
}

async function deleteMessage(msg: any) {
  const convId = store.activeConversationId
  if (!convId) return

  // 获取后端消息 ID
  let messageId = msg.id
  if (!messageId) {
    try {
      const res = await chatApi.getConversation(convId)
      const serverMsgs = (res.data.messages || []).filter((m: any) => m.role !== 'tool')
      const matched = serverMsgs.find((m: any) => m.role === msg.role && m.content === msg.content)
      if (matched) messageId = matched.id
    } catch {}
  }

  // 调用后端 API 删除
  if (messageId) {
    await chatApi.deleteMessage(convId, messageId).catch(() => {})
  }

  // 从后端重新加载消息列表刷新显示
  try {
    const res = await chatApi.getConversation(convId)
    store.messages = (res.data.messages || [])
      .filter((m: any) => m.role !== 'tool')
      .map((m: any) => ({
        id: m.id, role: m.role, content: m.content,
        steps: m.steps || [], sources: m.sources || [],
        route_used: m.route_used, aborted: m.aborted || false,
      }))
  } catch {}
}

const store = useChatStore()
const authStore = useAuthStore()

// 每个消息的思考步骤折叠状态：key = message index, value = 是否展开
const stepsExpandedMap = ref<Record<number, boolean>>({})

function toggleSteps(index: number) {
  stepsExpandedMap.value[index] = !stepsExpandedMap.value[index]
}

function isStepsExpanded(index: number): boolean {
  return stepsExpandedMap.value[index] ?? false
}

// 流式思考步骤折叠状态
const streamingStepsExpanded = ref(false)
function toggleStreamingSteps() {
  streamingStepsExpanded.value = !streamingStepsExpanded.value
}

const conversationList = computed(() =>
  store.conversations.map(c => ({
    ...c,
    title: c.title || '新的对话',
  }))
)

async function sendMessage() {
  const text = store.inputText?.trim()
  if (!text) return
  store.inputText = ''
  await store.sendMessage(text)
  await nextTick()
  const container = document.querySelector('.chat-messages')
  if (container) container.scrollTop = container.scrollHeight
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (store.isStreaming) {
      store.stopStreaming()
    }
    sendMessage()
  }
}

function handleRenameKeydown(e: KeyboardEvent, convId: string) {
  if (e.key === 'Enter') {
    e.preventDefault()
    store.renameConversation(convId, store.renameText)
  } else if (e.key === 'Escape') {
    store.cancelRename()
  }
}

function renderMarkdown(content: string): string {
  if (!content) return ''
  return marked.parse(content) as string
}

// ── 页面初始化逻辑：优先从 URL 参数恢复对话历史，禁止自动发起聊天请求 ──
onMounted(async () => {
  store.loadConversations()
  store.loadKBList()

  // 1. 确定 conversation_id：URL参数优先 > localStorage
  const route = useRoute()
  const urlConvId = (route.query.conv as string) || null
  const lsConvId = localStorage.getItem('activeConvId') || null
  const targetConvId = urlConvId || lsConvId

  if (targetConvId) {
    // 有历史对话 → 加载历史消息，不发起任何新聊天请求
    store.activeConversationId = targetConvId
    // 同步写入 URL 和 localStorage
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('conv', targetConvId)
      window.history.replaceState({}, '', url.toString())
    } catch {}
    localStorage.setItem('activeConvId', targetConvId)
    // 加载历史消息（仅在对话列表中有效时才真正渲染）
    try {
      const res = await chatApi.getConversation(targetConvId)
      if (res.data && res.data.messages) {
        store.messages = (res.data.messages || [])
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
        if (res.data.kb_id) {
          store.selectedKbId = res.data.kb_id
        }
      }
    } catch {
      // 对话不存在或已删除，保持空状态
    }
  }
  // 无有效 id → 保持空对话状态，等待用户主动操作
})
</script>

<template>
  <div class="chat-layout">
    <div class="conversation-list">
      <div class="conversation-list-header">
        <el-button type="primary" style="width: 100%" @click="store.newConversation()">
          <el-icon><Plus /></el-icon> 新建对话
        </el-button>
      </div>
      <div class="conversation-list-items" v-loading="store.loadingConversations">
        <div v-for="conv in conversationList" :key="conv.id" class="conversation-item" :class="{ active: conv.id === store.activeConversationId }" @click="store.selectConversation(conv.id)" @dblclick.stop="store.startRename(conv.id, conv.title)">
          <template v-if="store.renamingId === conv.id">
            <el-icon style="margin-right: 8px; flex-shrink: 0"><EditPen /></el-icon>
            <el-input v-model="store.renameText" size="small" style="flex: 1" @keydown="handleRenameKeydown($event, conv.id)" @blur="store.renameConversation(conv.id, store.renameText)" @click.stop />
          </template>
          <template v-else>
            <el-icon style="margin-right: 8px; flex-shrink: 0"><ChatDotRound /></el-icon>
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ conv.title }}</span>
            <span class="rename-hint" @click.stop="store.startRename(conv.id, conv.title)" title="重命名">✎</span>
            <el-popconfirm title="确定要删除这个会话吗？" confirm-button-text="删除" cancel-button-text="取消" @confirm="store.deleteConversation(conv.id)" @click.stop>
              <template #reference>
                <span class="delete-hint" title="删除会话"><el-icon><Delete /></el-icon></span>
              </template>
            </el-popconfirm>
          </template>
        </div>
        <div v-if="store.conversations.length === 0 && !store.loadingConversations" class="conv-empty">暂无对话记录</div>
      </div>
    </div>

    <div class="chat-main-area">
      <div class="chat-container">
        <div class="chat-messages">
          <div v-if="store.messages.length === 0 && !store.isStreaming" class="chat-welcome">
            <el-icon style="font-size: 48px; margin-bottom: 16px"><ChatLineSquare /></el-icon>
            <p style="font-size: 16px">开始与 BaseAgent 对话</p>
            <p style="font-size: 13px; margin-top: 8px; color: #94a3b8">支持知识库检索和智能路由，可调用已配置的工具</p>
          </div>

          <!-- 已发送的消息 -->
          <div v-for="(msg, index) in store.messages" :key="index" class="message-item" :class="msg.role">
            <div class="message-avatar" :class="msg.role">
              <svg v-if="msg.role === 'assistant'" viewBox="0 0 36 36" style="width:36px;height:36px;border-radius:50%;">
                <defs>
                  <linearGradient id="agentGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" style="stop-color:#667eea;stop-opacity:1" />
                    <stop offset="100%" style="stop-color:#764ba2;stop-opacity:1" />
                  </linearGradient>
                </defs>
                <circle cx="18" cy="18" r="18" fill="url(#agentGrad)"/>
                <g transform="translate(9,9) scale(0.5)">
                  <rect x="4" y="10" width="28" height="20" rx="4" fill="white" opacity="0.9"/>
                  <circle cx="18" cy="18" r="6" fill="url(#agentGrad)"/>
                  <circle cx="14" cy="15" r="1.5" fill="white"/>
                  <circle cx="22" cy="15" r="1.5" fill="white"/>
                  <path d="M12 21 Q18 25 24 21" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
                </g>
              </svg>
              <template v-else>
                <div v-if="authStore.avatarUrl" style="width:36px;height:36px;border-radius:50%;overflow:hidden;">
                  <img :src="authStore.avatarUrl" style="width:100%;height:100%;object-fit:cover;" alt="avatar" />
                </div>
                <div v-else class="user-avatar-placeholder">
                  {{ authStore.displayName.charAt(0) || 'U' }}
                </div>
              </template>
            </div>
            <div class="message-content">
              <ThinkingTimeline
                v-if="msg.steps && msg.steps.length > 0"
                :steps="msg.steps"
                :is-streaming="false"
              />
              <div v-if="msg.content" class="markdown-body" v-html="renderMarkdown(msg.content)"></div>
              <div v-if="msg.aborted" class="msg-aborted">
                <span>⏹</span>
                <span>用户终止</span>
              </div>
            </div>
            <!-- 操作栏：Agent 回答在左侧，用户问题在右侧 -->
            <div class="message-actions" :class="msg.role">
              <el-button text class="msg-action-btn" @click="copyText(msg.content || '')" title="复制">
                <svg viewBox="0 0 24 24" style="width: 15px; height: 15px;" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
              </el-button>
              <el-button text class="msg-action-btn" @click="deleteMessage(msg)" title="删除">
                <svg viewBox="0 0 24 24" style="width: 15px; height: 15px;" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
              </el-button>
            </div>
          </div>

          <!-- 流式消息 -->
          <div v-if="store.isStreaming" class="message-item assistant">
            <div class="message-avatar assistant">
              <svg viewBox="0 0 36 36" style="width: 36px; height: 36px; border-radius: 50%;">
                <defs>
                  <linearGradient id="agentGrad2" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" style="stop-color:#667eea;stop-opacity:1" />
                    <stop offset="100%" style="stop-color:#764ba2;stop-opacity:1" />
                  </linearGradient>
                </defs>
                <circle cx="18" cy="18" r="18" fill="url(#agentGrad2)"/>
                <g transform="translate(9,9) scale(0.5)">
                  <rect x="4" y="10" width="28" height="20" rx="4" fill="white" opacity="0.9"/>
                  <circle cx="18" cy="18" r="6" fill="url(#agentGrad2)"/>
                  <circle cx="14" cy="16" r="1.5" fill="white"/>
                  <circle cx="22" cy="16" r="1.5" fill="white"/>
                  <path d="M12 22 Q18 26 24 22" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
                </g>
              </svg>
            </div>
            <div class="message-content">
              <ThinkingTimeline
                v-if="store.thinkingSteps.length > 0"
                :steps="store.thinkingSteps"
                :is-streaming="true"
              />
              <div v-if="store.streamingMessage" class="markdown-body" v-html="renderMarkdown(store.streamingMessage)"></div>
              <div v-else class="thinking-placeholder">
                <div class="loading-dots">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="chat-input-area">
          <div class="kb-selector-row">
            <el-icon style="font-size: 15px; color: #6366f1; flex-shrink: 0;"><Collection /></el-icon>
            <el-select
              v-model="store.selectedKbId"
              placeholder="关联知识库 (选填)"
              clearable
              size="small"
              class="kb-select-compact"
              @change="store.updateConversationKb"
            >
              <el-option
                v-for="kb in store.knowledgeBases"
                :key="kb.id"
                :label="kb.name"
                :value="kb.id"
                :disabled="kb.status !== 'ready'"
              >
                <div style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
                  <span>
                    <span v-if="kb.status !== 'ready'" style="margin-right: 6px; font-size: 12px;" :style="{ color: kb.status === 'error' ? '#ef4444' : '#f59e0b' }">
                      {{ kb.status === 'error' ? '⚠' : '⏳' }}
                    </span>
                    <span>{{ kb.name }}</span>
                  </span>
                  <el-tag
                    :type="kb.status === 'ready' ? 'success' : kb.status === 'error' ? 'danger' : 'warning'"
                    size="small"
                    style="margin-left: 8px; flex-shrink: 0;"
                  >
                    {{ kb.status === 'ready' ? '已就绪' : kb.status === 'error' ? '异常' : '处理中' }}
                  </el-tag>
                </div>
              </el-option>
            </el-select>
            <div v-if="store.selectedKbId" class="kb-selected-tag">
              <el-icon style="color: #10b981; font-size: 13px;"><CircleCheckFilled /></el-icon>
              <span>{{ store.knowledgeBases.find(kb => kb.id === store.selectedKbId)?.name || '' }}</span>
            </div>
          </div>

          <div class="chat-input-row">
            <el-input v-model="store.inputText" type="textarea" :rows="2" placeholder="输入您的问题，Enter 发送，Shift+Enter 换行" @keydown="handleKeydown" resize="none" class="chat-input" />
            <el-button v-if="store.isStreaming" type="danger" class="stop-btn" @click="store.stopStreaming()">
              <svg viewBox="0 0 24 24" style="width: 20px; height: 20px;">
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5" />
                <circle cx="12" cy="12" r="4" fill="currentColor" />
              </svg>
            </el-button>
            <el-button v-else type="primary" :icon="'Promotion'" :disabled="!store.inputText?.trim()" class="send-btn" @click="sendMessage">发送</el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  gap: 0;
}

.chat-main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
}

/* ── Markdown Body ── */
.markdown-body :deep(p) { margin: 0 0 8px 0; line-height: 1.7; }
.markdown-body :deep(strong) { font-weight: 700; color: inherit; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3), .markdown-body :deep(h4) { margin: 12px 0 6px 0; font-weight: 600; line-height: 1.4; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 20px; margin: 4px 0 8px 0; }
.markdown-body :deep(li) { margin: 2px 0; line-height: 1.6; }
.markdown-body :deep(code) { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; color: #1e293b; }
.markdown-body :deep(pre) { background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; overflow-y: auto; margin: 8px 0; max-height: 400px; }
.markdown-body :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.markdown-body :deep(blockquote) { border-left: 3px solid #2563eb; padding: 4px 12px; margin: 8px 0; color: #475569; background: #f8fafc; border-radius: 0 6px 6px 0; }
.markdown-body :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; }
.markdown-body :deep(th) { background: #f8fafc; font-weight: 600; }
.markdown-body :deep(a) { color: #2563eb; text-decoration: none; }
.markdown-body :deep(a:hover) { text-decoration: underline; }

/* ── Message Content ── */
.message-content {
  overflow-x: auto;
  word-break: break-word;
  max-width: 100%;
  min-width: 0;
}
.message-content :deep(pre) {
  white-space: pre-wrap;
  word-break: break-word;
}
.message-content :deep(pre code) {
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── Rename / Delete hints ── */
.rename-hint { display: none; cursor: pointer; color: #94a3b8; font-size: 14px; padding: 0 4px; flex-shrink: 0; }
.delete-hint { display: none; cursor: pointer; color: #94a3b8; font-size: 14px; padding: 0 4px; flex-shrink: 0; }
.conversation-item:hover .rename-hint, .conversation-item:hover .delete-hint { display: inline; }
.rename-hint:hover { color: var(--primary-color); }
.delete-hint:hover { color: var(--danger-color); }

/* ── Conversation sidebar — distinct from chat area ── */
.chat-layout {
  display: flex;
  height: 100%;
  gap: 0;
}
.conversation-list {
  width: 260px;
  flex-shrink: 0;
  background: #F1F5F9;
  border-right: 2px solid #CBD5E1;
  display: flex;
  flex-direction: column;
}
.conversation-list-header {
  padding: var(--spacing-md);
  border-bottom: 2px solid #CBD5E1;
  background: #EEF2FF;
}
.conversation-list-items {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-xs);
}
.conversation-item {
  padding: 10px 12px;
  border-radius: var(--radius-lg);
  cursor: pointer;
  margin-bottom: 4px;
  font-size: 13px;
  transition: all var(--transition-fast);
  border: 1px solid transparent;
  color: #475569;
  font-weight: 500;
}
.conversation-item:hover {
  background: #fff;
  border-color: #CBD5E1;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
  color: var(--text-primary);
}
.conversation-item.active {
  background: #DBEAFE;
  border-color: #93C5FD;
  color: #1D4ED8;
  font-weight: 600;
  box-shadow: 0 0 8px rgba(59, 130, 246, 0.12);
}

/* ── Empty conversation ── */
.conv-empty { text-align: center; color: #909399; padding: 20px; font-size: 13px; }

/* ── Chat welcome ── */
.chat-welcome { text-align: center; color: #909399; margin-top: 120px; }

/* ── User avatar placeholder ── */
.user-avatar-placeholder {
  width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; color: #fff; background: var(--primary-color);
}

/* ── Gemini 风格气泡操作栏 ── */
.message-item {
  position: relative;
  margin-bottom: 38px;
}
.message-actions {
  position: absolute;
  bottom: -32px;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.2s ease;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 2px;
  box-shadow: var(--shadow-sm);
  z-index: 5;
}
.message-actions.assistant { left: 52px; }
.message-actions.user { right: 52px; }
.message-item:hover .message-actions { opacity: 1; }
.msg-action-btn {
  color: #94a3b8 !important;
  border-radius: 6px;
  width: 28px;
  height: 28px;
  padding: 0;
}
.msg-action-btn:hover {
  color: var(--primary-color) !important;
  background: #eff6ff;
}

/* ── Steps ── */
.steps-section { margin-bottom: 8px; }
.steps-arrow {
  font-size: 10px;
  transition: transform 0.2s;
  color: var(--primary-color);
  font-weight: bold;
}
.steps-arrow.expanded { transform: rotate(90deg); }
.steps-label { font-weight: 500; }
.steps-list {
  border-left: 2px solid var(--primary-color);
  padding-left: 12px;
  margin-top: 4px;
  margin-left: 4px;
}
.thinking-step {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 13px;
  color: #64748b;
}
.thinking-step.completed { color: #64748b; }
.step-icon { font-size: 11px; color: var(--primary-color); }
.step-icon.loading-icon { font-size: 14px; }

/* ── Aborted message ── */
.msg-aborted {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  display: flex;
  align-items: center;
  gap: 4px;
  border-top: 1px dashed #e0e0e0;
  padding-top: 6px;
}

/* ── Thinking placeholder ── */
.thinking-placeholder {
  color: #909399;
  font-size: 20px;
  letter-spacing: 2px;
}

/* ── Chat Input ── */
.chat-input :deep(.el-textarea__inner) {
  background: #f8fafc !important;
  border: 1px solid var(--border-color) !important;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s !important;
  border-radius: 8px !important;
}
.chat-input :deep(.el-textarea__inner:focus) {
  background: #fff !important;
  border-color: var(--primary-color) !important;
  box-shadow: 0 0 0 2px var(--primary-light) !important;
}

.chat-input-row {
  display: flex;
  gap: 8px;
}
.stop-btn {
  height: auto;
  width: 56px;
  padding: 8px;
  border-radius: var(--radius-lg);
}
.send-btn {
  height: auto;
  border-radius: var(--radius-lg);
}

/* ── KB Selector ── */
.kb-selector-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 10px;
  background: #f8fafc;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  transition: border-color 0.2s;
}
.kb-selector-row:focus-within {
  border-color: #a5b4fc;
}
.kb-select-compact {
  flex: 1;
  min-width: 160px;
}
.kb-select-compact :deep(.el-input__wrapper) {
  background: #fff !important;
  border-radius: 6px !important;
  border: 1px solid #d1d5db !important;
  box-shadow: none !important;
  transition: border-color 0.2s !important;
}
.kb-select-compact :deep(.el-input__wrapper:hover) {
  border-color: var(--primary-color) !important;
}
.kb-select-compact :deep(.el-input__wrapper.is-focus) {
  border-color: var(--primary-color) !important;
  box-shadow: 0 0 0 2px var(--primary-light) !important;
}
.kb-selected-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #059669;
  background: #ecfdf5;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
  flex-shrink: 0;
}
</style>