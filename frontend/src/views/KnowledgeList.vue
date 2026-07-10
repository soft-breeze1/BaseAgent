<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { knowledgeApi, type KnowledgeBaseItem, type CreateKBParams } from '../api/knowledge'
import { modelApi, type ModelConfigItem } from '../api/models'
import { ElMessage, ElMessageBox } from 'element-plus'

const router = useRouter()
const loading = ref(false)
const knowledgeBases = ref<KnowledgeBaseItem[]>([])
const activeEmbeddingModels = ref<ModelConfigItem[]>([])

const showCreateDialog = ref(false)
const creating = ref(false)
const embeddingModels = ref<ModelConfigItem[]>([])
const loadingEmbeddingModels = ref(false)

const createForm = ref<CreateKBParams>({
  name: '',
  description: '',
  embedding_model: '',
})

async function loadEmbeddingModels() {
  loadingEmbeddingModels.value = true
  try {
    const res = await modelApi.list('embedding')
    const models = Array.isArray(res) ? res : res.data
    embeddingModels.value = models
    activeEmbeddingModels.value = models.filter((m: ModelConfigItem) => m.is_active)
    if (models.length > 0 && !createForm.value.embedding_model) {
      const active = models.find((m: ModelConfigItem) => m.is_active)
      createForm.value.embedding_model = active ? active.model_name : models[0].model_name
    }
  } catch {
    embeddingModels.value = []
    activeEmbeddingModels.value = []
  } finally {
    loadingEmbeddingModels.value = false
  }
}

watch(showCreateDialog, (isOpen) => {
  if (isOpen) loadEmbeddingModels()
})

async function loadKBs() {
  loading.value = true
  try {
    const res = await knowledgeApi.listKBs()
    knowledgeBases.value = res.data
    // Also load active embedding models for display
    if (activeEmbeddingModels.value.length === 0) {
      try {
        const res2 = await modelApi.list('embedding')
        const models = Array.isArray(res2) ? res2 : res2.data
        activeEmbeddingModels.value = models.filter((m: ModelConfigItem) => m.is_active)
      } catch {}
    }
  } finally {
    loading.value = false
  }
}

function getEmbeddingDisplay(kb: KnowledgeBaseItem): string {
  // Try to find active model; if kb's model matches active, show "active" badge
  const activeModel = activeEmbeddingModels.value.find(m => m.model_name === kb.embedding_model && m.is_active)
  if (activeModel) {
    return `${kb.embedding_model} ✅`
  }
  return kb.embedding_model || '未配置'
}

function getDescriptionDisplay(kb: KnowledgeBaseItem): string {
  if (kb.description && kb.description.trim()) {
    return kb.description.length > 40 ? kb.description.slice(0, 40) + '...' : kb.description
  }
  return ''
}

const cardListStyle = computed(() => ({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
  gap: '20px',
}))

async function handleCreate() {
  if (!createForm.value.name.trim()) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  creating.value = true
  try {
    await knowledgeApi.createKB(createForm.value)
    ElMessage.success('知识库创建成功')
    showCreateDialog.value = false
    createForm.value = { name: '', description: '', embedding_model: '' }
    await loadKBs()
  } finally {
    creating.value = false
  }
}

