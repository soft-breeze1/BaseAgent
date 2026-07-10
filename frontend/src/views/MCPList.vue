<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { mcpApi, type MCPServerItem } from '../api/mcp'
import { ElMessage } from 'element-plus'
import { Plus, Connection, Edit, Delete, CircleCheck, VideoPause, MoreFilled } from '@element-plus/icons-vue'

const loading = ref(false)
const allServers = ref<MCPServerItem[]>([])
const showConnectDialog = ref(false)
const showEditDialog = ref(false)
const editingServer = ref<MCPServerItem | null>(null)
const activeTab = ref('stdio')

const httpServers = computed(() => allServers.value.filter(s => s.type === 'http'))
const stdioServers = computed(() => allServers.value.filter(s => s.type === 'stdio'))

const newServerName = ref('')
const newServerUrl = ref('')
const connecting = ref(false)

const stdioName = ref('')
const stdioCommand = ref('')
const stdioArgs = ref('')
const stdioEnvKey = ref('')
const stdioEnvValue = ref('')
const stdioEnv = ref<Record<string, string>>({})
const stdioConnecting = ref(false)

const editName = ref('')
const editCommand = ref('')
const editArgs = ref('')
const editEnvKey = ref('')
const editEnvValue = ref('')
const editEnv = ref<Record<string, string>>({})
const editConnecting = ref(false)

function getServerDescription(server: MCPServerItem): string {
  const name = server.name?.toLowerCase() || ''
  if (name.includes('filesystem')) return '文件系统操作工具 — 读写、管理容器内文件与目录'
  if (name.includes('github')) return 'GitHub 集成工具 — 管理仓库、Issue、PR 等'
  if (name.includes('git')) return 'Git 版本控制工具 — 代码仓库操作'
  if (name.includes('database') || name.includes('sql') || name.includes('mysql')) return '数据库管理工具 — SQL 查询与数据操作'
  if (name.includes('redis')) return 'Redis 缓存工具 — 键值存储与缓存操作'
  if (name.includes('search') || name.includes('web')) return '网络搜索工具 — 信息检索与网页抓取'
  if (name.includes('docker')) return '容器管理工具 — Docker 容器与镜像操作'
  if (name.includes('slack')) return 'Slack 协作工具 — 消息发送与频道管理'
  if (name.includes('notion')) return 'Notion 知识库工具 — 页面与数据库管理'
  return 'MCP 服务器 — 提供可调用的工具和服务'
}

async function loadServers() {
  loading.value = true
  try {
    const res = await mcpApi.listServers()
    allServers.value = res.data
  } finally {
    loading.value = false
  }
}

async function handleConnect() {
  if (!newServerName.value.trim() || !newServerUrl.value.trim()) {
    ElMessage.warning('请填写 MCP 服务器名称和 URL'); return
  }
  connecting.value = true
  try {
    await mcpApi.createServer({ name: newServerName.value.trim(), type: 'http', config: { url: newServerUrl.value.trim() } })
    ElMessage.success(`已连接 MCP 服务器「${newServerName.value}」`)
    showConnectDialog.value = false
    newServerName.value = ''; newServerUrl.value = ''
    await loadServers()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '连接失败')
  } finally { connecting.value = false }
}

async function handleDisconnect(server: MCPServerItem) {
  try {
    await mcpApi.deleteServer(server.id)
    ElMessage.success(`已断开 MCP 服务器「${server.name}」`)
    await loadServers()
  } catch { ElMessage.error('断开连接失败') }
}

async function handleStdioConnect() {
  if (!stdioName.value.trim() || !stdioCommand.value.trim()) {
    ElMessage.warning('请填写服务器名称和命令'); return
  }
  stdioConnecting.value = true
  try {
    const args = stdioArgs.value.split(/\s+/).map(s => s.trim()).filter(s => s.length > 0)
    await mcpApi.createServer({ name: stdioName.value.trim(), type: 'stdio', config: { command: stdioCommand.value.trim(), args, env: { ...stdioEnv.value } } })
    ElMessage.success(`已启动 Stdio MCP 服务器「${stdioName.value}」`)
    showConnectDialog.value = false
    stdioName.value = ''; stdioCommand.value = ''; stdioArgs.value = ''; stdioEnv.value = {}
    await loadServers()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动失败')
  } finally { stdioConnecting.value = false }
}

