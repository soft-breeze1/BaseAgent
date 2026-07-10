<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { InfoFilled, EditPen } from '@element-plus/icons-vue'
import { marked } from 'marked'
import { systemPromptApi, type SystemPromptItem } from '../api/systemPrompt'
import { ElMessage, ElMessageBox } from 'element-plus'

marked.setOptions({ breaks: true, gfm: true })

const loading = ref(false)
const saving = ref(false)
const resetting = ref(false)
const editing = ref(false)

const promptData = ref<SystemPromptItem | null>(null)
const content = ref('')
const originalContent = ref('')

async function loadPrompt() {
  loading.value = true
  try {
    const res = await systemPromptApi.get()
    promptData.value = res.data
    content.value = res.data.content
    originalContent.value = res.data.content
  } catch {
    ElMessage.error('加载系统提示词失败')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!content.value.trim()) {
    ElMessage.warning('提示词内容不能为空')
    return
  }
  saving.value = true
  try {
    const res = await systemPromptApi.update(content.value)
    if (res.data.success) {
      ElMessage({
        type: 'success',
        message: res.data.message || '系统提示词已更新，将应用于所有新对话',
        duration: 4000,
      })
      originalContent.value = content.value
      editing.value = false
    }
  } catch {
    ElMessage.error('保存失败，请重试')
  } finally {
    saving.value = false
  }
}

