<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { knowledgeApi, type KnowledgeBaseItem, type KnowledgeDocumentItem } from '../api/knowledge'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const router = useRouter()
const kbId = computed(() => route.params.id as string)

const kb = ref<KnowledgeBaseItem | null>(null)
const documents = ref<KnowledgeDocumentItem[]>([])
const loading = ref(false)
const uploading = ref(false)

const uploadRef = ref<HTMLInputElement>()

const acceptedFormats = '.pdf,.doc,.docx,.csv,.md,.txt,.ppt,.pptx,.xls,.xlsx,.html,.htm,.xml,.json,.epub'

async function loadKB() {
  try {
    const res = await knowledgeApi.getKB(kbId.value)
    kb.value = res.data
  } catch {
    ElMessage.error('知识库不存在')
    router.push('/knowledge')
  }
}

async function loadDocuments() {
  loading.value = true
  try {
    const res = await knowledgeApi.listDocuments(kbId.value)
    documents.value = res.data
  } finally {
    loading.value = false
  }
}

async function handleUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return

  uploading.value = true
  let successCount = 0
  let failCount = 0

  for (const file of Array.from(files)) {
    try {
      await knowledgeApi.uploadDocument(kbId.value, file)
      successCount++
    } catch {
      failCount++
      ElMessage.error(`文件「${file.name}」上传失败`)
    }
  }

  if (successCount > 0) {
    ElMessage.success(`成功上传 ${successCount} 个文件` + (failCount > 0 ? `，${failCount} 个失败` : ''))
  }
  await loadDocuments()
  await loadKB()
  uploading.value = false
  if (uploadRef.value) uploadRef.value.value = ''
}

function triggerUpload() {
  uploadRef.value?.click()
}

async function handleDelete(doc: KnowledgeDocumentItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除文档「${doc.filename}」吗？`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await knowledgeApi.deleteDocument(kbId.value, doc.id)
    ElMessage.success('文档已删除')
    await loadDocuments()
    await loadKB()
  } catch {
    // cancelled
  }
}

async function handleReprocess(doc: KnowledgeDocumentItem) {
  try {
    await knowledgeApi.reprocessDocument(kbId.value, doc.id)
    ElMessage.success('已重新加入处理队列')
    await loadDocuments()
  } catch {
    // handled
  }
}

function getStatusDotClass(status: string): string {
  return `status-dot status-${status}`
}

function getStatusText(status: string): string {
  const map: Record<string, string> = {
    ready: '已就绪',
    processing: '处理中',
    pending: '等待处理',
    error: '失败',
  }
  return map[status] || status
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

watch(() => route.params.id, (newId) => {
  if (newId) {
    loadKB()
    loadDocuments()
  }
})

onMounted(() => {
  loadKB()
  loadDocuments()
})
</script>

<template>
  <div>
    <!-- Back -->
    <div style="margin-bottom: 16px">
      <el-button text @click="router.push('/knowledge')">
        <el-icon><ArrowLeft /></el-icon>
        返回知识库列表
      </el-button>
    </div>

    <!-- KB Info -->
    <div class="page-header" v-if="kb">
      <div>
        <h2>{{ kb.name }}</h2>
        <p class="kb-meta">
          文档数: {{ kb.document_count }} ·
          Embedding: {{ kb.embedding_model }}
        </p>
      </div>
    </div>

    <!-- Upload Zone -->
    <div class="upload-zone" @click="triggerUpload" v-loading="uploading" element-loading-text="正在上传并解析文件...">
      <input
        ref="uploadRef"
        type="file"
        :accept="acceptedFormats"
        multiple
        style="display: none"
        @change="handleUpload"
      />
      <el-icon style="font-size: 40px; color: #c0c4cc; margin-bottom: 12px"><UploadFilled /></el-icon>
      <p style="font-size: 15px; color: #606266">点击或拖拽文件到此处上传</p>
      <p style="font-size: 12px; color: #c0c4cc; margin-top: 6px">
        支持 PDF、Word、CSV、Markdown、TXT、PPT、Excel、HTML、XML、JSON、EPUB 格式
      </p>
    </div>

    <!-- Document Table -->
    <el-card class="doc-card" style="margin-top: 20px">
      <template #header>
        <span class="doc-card-title">文档列表 ({{ documents.length }})</span>
      </template>
      <el-table :data="documents" v-loading="loading" style="width: 100%">
        <el-table-column label="文件名" min-width="220">
          <template #default="{ row }">
            <div class="doc-table-name">
              <span :class="getStatusDotClass(row.status)" style="margin-right: 8px;"></span>
              <span>{{ row.filename }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.file_type.toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">
            {{ formatFileSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column label="分块数" width="100">
          <template #default="{ row }">
            {{ row.chunk_count }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.status === 'error' && row.error_message"
              :content="row.error_message"
              placement="top"
            >
              <span class="status-tag" :class="'status-tag-' + row.status">{{ getStatusText(row.status) }}</span>
            </el-tooltip>
            <span v-else class="status-tag" :class="'status-tag-' + row.status">{{ getStatusText(row.status) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="180">
          <template #default="{ row }">
            {{ new Date(row.created_at + 'Z').toLocaleString() }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'error' || row.status === 'pending'"
              type="primary"
              size="small"
              text
              @click="handleReprocess(row)"
            >
              重试
            </el-button>
            <el-button
              type="danger"
              size="small"
              text
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div style="padding: 40px; text-align: center; color: #909399">
            暂无文档，请上传文件
          </div>
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.kb-meta {
  color: var(--text-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.doc-card-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
}
.doc-card {
  border-radius: var(--radius-lg) !important;
  border: 1px solid var(--border-color) !important;
  box-shadow: var(--shadow-sm) !important;
}
.doc-card :deep(.el-card__header) {
  padding: 14px 20px !important;
  background: var(--gray-50);
  border-bottom: 1px solid var(--border-color) !important;
}
.doc-table-name {
  display: flex;
  align-items: center;
}
</style>