async function handleStdioDisconnect(server: MCPServerItem) {
  try {
    await mcpApi.deleteServer(server.id)
    ElMessage.success(`已关闭 MCP 服务器「${server.name}」`)
    await loadServers()
  } catch { ElMessage.error('关闭失败') }
}

function openEditDialog(server: MCPServerItem) {
  editingServer.value = server
  if (server.type === 'stdio') {
    editName.value = server.name; editCommand.value = server.config?.command || ''
    editArgs.value = (server.config?.args || []).join(' '); editEnv.value = { ...(server.config?.env || {}) }
  } else {
    editName.value = server.name; editCommand.value = server.config?.url || ''; editArgs.value = ''; editEnv.value = {}
  }
  editEnvKey.value = ''; editEnvValue.value = ''
  showEditDialog.value = true
}

async function handleUpdate() {
  if (!editingServer.value) return
  if (!editName.value.trim() || (editingServer.value.type === 'stdio' && !editCommand.value.trim())) {
    ElMessage.warning('请填写必填字段'); return
  }
  editConnecting.value = true
  try {
    if (editingServer.value.type === 'stdio') {
      const args = editArgs.value.split(/\s+/).map(s => s.trim()).filter(s => s.length > 0)
      await mcpApi.updateServer(editingServer.value.id, { name: editName.value.trim(), type: 'stdio', config: { command: editCommand.value.trim(), args, env: { ...editEnv.value } } })
    } else {
      await mcpApi.updateServer(editingServer.value.id, { name: editName.value.trim(), type: 'http', config: { url: editCommand.value.trim() } })
    }
    ElMessage.success(`已更新 MCP 服务器「${editingServer.value.name}」`)
    showEditDialog.value = false; editingServer.value = null
    await loadServers()
  } catch (err: any) { ElMessage.error(err?.response?.data?.detail || '更新失败') }
  finally { editConnecting.value = false }
}