async function handleReset() {
  try {
    await ElMessageBox.confirm(
      '确定要重置为默认系统提示词吗？此操作不可撤销。',
      '确认重置',
      { confirmButtonText: '确定重置', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    const res = await systemPromptApi.reset()
    if (res.data.success) {
      ElMessage({
        type: 'success',
        message: res.data.message || '已重置为默认系统提示词',
        duration: 4000,
      })
      content.value = res.data.content
      originalContent.value = res.data.content
      editing.value = false
    }
  } catch {
    ElMessage.error('重置失败，请重试')
  } finally {
    resetting.value = false
  }
}

const renderedContent = computed(() => {
  if (!content.value) return '<p style="color: #94a3b8; font-style: italic;">暂无内容</p>'
  return marked.parse(content.value) as string
})

onMounted(() => { loadPrompt() })
</script>

<template>
  <div class="system-prompt-page">
    <div class="page-header">
      <h2>⚙️ 全局系统提示词</h2>
      <p class="page-desc">设置 AI 助手的全局行为规范，所有新建对话将自动继承此提示词。</p>
    </div>

    <el-card class="editor-card" v-loading="loading">
      <template #header>
        <div class="editor-header">
          <div style="display: flex; align-items: center; gap: 12px">
            <span class="editor-title">📝 提示词内容</span>
          </div>
          <span class="editor-subtitle">
            最后更新：
            <template v-if="promptData?.updated_at">
              {{ new Date(promptData.updated_at + 'Z').toLocaleString('zh-CN') }}
            </template>
            <template v-else>--</template>
          </span>
        </div>
      </template>

      <!-- 预览模式 (默认) — Markdown 渲染 -->
      <div v-if="!editing" class="markdown-preview" v-html="renderedContent"></div>

      <!-- 编辑模式 — Textarea -->
      <el-input
        v-else
        v-model="content"
        type="textarea"
        :rows="1"
        placeholder="请输入系统提示词（支持 Markdown 格式）..."
        :disabled="loading"
        resize="none"
        class="prompt-textarea"
      />

      <div class="action-bar">
        <div class="action-bar-left">
          <el-button v-if="!editing" type="default" size="large" @click="editing = true">
            <el-icon><EditPen /></el-icon> 编辑
          </el-button>
          <el-button v-else type="primary" size="large" :loading="saving" :disabled="loading || !content.trim()" @click="handleSave">
            💾 保存修改
          </el-button>
          <el-button size="large" :loading="resetting" :disabled="loading" @click="handleReset">
            🔄 重置为默认值
          </el-button>
          <span v-if="content !== originalContent && !loading" class="unsaved-hint">有未保存的更改</span>
        </div>
        <div class="action-bar-right">
          <div class="info-tip">
            <el-icon style="font-size: 13px; color: var(--primary-color);"><InfoFilled /></el-icon>
            <span>修改后仅对新对话生效，已有对话不受影响</span>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.system-prompt-page { max-width: 960px; margin: 0 auto; height: 100%; display: flex; flex-direction: column; }
.page-header h2 { margin: 0 0 6px 0; font-size: 22px; flex-shrink: 0; }
.page-desc { margin: 0 0 16px 0; font-size: 13px; color: #64748b; flex-shrink: 0; }

/* 编辑器卡片 - 渐变 header */
.editor-card {
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  margin-bottom: 0;
  box-shadow: var(--shadow-sm);
}
.editor-card :deep(.el-card__header) {
  padding: 14px 20px;
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  border-bottom: 1px solid #fcd34d;
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
}
.editor-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 16px 20px !important;
  background: #fcfcfc;
}
.editor-header { display: flex; align-items: center; justify-content: space-between; }
.editor-title { font-weight: 600; font-size: 15px; color: #92400e; }
.editor-subtitle { font-size: 12px; color: #a16207; }

/* Markdown 预览 (默认) */
.markdown-preview {
  flex: 1; min-height: 0; overflow-y: auto;
  padding: 20px 24px;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  font-size: 15px; line-height: 1.8; color: #1e293b;
  box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.02);
}
.markdown-preview :deep(h1) { font-size: 24px; font-weight: 700; margin: 0 0 16px 0; padding-bottom: 8px; border-bottom: 2px solid var(--border-color); color: #0f172a; }
.markdown-preview :deep(h2) { font-size: 20px; font-weight: 600; margin: 24px 0 12px 0; padding-bottom: 6px; border-bottom: 1px solid var(--border-color); color: #0f172a; }
.markdown-preview :deep(h3) { font-size: 17px; font-weight: 600; margin: 20px 0 10px 0; color: #1e293b; }
.markdown-preview :deep(h4) { font-size: 15px; font-weight: 600; margin: 16px 0 8px 0; color: #1e293b; }
.markdown-preview :deep(p) { margin: 0 0 12px 0; }
.markdown-preview :deep(strong) { font-weight: 700; color: #0f172a; }
.markdown-preview :deep(ul), .markdown-preview :deep(ol) { padding-left: 24px; margin: 8px 0 12px 0; }
.markdown-preview :deep(li) { margin: 4px 0; line-height: 1.7; }
.markdown-preview :deep(code) { background: #f1f5f9; padding: 2px 8px; border-radius: 4px; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.85em; color: #1e293b; }
.markdown-preview :deep(pre) { background: #1e293b; color: #e2e8f0; padding: 16px 20px; border-radius: 8px; overflow-x: auto; margin: 12px 0; line-height: 1.6; }
.markdown-preview :deep(pre code) { background: transparent; padding: 0; color: inherit; font-size: 13px; }
.markdown-preview :deep(blockquote) { border-left: 4px solid #f59e0b; padding: 8px 16px; margin: 12px 0; color: #475569; background: #fffbeb; border-radius: 0 6px 6px 0; }
.markdown-preview :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
.markdown-preview :deep(th), .markdown-preview :deep(td) { border: 1px solid var(--border-color); padding: 8px 12px; text-align: left; }
.markdown-preview :deep(th) { background: #f8fafc; font-weight: 600; color: #475569; }
.markdown-preview :deep(a) { color: var(--primary-color); text-decoration: none; }
.markdown-preview :deep(a:hover) { text-decoration: underline; }
.markdown-preview :deep(hr) { border: none; border-top: 1px solid var(--border-color); margin: 20px 0; }
.markdown-preview :deep(img) { max-width: 100%; border-radius: 6px; margin: 8px 0; }

/* 编辑模式 textarea */
.prompt-textarea { flex: 1; min-height: 0; }
.prompt-textarea :deep(.el-textarea__inner) {
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', 'Courier New', monospace !important;
  font-size: 14px !important; line-height: 1.9 !important; padding: 16px;
  height: 100% !important; background: #fafbfc !important;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.03) !important; color: #334155 !important;
}

/* 底部操作栏 */
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}
.action-bar-left { display: flex; align-items: center; gap: 12px; }
.action-bar-right { flex-shrink: 0; }
.info-tip {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #6d28d9;
  background: #f5f3ff;
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid #e0e7ff;
  white-space: nowrap;
}

.unsaved-hint { font-size: 13px; color: #d97706; margin-left: 4px; }
</style>