async function handleDelete(kb: KnowledgeBaseItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除知识库「${kb.name}」吗？删除后所有文档和向量数据将无法恢复。`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await knowledgeApi.deleteKB(kb.id)
    ElMessage.success('知识库已删除')
    await loadKBs()
  } catch {}
}

function goToDetail(id: string) {
  router.push(`/knowledge/${id}`)
}

onMounted(() => { loadKBs(); loadEmbeddingModels() })
</script>

<template>
  <div class="kb-page">
    <div class="page-header">
      <div>
        <h2>📚 知识库管理</h2>
        <p class="page-desc">管理和查看知识库，支持文档上传、智能语义分块与混合检索。</p>
      </div>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon> 新建知识库
      </el-button>
    </div>

    <div v-loading="loading" :style="cardListStyle">
      <el-card v-for="kb in knowledgeBases" :key="kb.id" shadow="hover" class="kb-card" @click="goToDetail(kb.id)">
        <div class="kb-card-inner">
          <!-- Top: Name + Badge -->
          <div class="kb-top">
            <div class="kb-name-row">
              <span class="kb-icon">📂</span>
              <span class="kb-name" :title="kb.name">{{ kb.name }}</span>
            </div>
            <el-tag :type="kb.status === 'ready' ? 'success' : 'warning'" size="small" effect="plain" round>
              {{ kb.status === 'ready' ? '就绪' : '处理中' }}
            </el-tag>
          </div>

          <!-- Description (if any) -->
          <div v-if="getDescriptionDisplay(kb)" class="kb-desc">
            {{ getDescriptionDisplay(kb) }}
          </div>

          <!-- Stats row -->
          <div class="kb-stats">
            <div class="kb-stat">
              <span class="stat-icon">📄</span>
              <span class="stat-val">{{ kb.document_count }}</span>
              <span class="stat-lbl">文档</span>
            </div>
            <div class="kb-stat">
              <span class="stat-icon">🧩</span>
              <span class="stat-val">{{ kb.chunk_size || '—' }}</span>
              <span class="stat-lbl">块大小</span>
            </div>
          </div>

          <!-- Embedding model -->
          <div class="kb-model-row">
            <span class="model-label">Embedding:</span>
            <span class="model-value">{{ getEmbeddingDisplay(kb) }}</span>
          </div>
        </div>

        <!-- Footer actions -->
        <div class="kb-actions">
          <el-button size="small" class="action-btn" @click.stop="goToDetail(kb.id)">
            查看详情
          </el-button>
          <el-button size="small" class="action-btn danger" @click.stop="handleDelete(kb)">
            删除
          </el-button>
        </div>
      </el-card>

      <div v-if="!loading && knowledgeBases.length === 0" class="empty-state">
        <el-icon style="font-size: 48px; margin-bottom: 16px; color: #94a3b8"><FolderOpened /></el-icon>
        <p style="font-size: 15px">暂无知识库，点击右上角新建</p>
      </div>
    </div>

    <!-- Create Dialog -->
    <el-dialog v-model="showCreateDialog" title="新建知识库" width="480px" :close-on-click-modal="false">
      <el-form :model="createForm" label-position="top">
        <el-form-item label="知识库名称" required>
          <el-input v-model="createForm.name" placeholder="请输入知识库名称" maxlength="200" />
        </el-form-item>
        <el-form-item v-if="loadingEmbeddingModels" label="Embedding 模型">
          <el-select placeholder="加载中..." style="width: 100%" loading disabled />
        </el-form-item>
        <el-form-item v-else-if="embeddingModels.length > 0" label="Embedding 模型" required>
          <el-select v-model="createForm.embedding_model" placeholder="请选择嵌入模型" style="width: 100%">
            <el-option v-for="m in embeddingModels" :key="m.id" :label="`${m.model_name} (${m.provider})${m.is_active ? ' ✅' : ''}`" :value="m.model_name" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="Embedding 模型">
          <el-alert type="warning" :closable="false" show-icon>
            <template #title>
              未检测到已配置的嵌入模型，请先在
              <el-link type="primary" @click="() => { showCreateDialog = false; router.push('/models') }">模型配置</el-link>
              中添加嵌入模型（model_type 选择 "embedding"）。
            </template>
          </el-alert>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-page {
  padding: 0 4px;
}

/* ── Card ── */
.kb-card {
  border: 1px solid #e4e7ed !important;
  border-radius: 12px !important;
  transition: box-shadow 0.25s, transform 0.25s !important;
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.kb-card:hover {
  box-shadow: 0 8px 25px rgba(0,0,0,0.08) !important;
  transform: translateY(-3px);
}
.kb-card :deep(.el-card__body) {
  padding: 0 !important;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.kb-card-inner {
  padding: 18px 20px 12px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* ── Top row ── */
.kb-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}
.kb-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.kb-icon {
  font-size: 20px;
  flex-shrink: 0;
}
.kb-name {
  font-weight: 600;
  font-size: 15px;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Description ── */
.kb-desc {
  font-size: 13px;
  color: #64748b;
  line-height: 1.4;
  padding: 6px 10px;
  background: #f8fafc;
  border-radius: 6px;
  border-left: 3px solid #6366f1;
}

/* ── Stats ── */
.kb-stats {
  display: flex;
  gap: 16px;
  padding: 6px 0;
}
.kb-stat {
  display: flex;
  align-items: center;
  gap: 4px;
}
.stat-icon { font-size: 15px; }
.stat-val { font-size: 14px; font-weight: 600; color: #334155; }
.stat-lbl { font-size: 12px; color: #94a3b8; }

/* ── Model row ── */
.kb-model-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: linear-gradient(135deg, #eef2ff 0%, #f0f4ff 100%);
  border-radius: 8px;
  font-size: 13px;
}
.model-label {
  color: #64748b;
  font-weight: 500;
}
.model-value {
  color: #4338ca;
  font-weight: 600;
  font-family: 'SF Mono', 'Consolas', monospace;
  font-size: 12px;
}

/* ── Actions ── */
.kb-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 20px;
  border-top: 1px solid #f1f5f9;
  background: #fafbfc;
}
.action-btn {
  font-size: 12px !important;
  padding: 4px 12px !important;
  color: #475569 !important;
  border-color: #e2e8f0 !important;
}
.action-btn:hover {
  color: #6366f1 !important;
  border-color: #6366f1 !important;
  background: #eef2ff !important;
}
.action-btn.danger {
  color: #fb7185 !important;
}
.action-btn.danger:hover {
  color: #e11d48 !important;
  background: #fff1f2 !important;
  border-color: #fecdd3 !important;
}

/* ── Empty state ── */
.empty-state { grid-column: 1/-1; text-align: center; padding: 60px; color: #64748b; }
</style>