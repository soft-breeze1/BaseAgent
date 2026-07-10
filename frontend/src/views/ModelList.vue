<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { modelApi, type ModelConfigItem, type CreateModelParams, type ModelType, type OllamaModelItem } from '../api/models'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Connection, Delete, Edit, Star, Loading } from '@element-plus/icons-vue'

const loading = ref(false)
const models = ref<ModelConfigItem[]>([])

// Test connection state
const testingIds = ref<Set<string>>(new Set())

// Create/Edit dialog
const showDialog = ref(false)
const isEditing = ref(false)
const editId = ref<string | null>(null)
const saving = ref(false)

// Track which side triggered the dialog (for create mode)
const dialogType = ref<ModelType>('llm')

const form = ref<CreateModelParams>({
  provider: '',
  model_name: '',
  model_type: 'llm',
  api_key: '',
  api_base: '',
})

// Ollama model import
const ollamaModels = ref<OllamaModelItem[]>([])
const loadingOllama = ref(false)
const ollamaModelInput = ref('')

const providers = [
  { label: 'DeepSeek', value: 'deepseek' },
  { label: 'OpenAI', value: 'openai' },
  { label: 'Zhipu AI (智谱)', value: 'zhipu' },
  { label: 'Alibaba (阿里云)', value: 'alibaba' },
  { label: 'Moonshot (月之暗面)', value: 'moonshot' },
  { label: 'Ollama (本地)', value: 'ollama' },
  { label: 'Custom', value: 'custom' },
]

const isOllamaProvider = computed(() => form.value.provider === 'ollama')

const llmModels = computed(() => models.value.filter(m => m.model_type === 'llm'))
const embeddingModels = computed(() => models.value.filter(m => m.model_type === 'embedding'))

async function loadModels() {
  loading.value = true
  try {
    const res = await modelApi.list()
    models.value = res.data
  } finally {
    loading.value = false
  }
}

// Watch provider to detect when Ollama is selected
watch(() => form.value.provider, (val) => {
  if (val === 'ollama') {
    loadOllamaModels()
  }
})

async function loadOllamaModels() {
  loadingOllama.value = true
  try {
    const res = await modelApi.listOllamaModels()
    ollamaModels.value = res.data
  } catch {
    ollamaModels.value = []
  } finally {
    loadingOllama.value = false
  }
}

function selectOllamaModel(name: string) {
  form.value.model_name = name
}

function openCreate(modelType: ModelType) {
  isEditing.value = false
  editId.value = null
  dialogType.value = modelType
  form.value = { provider: '', model_name: '', model_type: modelType, api_key: '', api_base: '' }
  ollamaModels.value = []
  ollamaModelInput.value = ''
  showDialog.value = true
}

function openEdit(model: ModelConfigItem) {
  isEditing.value = true
  editId.value = model.id
  dialogType.value = model.model_type
  form.value = {
    provider: model.provider,
    model_name: model.model_name,
    model_type: model.model_type,
    api_key: model.api_key,
    api_base: model.api_base || '',
  }
  ollamaModels.value = []
  ollamaModelInput.value = ''
  if (model.provider === 'ollama') {
    loadOllamaModels()
  }
  showDialog.value = true
}

async function handleSave() {
  if (!form.value.provider || !form.value.model_name) {
    ElMessage.warning('请填写必填项')
    return
  }
  // API Key is optional for Ollama
  if (form.value.provider !== 'ollama' && !form.value.api_key) {
    ElMessage.warning('请输入 API Key')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value }
    // For Ollama, allow empty api_key to be passed as-is (no placeholder)
    if (isEditing.value && editId.value) {
      // When editing, if api_key is empty, explicitly send empty string to clear it
      await modelApi.update(editId.value, payload)
      ElMessage.success('模型配置已更新')
    } else {
      // For Ollama creation, if no api_key provided, send empty string
      await modelApi.create(payload)
      ElMessage.success('模型配置已创建')
    }
    showDialog.value = false
    await loadModels()
  } finally {
    saving.value = false
  }
}