onMounted(() => { loadServers() })
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2>🔌 MCP 扩展</h2>
        <p class="page-desc">管理和连接 MCP 服务器。支持 <strong>Stdio</strong>（本地子进程）和 <strong>HTTP</strong>（远端网关）两种模式。</p>
      </div>
      <el-button type="primary" @click="showConnectDialog = true">
        <el-icon><Plus /></el-icon> 新建 MCP
      </el-button>
    </div>

    <el-dialog v-model="showConnectDialog" title="连接 MCP 服务器" width="580px">
      <el-tabs v-model="activeTab" stretch>
        <el-tab-pane label="本地子进程 (Stdio)" name="stdio">
          <p style="font-size: 12px; color: #64748b; margin-bottom: 12px">启动一个标准 MCP Server 子进程。</p>
          <el-alert title="Docker 环境提示" type="warning" :closable="false" show-icon style="margin-bottom: 14px; font-size: 12px">
            <template #default><p style="margin: 0; line-height: 1.6">Docker 环境下 MCP 只能访问容器内路径。默认工作目录为 <code>/app/workspace</code>。</p></template>
          </el-alert>
          <el-form label-position="top">
            <el-form-item label="服务器名称" required><el-input v-model="stdioName" placeholder="例如：filesystem" /></el-form-item>
            <el-form-item label="命令 (command)" required><el-input v-model="stdioCommand" placeholder="例如：npx / python / uvx" /></el-form-item>
            <el-form-item label="参数 (args)">
              <el-input v-model="stdioArgs" placeholder="空格分隔" />
              <p style="font-size: 11px; color: #909399; margin-top: 4px">多个参数用空格分隔。</p>
            </el-form-item>
            <el-form-item label="环境变量 (env)">
              <div style="display: flex; gap: 8px; width: 100%; margin-bottom: 8px">
                <el-input v-model="stdioEnvKey" placeholder="KEY" style="flex: 1" /><el-input v-model="stdioEnvValue" placeholder="VALUE" style="flex: 2" />
                <el-button @click="() => { if (stdioEnvKey.value.trim()) { stdioEnv.value[stdioEnvKey.value.trim()] = stdioEnvValue.value; stdioEnvKey.value = ''; stdioEnvValue.value = '' } }">添加</el-button>
              </div>
              <div v-if="Object.keys(stdioEnv).length > 0" style="display: flex; flex-wrap: wrap; gap: 6px">
                <el-tag v-for="(v, k) in stdioEnv" :key="k" closable @close="delete stdioEnv.value[k]">{{ k }}={{ v }}</el-tag>
              </div>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="远端服务器 (HTTP)" name="http">
          <p style="font-size: 12px; color: #64748b; margin-bottom: 12px">连接远端 HTTP 服务的 MCP 兼容网关。</p>
          <el-form label-position="top">
            <el-form-item label="服务器名称" required><el-input v-model="newServerName" placeholder="例如：my-mcp-server" /></el-form-item>
            <el-form-item label="服务器 URL" required><el-input v-model="newServerUrl" placeholder="http://localhost:8001/mcp" /></el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>
      <div style="margin-top: 20px; text-align: right">
        <el-button @click="showConnectDialog = false">取消</el-button>
        <el-button v-if="activeTab === 'stdio'" type="primary" :loading="stdioConnecting" @click="handleStdioConnect">启动</el-button>
        <el-button v-if="activeTab === 'http'" type="primary" :loading="connecting" @click="handleConnect">连接</el-button>
      </div>
    </el-dialog>

    <el-dialog v-model="showEditDialog" :title="'编辑 MCP 服务器 — ' + (editingServer?.name || '')" width="580px">
      <template v-if="editingServer">
        <el-form label-position="top" v-if="editingServer.type === 'stdio'">
          <el-form-item label="服务器名称" required><el-input v-model="editName" /></el-form-item>
          <el-form-item label="命令" required><el-input v-model="editCommand" /></el-form-item>
          <el-form-item label="参数"><el-input v-model="editArgs" /></el-form-item>
        </el-form>
        <el-form label-position="top" v-else>
          <el-form-item label="服务器名称" required><el-input v-model="editName" /></el-form-item>
          <el-form-item label="URL" required><el-input v-model="editCommand" /></el-form-item>
        </el-form>
      </template>
      <div style="margin-top: 20px; text-align: right">
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="editConnecting" @click="handleUpdate">保存</el-button>
      </div>
    </el-dialog>

    <div v-loading="loading" class="card-list">
      <el-card v-for="server in allServers" :key="server.id" shadow="never" class="mcp-card">
        <template #header>
          <div class="mcp-header">
            <div class="mcp-header-left">
              <span class="mcp-name">{{ server.name }}</span>
              <span class="mcp-tag" :class="server.type === 'stdio' ? 'tag-stdio' : 'tag-http'">
                {{ server.type === 'stdio' ? 'Stdio' : 'HTTP' }}
              </span>
            </div>
            <div class="mcp-header-right">
              <span :class="'mcp-dot ' + (server.status === 'running' ? 'dot-ok' : server.status === 'starting' ? 'dot-warn' : 'dot-err')"></span>
              <span class="mcp-status-text">{{ server.status === 'running' ? '运行中' : server.status === 'starting' ? '启动中' : server.status || 'unknown' }}</span>
            </div>
          </div>
        </template>
        <p class="mcp-desc">{{ getServerDescription(server) }}</p>
        <div class="mcp-bottom">
          <div class="mcp-bottom-left">
            <span class="mcp-cmd-label">COMMAND</span>
            <code class="mcp-cmd">{{ server.type === 'stdio' ? `${server.config?.command || ''} ${(server.config?.args || []).join(' ')}` : server.config?.url }}</code>
          </div>
          <div class="mcp-bottom-right">
            <span v-if="server.tool_count !== undefined" class="mcp-tools">{{ server.tool_count }} 个工具</span>
            <div class="mcp-actions">
              <button class="mcp-act mcp-act-edit" @click.stop="openEditDialog(server)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> 编辑</button>
              <button class="mcp-act mcp-act-del" @click.stop="server.type === 'stdio' ? handleStdioDisconnect(server) : handleDisconnect(server)"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg> 删除</button>
            </div>
          </div>
        </div>
      </el-card>

      <div v-if="!loading && allServers.length === 0" class="empty-state">
        <el-icon style="font-size: 48px; margin-bottom: 16px; color: #94a3b8"><Connection /></el-icon>
        <p style="font-size: 15px">暂无已连接的 MCP 服务器</p>
        <p style="font-size: 12px; margin-top: 8px; color: #94a3b8">点击右上角「连接 MCP 服务器」按钮添加</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 卡片容器 ===== */
