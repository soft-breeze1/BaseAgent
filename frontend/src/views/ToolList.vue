<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { toolApi, type ToolItem } from '../api/tools'
import { ElMessage } from 'element-plus'

const loading = ref(false)
const tools = ref<ToolItem[]>([])
const testingTavily = ref(false)

const translating = ref<Record<string, boolean>>({})
const translatedTexts = ref<Record<string, string>>({})
const translationErrors = ref<Record<string, string>>({})
const tavilyApiKey = ref('')

const toolTypeMap: Record<string, { label: string; color: string }> = {
  builtin: { label: '内置', color: '#3b82f6' },
  api: { label: 'API 工具', color: '#059669' },
  skill: { label: '技能', color: '#d97706' },
}

function isChineseText(text: string): boolean {
  const chineseChars = (text.match(/[\u4e00-\u9fff]/g) || []).length
  return chineseChars > text.length * 0.3
}

function getButtonText(name: string, text: string): string {
  if (translatedTexts.value[name]) return '原文'
  if (isChineseText(text)) return ''
  return '翻译'
}

function getButtonType(name: string, text: string): string {
  if (translatedTexts.value[name]) return 'warning'
  return 'default'
}


async function loadTools() {
  loading.value = true
  try {
    const res = await toolApi.list()
    tools.value = res.data
    const tavily = res.data.find(t => t.name === 'tavily_web_search')
    if (tavily && tavily.config?.api_key) {
      tavilyApiKey.value = tavily.config.api_key
    }
  } finally {
    loading.value = false
  }
}

async function saveTavilyApiKey() {
  try {
    await toolApi.updateConfig('tavily_web_search', { api_key: tavilyApiKey.value })
    ElMessage.success('Tavily API Key 已保存')
  } catch {
    ElMessage.error('保存失败，请稍后重试')
  }
}

async function testTavilyConnection() {
  if (!tavilyApiKey.value.trim()) { ElMessage.warning('请先输入 API Key'); return }
  testingTavily.value = true
  try {
    const res = await toolApi.testTavily(tavilyApiKey.value.trim())
    if (res.data.success) ElMessage.success(res.data.message); else ElMessage.error(res.data.message)
  } catch { ElMessage.error('测试连接失败') }
  finally { testingTavily.value = false }
}

async function translateDescription(name: string, text: string) {
  if (isChineseText(text)) {
    translationErrors.value[name] = '已是中文无需翻译'
    setTimeout(() => { delete translationErrors.value[name] }, 2000)
    return
  }
  if (translatedTexts.value[name]) {
    delete translatedTexts.value[name]
    delete translationErrors.value[name]
    return
  }
  translating.value[name] = true
  delete translationErrors.value[name]
  try {
    const res = await toolApi.translate(text)
    if (res.data.translated && res.data.translated !== text) {
      translatedTexts.value[name] = res.data.translated
    } else {
      translationErrors.value[name] = '翻译失败'
      setTimeout(() => { delete translationErrors.value[name] }, 2000)
    }
  } catch {
    translationErrors.value[name] = '翻译服务暂时不可用'
    setTimeout(() => { delete translationErrors.value[name] }, 2000)
  }
  finally { translating.value[name] = false }
}