async function handleToggleActive(model: ModelConfigItem) {
  try {
    await modelApi.update(model.id, { is_active: !model.is_active })
    await loadModels()
    ElMessage.success(model.is_active ? '已停用' : '已启用')
  } catch {
    // handled
  }
}

async function handleSetDefault(model: ModelConfigItem) {
  try {
    await modelApi.update(model.id, { is_default: true })
    ElMessage.success(`已将「${model.model_name}」设为默认模型`)
    await loadModels()
  } catch {
    // handled
  }
}

async function handleTestConnection(model: ModelConfigItem) {
  testingIds.value.add(model.id)
  try {
    const res = await modelApi.testConnection(model.id)
    if (res.data.success) {
      ElMessage.success(res.data.message)
    } else {
      ElMessage.error(res.data.message)
    }
  } catch {
    ElMessage.error('连接失败：请求异常')
  } finally {
    testingIds.value.delete(model.id)
  }
}

async function handleDelete(model: ModelConfigItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除模型配置「${model.model_name}」吗？`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await modelApi.delete(model.id)
    ElMessage.success('已删除')
    await loadModels()
  } catch {
    // cancelled
  }
}

function maskApiKey(key: string | null | undefined): string {
  if (!key) return ''
  if (key.length <= 8) return '*'.repeat(key.length)
  return key.substring(0, 4) + '*'.repeat(24) + key.substring(key.length - 4)
}

function getProviderLabel(provider: string): string {
  return providers.find(p => p.value === provider)?.label || provider
}

function getProviderColor(provider: string): string {
  const colors: Record<string, string> = {
    deepseek: '#4FC3F7',
    openai: '#10A37F',
    zhipu: '#2B6CB0',
    alibaba: '#FF6A00',
    moonshot: '#6C5CE7',
    ollama: '#8B5CF6',
    custom: '#64748B',
  }
  return colors[provider] || '#64748B'
}

function formatSize(bytes: number): string {
  if (bytes >= 1_000_000_000) return (bytes / 1_000_000_000).toFixed(1) + ' GB'
  if (bytes >= 1_000_000) return (bytes / 1_000_000).toFixed(1) + ' MB'
  if (bytes >= 1_000) return (bytes / 1_000).toFixed(1) + ' KB'
  return bytes + ' B'
}

function getDialogTitle(): string {
  if (isEditing.value) return '编辑模型配置'
  return dialogType.value === 'llm' ? '添加 LLM 模型' : '添加嵌入模型'
}

onMounted(() => {
  loadModels()
})
</script>

<template>
  <div class="model-page">
    <div class="page-header">
      <h2>🤖 模型管理</h2>
    </div>

    <div class="model-grid">
      <!-- LLM Models -->
      <div class="model-column llm-column">
        <div class="column-header">
          <div class="column-title">
            <span class="column-icon">💬</span>
            <h3>大语言模型</h3>
            <el-tag size="small" round type="primary">{{ llmModels.length }}</el-tag>
          </div>
          <el-button size="small" class="column-add-btn" @click="openCreate('llm')">
            <el-icon><Plus /></el-icon>
            添加
          </el-button>
        </div>
        <div class="card-list">
          <div v-if="loading && llmModels.length === 0" class="loading-placeholder">
            <el-icon class="loading-icon"><Loading /></el-icon>
            <span>加载中...</span>
          </div>
          <div
            v-for="model in llmModels"
            :key="model.id"
            class="model-card llm-card"
            :class="{ inactive: !model.is_active }"
          >
            <div class="card-indicator"></div>
            <div class="card-content">
              <div class="card-top">
                <div class="card-name-row">
                  <span class="model-name">{{ model.model_name }}</span>
                  <el-tag v-if="model.is_default" size="small" type="warning" effect="dark" round>默认</el-tag>
                </div>
                <el-switch
                  :model-value="model.is_active"
                  size="small"
                  @change="handleToggleActive(model)"
                />
              </div>
              <div class="card-body">
                <div class="card-info-row">
                  <span class="card-label">服务商</span>
                  <span class="provider-badge" :style="{ background: getProviderColor(model.provider) + '18', color: getProviderColor(model.provider) }">
                    {{ getProviderLabel(model.provider) }}
                  </span>
                </div>
                <div class="card-info-row" v-if="model.api_key">
                  <span class="card-label">API Key</span>
                  <span class="card-value api-key">{{ maskApiKey(model.api_key) }}</span>
                </div>
              </div>
              <div class="card-actions">
                <el-button
                  size="small"
                  text
                  :loading="testingIds.has(model.id)"
                  @click="handleTestConnection(model)"
                >
                  <template #icon>
                    <el-icon><Connection /></el-icon>
                  </template>
                  测试
                </el-button>
                <el-button size="small" text class="act-btn" @click="openEdit(model)">
                  <template #icon>
                    <el-icon><Edit /></el-icon>
                  </template>
                  编辑
                </el-button>
                <el-button
                  v-if="!model.is_default"
                  size="small"
                  text
                  class="act-btn"
                  @click="handleSetDefault(model)"
                >
                  <template #icon>
                    <el-icon><Star /></el-icon>
                  </template>
                  设为默认
                </el-button>
                <el-button size="small" text class="delete-btn" @click="handleDelete(model)">
                  <template #icon>
                    <el-icon><Delete /></el-icon>
                  </template>
                  删除
                </el-button>
              </div>
            </div>
          </div>
          <!-- Empty state -->
          <div v-if="!loading && llmModels.length === 0" class="empty-card">
            <div class="empty-icon">💬</div>
            <p>暂无 LLM 模型</p>
            <el-button size="small" class="empty-add-btn" @click="openCreate('llm')">立即添加</el-button>
          </div>
        </div>
      </div>

      <!-- Embedding Models -->
      <div class="model-column embedding-column">
        <div class="column-header">
          <div class="column-title">
            <span class="column-icon">🔢</span>
            <h3>嵌入模型</h3>
            <el-tag size="small" round type="success">{{ embeddingModels.length }}</el-tag>
          </div>
          <el-button size="small" class="column-add-btn" @click="openCreate('embedding')">
            <el-icon><Plus /></el-icon>
            添加
          </el-button>
        </div>
        <div class="card-list">
          <div v-if="loading && embeddingModels.length === 0" class="loading-placeholder">
            <el-icon class="loading-icon"><Loading /></el-icon>
            <span>加载中...</span>
          </div>
          <div
            v-for="model in embeddingModels"
            :key="model.id"
            class="model-card embedding-card"
            :class="{ inactive: !model.is_active }"
          >
            <div class="card-indicator"></div>
            <div class="card-content">
              <div class="card-top">
                <div class="card-name-row">
                  <span class="model-name">{{ model.model_name }}</span>
                  <el-tag v-if="model.is_default" size="small" type="warning" effect="dark" round>默认</el-tag>
                </div>
                <el-switch
                  :model-value="model.is_active"
                  size="small"
                  @change="handleToggleActive(model)"
                />
              </div>
              <div class="card-body">
                <div class="card-info-row">
                  <span class="card-label">服务商</span>
                  <span class="provider-badge" :style="{ background: getProviderColor(model.provider) + '18', color: getProviderColor(model.provider) }">
                    {{ getProviderLabel(model.provider) }}
                  </span>
                </div>
                <div class="card-info-row" v-if="model.api_key">
                  <span class="card-label">API Key</span>
                  <span class="card-value api-key">{{ maskApiKey(model.api_key) }}</span>
                </div>
              </div>
              <div class="card-actions">
                <el-button
                  size="small"
                  text
                  :loading="testingIds.has(model.id)"
                  @click="handleTestConnection(model)"
                >
                  <template #icon>
                    <el-icon><Connection /></el-icon>
                  </template>
                  测试
                </el-button>
                <el-button size="small" text class="act-btn" @click="openEdit(model)">
                  <template #icon>
                    <el-icon><Edit /></el-icon>
                  </template>
                  编辑
                </el-button>
                <el-button
                  v-if="!model.is_default"
                  size="small"
                  text
                  class="act-btn"
                  @click="handleSetDefault(model)"
                >
                  <template #icon>
                    <el-icon><Star /></el-icon>
                  </template>
                  设为默认
                </el-button>
                <el-button size="small" text class="delete-btn" @click="handleDelete(model)">
                  <template #icon>
                    <el-icon><Delete /></el-icon>
                  </template>
                  删除
                </el-button>
              </div>
            </div>
          </div>
          <!-- Empty state -->
          <div v-if="!loading && embeddingModels.length === 0" class="empty-card">
            <div class="empty-icon">🔢</div>
            <p>暂无嵌入模型</p>
            <el-button size="small" class="empty-add-btn" @click="openCreate('embedding')">立即添加</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- Full-page loading overlay -->
    <div v-if="loading && models.length > 0" class="loading-overlay">
      <el-icon class="loading-icon"><Loading /></el-icon>
    </div>

    <!-- Create/Edit Dialog -->
    <el-dialog
      v-model="showDialog"
      :title="getDialogTitle()"
      width="520px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form :model="form" label-position="top">
        <!-- 创建时隐藏模型类型选择，由点击的列决定 -->
        <el-form-item v-if="isEditing" label="模型类型" required>
          <el-radio-group v-model="form.model_type" disabled>
            <el-radio value="llm">💬 大语言模型 (LLM)</el-radio>
            <el-radio value="embedding">🔢 嵌入模型 (Embedding)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="服务商" required>
          <el-select v-model="form.provider" placeholder="请选择大模型服务商" style="width: 100%">
            <el-option
              v-for="p in providers"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </el-select>
        </el-form-item>
        <!-- Ollama model picker -->
        <el-form-item v-if="isOllamaProvider" label="选择已安装模型">
          <div style="display: flex; gap: 8px; margin-bottom: 8px">
            <el-input
              v-model="ollamaModelInput"
              placeholder="或手动输入模型名称，如 qwen2.5:7b"
              size="small"
              style="flex: 1"
              @change="selectOllamaModel(ollamaModelInput)"
            />
            <el-button size="small" :loading="loadingOllama" @click="loadOllamaModels">
              刷新
            </el-button>
          </div>
          <div v-if="ollamaModels.length > 0" class="ollama-list">
            <div
              v-for="om in ollamaModels"
              :key="om.name"
              class="ollama-item"
              :class="{ selected: form.model_name === om.name }"
              @click="selectOllamaModel(om.name)"
            >
              <span class="ollama-name">{{ om.name }}</span>
              <span class="ollama-size">{{ formatSize(Number(om.size)) }}</span>
            </div>
          </div>
          <div v-else-if="!loadingOllama" class="ollama-empty">
            未检测到已安装模型，请确认 Ollama 服务已启动
          </div>
        </el-form-item>
        <el-form-item label="模型名称" required>
          <el-input v-model="form.model_name" placeholder="如 gpt-4o, glm-4, deepseek-chat, qwen2.5:7b" />
        </el-form-item>
        <el-form-item :label="isOllamaProvider ? 'API Key (Ollama 可不填)' : 'API Key'" :required="!isOllamaProvider">
          <el-input v-model="form.api_key" type="password" placeholder="请输入 API Key" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.model-page {
  position: relative;
  min-height: 400px;
}

/* Grid Layout */
.model-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-lg);
}

.model-column {
  display: flex;
  flex-direction: column;
}

/* Column Headers — 带渐变底色 */
.column-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-md);
  padding: 14px 20px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  position: relative;
}

.llm-column .column-header {
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  border-bottom: 1px solid #bfdbfe;
}

.embedding-column .column-header {
  background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
  border-bottom: 1px solid #bbf7d0;
}

.column-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.column-icon {
  font-size: 22px;
}

.column-title h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.column-add-btn {
  color: var(--primary-color);
  font-weight: 500;
}

.embedding-column .column-add-btn {
  color: #059669;
}

/* Card List */
.card-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.loading-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px;
  color: #909399;
  font-size: 14px;
}

.loading-icon {
  font-size: 20px;
  animation: rotating 1.5s linear infinite;
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Model Card */
.model-card {
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border-color);
  transition: all var(--transition-base);
  display: flex;
  overflow: hidden;
}

.model-card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}

.model-card.inactive {
  background: #fff;
  opacity: 1;
}

/* Card left accent indicator */
.card-indicator {
  width: 3px;
  flex-shrink: 0;
}

.llm-card .card-indicator {
  background: linear-gradient(180deg, var(--primary-color), #60a5fa);
}

.embedding-card .card-indicator {
  background: linear-gradient(180deg, #059669, #34d399);
}

/* Card background - active has light tint, inactive has white */
.llm-card:not(.inactive) {
  background: #f0f7ff;
}

.embedding-card:not(.inactive) {
  background: #ecfdf5;
}

.card-content {
  flex: 1;
  padding: 8px 14px 6px 10px;
  min-width: 0;
}

/* Card Top */
.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.card-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.model-name {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a2e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Card Body */
.card-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.card-info-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.card-label {
  color: #909399;
  flex-shrink: 0;
  min-width: 56px;
}

.card-value {
  color: #303133;
}

.api-key {
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', 'Courier New', monospace;
  font-size: 12px;
  color: #64748b;
  letter-spacing: 1px;
  background: #f8fafc;
  padding: 1px 6px;
  border-radius: 4px;
}

/* Provider Badge */
.provider-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

/* Card Actions */
.card-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
  flex-wrap: wrap;
}

.card-actions .el-button {
  padding: 4px 8px;
  font-size: 12px;
}

.llm-card .act-btn {
  color: var(--primary-color);
}

.embedding-card .act-btn {
  color: #059669;
}

/* Delete button — 默认灰色，hover 时微红 */
.delete-btn {
  color: #909399 !important;
}
.delete-btn:hover {
  color: #e74c3c !important;
  background: rgba(231, 76, 60, 0.06) !important;
}
.delete-btn:active {
  background: rgba(231, 76, 60, 0.12) !important;
}

/* Empty State */
.empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: #fff;
  border-radius: var(--radius-lg);
  border: 2px dashed var(--border-color);
  color: #64748b;
}

.empty-icon {
  font-size: 40px;
  margin-bottom: 12px;
  opacity: 0.5;
}

.empty-card p {
  margin: 0 0 12px;
  font-size: 14px;
}

.empty-add-btn {
  color: var(--primary-color);
}

.embedding-column .empty-add-btn {
  color: #059669;
}

/* Loading overlay */
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 8px;
}

.loading-overlay .loading-icon {
  font-size: 32px;
  color: #409eff;
}

/* Ollama list in dialog */
.ollama-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
}

.ollama-item {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-light);
  transition: all 0.15s ease;
}

.ollama-item:last-child {
  border-bottom: none;
}

.ollama-item:hover {
  background: #f8fafc;
}

.ollama-item.selected {
  background: #eff6ff;
}

.ollama-name {
  flex: 1;
  font-weight: 500;
  font-size: 14px;
  color: #1e293b;
}

.ollama-size {
  color: #64748b;
  font-size: 12px;
}

.ollama-empty {
  color: #64748b;
  font-size: 12px;
  padding: 8px 0;
}

/* Responsive: stack on narrow screens */
@media (max-width: 900px) {
  .model-grid {
    grid-template-columns: 1fr;
  }
}
</style>