.mcp-card {
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  background: #fff;
  transition: box-shadow var(--transition-base), border-color var(--transition-base), transform var(--transition-base);
  overflow: hidden;
}
.mcp-card:hover {
  border-color: #c7d2fe;
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
.mcp-card :deep(.el-card__body) {
  padding: 12px 20px 16px;
}
.mcp-card :deep(.el-card__header) {
  padding: 14px 20px;
  background: linear-gradient(135deg, #faf5ff 0%, #ede9fe 100%);
  border-bottom: 1px solid #e4d5f5;
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
}

.mcp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mcp-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.mcp-name {
  font-weight: 700;
  font-size: 16px;
  color: #1e293b;
  letter-spacing: 0.01em;
}
.mcp-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 9999px;
  line-height: 1.6;
}
.tag-stdio { background: #ede9fe; color: #6d28d9; }
.tag-http  { background: #d1fae5; color: #047857; }

.mcp-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}
.mcp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-ok  { background: #10b981; box-shadow: 0 0 6px rgba(16,185,129,0.4); }
.dot-warn { background: #f59e0b; box-shadow: 0 0 6px rgba(245,158,11,0.4); animation: pulse-warn 1.5s infinite; }
.dot-err { background: #9ca3af; }
@keyframes pulse-warn { 0%,100%{opacity:1} 50%{opacity:0.5} }

.mcp-status-text {
  font-size: 13px;
  font-weight: 500;
  color: #475569;
}

/* ===== 描述 ===== */
.mcp-desc {
  margin: 0 0 12px 0;
  font-size: 13px;
  color: #64748b;
  line-height: 1.6;
}

/* ===== 底行：命令 + 工具数 + 操作 ===== */
.mcp-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.mcp-bottom-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.mcp-cmd-label {
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  letter-spacing: 0.08em;
  flex-shrink: 0;
}
.mcp-cmd {
  background: #f1f5f9;
  color: #334155;
  padding: 4px 10px;
  border-radius: 6px;
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 12px;
  word-break: break-all;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.mcp-bottom-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}
.mcp-tools {
  font-size: 12px;
  font-weight: 500;
  color: var(--primary-color);
  white-space: nowrap;
}
.mcp-actions {
  display: flex;
  gap: 6px;
}
.mcp-act {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1.4;
}
.mcp-act-edit {
  color: #4f46e5;
  background: #eef2ff;
}
.mcp-act-edit:hover {
  background: #c7d2fe;
  color: #3730a3;
}
.mcp-act-del {
  color: #dc2626;
  background: #fef2f2;
}
.mcp-act-del:hover {
  background: #fecaca;
  color: #b91c1c;
}

.empty-state { grid-column: 1/-1; text-align: center; padding: 60px; color: #64748b; }
</style>