onMounted(() => { loadTools() })
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2>🔧 Tools 管理</h2>
        <p class="page-desc">配置和管理 Agent 可调用的工具。所有已注册的工具默认启用，Agent 将根据问题智能决定是否调用。</p>
      </div>
      <el-button type="primary" @click="ElMessage.info('工具由系统自动注册，无需手动创建')">
        <el-icon><Plus /></el-icon> 新建 Tool
      </el-button>
    </div>

    <div v-loading="loading" class="card-list">
      <el-card
        v-for="tool in tools"
        :key="tool.name"
        shadow="hover"
        class="unified-card tool-card"
      >
        <template #header>
          <div class="tool-header">
            <div class="tool-header-left">
              <span class="tool-name">{{ tool.display_name }}</span>
              <!-- 翻译/原文按钮 — 放在名称右侧，badge 左侧 -->
              <template v-if="tool.tool_type === 'skill'">
                <span v-if="translating[tool.name]" class="translating-hint-inline">翻译中...</span>
                <el-button
                  v-if="!translating[tool.name] && getButtonText(tool.name, tool.description)"
                  size="small"
                  :loading="translating[tool.name]"
                  :type="getButtonType(tool.name, tool.description)"
                  class="translate-btn-inline"
                  @click="translateDescription(tool.name, tool.description)"
                >
                  {{ getButtonText(tool.name, tool.description) }}
                </el-button>
              </template>
            </div>
            <span class="badge-pill tool-type-badge"
              :style="{
                background: toolTypeMap[tool.tool_type]?.color + '18',
                color: toolTypeMap[tool.tool_type]?.color || '#64748b'
              }"
            >
              {{ toolTypeMap[tool.tool_type]?.label || tool.tool_type }}
            </span>
          </div>
        </template>

        <div class="tool-body">
          <div class="tool-desc">
            <p class="tool-desc-text">
              {{ translatedTexts[tool.name] || tool.description || '暂无描述' }}
            </p>
            <p v-if="translationErrors[tool.name]" class="tool-error">
              {{ translationErrors[tool.name] }}
            </p>
          </div>
        </div>

        <div v-if="tool.name === 'tavily_web_search'" class="tavily-config">
          <label class="tavily-label">API Key</label>
          <div class="tavily-input-row">
            <el-input
              v-model="tavilyApiKey"
              type="password"
              placeholder="输入 Tavily API Key"
              size="small"
              show-password
              class="tavily-input"
            />
            <el-button size="small" type="primary" @click="saveTavilyApiKey">保存</el-button>
            <el-button size="small" type="success" :loading="testingTavily" @click="testTavilyConnection">测试连接</el-button>
          </div>
          <p class="tavily-hint">
            在 <a href="https://app.tavily.com" target="_blank" style="color: #2563eb">app.tavily.com</a> 获取 API Key
          </p>
        </div>
      </el-card>

      <div v-if="!loading && tools.length === 0" class="empty-state">
        <el-icon style="font-size: 48px; margin-bottom: 16px; color: #94a3b8"><SetUp /></el-icon>
        <p style="font-size: 15px">暂无可用工具</p>
        <p style="font-size: 12px; margin-top: 8px; color: #94a3b8">系统启动后会自动注册内置工具</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── Badge ── */
.badge-pill {
  display: inline-block;
  font-size: 12px;
  padding: 3px 12px;
  border-radius: 9999px;
  font-weight: 600 !important;
  line-height: 1.5;
  flex-shrink: 0;
  border: 1px solid currentColor;
}

/* ── Empty state ── */
.empty-state {
  grid-column: 1/-1;
  text-align: center;
  padding: 60px;
  color: #64748b;
}

/* ═══════════════════════════════════════════════════
   Tool Card
   ═══════════════════════════════════════════════════ */
.tool-card {
  height: 260px !important;
  display: flex !important;
  flex-direction: column !important;
  border: 1px solid var(--border-color) !important;
  border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-sm) !important;
  transition: box-shadow var(--transition-base), transform var(--transition-base) !important;
  position: relative !important;
  overflow: hidden;
}

.tool-card:hover {
  box-shadow: var(--shadow-lg) !important;
  transform: translateY(-2px) !important;
}

.tool-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 14px 20px !important;
  min-height: 0 !important;
}

/* ── Header — 带渐变背景 ── */
.tool-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.tool-card :deep(.el-card__header) {
  padding: 14px 20px;
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border-bottom: 1px solid var(--border-color);
}

.tool-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}

.tool-name {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.4;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.translating-hint-inline {
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
}

.translate-btn-inline {
  font-size: 12px !important;
  padding: 4px 8px !important;
}

/* ── Body ── */
.tool-body {
  flex: 1;
  display: flex;
  flex-direction: column;
}

/* ── Description — 自然换行，全部宽度可用 ── */
.tool-desc {
  flex: 1;
  overflow: hidden;
}

.tool-desc-text {
  color: #475569;
  font-size: 13px;
  margin: 0;
  line-height: 1.6;
  font-weight: 450;
  word-break: normal;
  overflow-wrap: anywhere;
  white-space: normal;
}

.tool-error {
  color: #f59e0b;
  font-size: 12px;
  margin-top: 4px;
  margin-bottom: 0;
}

/* ── Tavily 配置区域 ── */
.tavily-config {
  padding-top: 10px;
  border-top: 1px solid var(--border-light);
  margin-top: 8px;
}

.tavily-label {
  font-size: 13px;
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
  color: #475569;
}

.tavily-input-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.tavily-input {
  flex: 1;
}

.tavily-hint {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 6px;
}
</style>