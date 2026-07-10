<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { skillApi } from '../api/skills'
import { ElMessage, ElMessageBox } from 'element-plus'

const loading = ref(false)
const executions = ref<any[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')

// Detail dialog
const detailVisible = ref(false)
const detailData = ref<any>(null)
const detailLoading = ref(false)

const statusColor: Record<string, string> = {
  completed: '#67c23a',
  failed: '#f56c6c',
  running: '#409eff',
}

async function loadExecutions() {
  loading.value = true
  try {
    const res = await skillApi.listExecutions({
      status: statusFilter.value || undefined,
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
    })
    executions.value = res.data.items
    total.value = res.data.total
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

function viewDetail(exec: any) {
  detailLoading.value = true
  detailVisible.value = true
  skillApi.getExecutionDetail(exec.id).then(res => {
    detailData.value = res.data
  }).catch(() => {
    detailData.value = exec
  }).finally(() => {
    detailLoading.value = false
  })
}

async function confirmDelete(exec: any) {
  try {
    await ElMessageBox.confirm(
      `确定要删除这条执行记录吗？`,
      '确认删除',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    await skillApi.deleteExecution(exec.id)
    ElMessage.success('已删除')
    await loadExecutions()
  } catch {
    // cancelled
  }
}

onMounted(() => {
  loadExecutions()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Skills 执行历史</h2>
    </div>

    <!-- Filters -->
    <div style="display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap">
      <el-select v-model="statusFilter" placeholder="状态筛选" clearable size="small" style="width: 130px">
        <el-option label="已完成" value="completed" />
        <el-option label="失败" value="failed" />
        <el-option label="运行中" value="running" />
      </el-select>
      <el-button size="small" type="primary" @click="loadExecutions">
        <el-icon style="margin-right: 4px"><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <!-- Table -->
    <div v-loading="loading">
      <el-table :data="executions" stripe style="width: 100%" empty-text="暂无执行记录">
        <el-table-column prop="skill_display_name" label="技能名称" min-width="140" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :color="statusColor[row.status] || '#909399'" effect="dark" size="small" style="color: #fff">
              {{ row.status === 'completed' ? '完成' : row.status === 'failed' ? '失败' : '运行中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="user_query" label="用户查询" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.user_query || '-' }}</template>
        </el-table-column>
        <el-table-column prop="started_at" label="执行时间" width="170" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewDetail(row)">详情</el-button>
            <el-button size="small" type="danger" @click="confirmDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="display: flex; justify-content: center; margin-top: 16px">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="loadExecutions"
          @size-change="loadExecutions"
        />
      </div>
    </div>

    <!-- Detail Dialog with Trace Log -->
    <el-dialog v-model="detailVisible" title="执行详情" width="650px" :close-on-click-modal="false">
      <div v-loading="detailLoading">
        <div v-if="detailData" style="font-size: 13px">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="技能名称">{{ detailData.skill_display_name }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :color="statusColor[detailData.status] || '#909399'" effect="dark" size="small" style="color: #fff">
                {{ detailData.status === 'completed' ? '完成' : detailData.status === 'failed' ? '失败' : '运行中' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="Session ID" :span="2">{{ detailData.session_id }}</el-descriptions-item>
            <el-descriptions-item label="用户查询" :span="2">{{ detailData.user_query || '-' }}</el-descriptions-item>
          </el-descriptions>

          <!-- Final Output -->
          <div v-if="detailData.output" style="margin-top: 12px">
            <div style="font-weight: 600; font-size: 14px; margin-bottom: 6px">最终输出</div>
            <div style="background: #f5f7fa; padding: 10px; border-radius: 4px; white-space: pre-wrap; max-height: 250px; overflow-y: auto; font-size: 12px">
              {{ detailData.output }}
            </div>
          </div>

          <!-- Error -->
          <div v-if="detailData.error_msg" style="margin-top: 12px">
            <div style="font-weight: 600; font-size: 14px; color: #f56c6c; margin-bottom: 6px">错误信息</div>
            <div style="background: #fef0f0; padding: 10px; border-radius: 4px; white-space: pre-wrap; font-size: 12px; color: #f56c6c">
              {{ detailData.error_msg }}
            </div>
          </div>

          <!-- Trace Log (text only, no timeline) -->
          <div v-if="detailData.trace_log && detailData.trace_log.length > 0" style="margin-top: 16px">
            <div style="font-weight: 600; font-size: 14px; margin-bottom: 8px">
              Trace 日志
            </div>
            <div style="background: #1d1e2c; padding: 10px; border-radius: 4px; max-height: 300px; overflow-y: auto">
              <div v-for="(evt, i) in detailData.trace_log" :key="i" style="font-size: 11px; color: #e0e0e0; font-family: 'Courier New', monospace; margin-bottom: 4px; line-height: 1.6">
                <span style="color: #909399">{{ evt.timestamp?.slice(0, 19) || '---' }}</span>
                <span :style="{ color: evt.event.includes('error') ? '#f56c6c' : evt.event.includes('complete') ? '#67c23a' : '#409eff' }">
                  [{{ evt.event }}]
                </span>
                <span v-if="evt.skill_name"> {{ evt.skill_name }}</span>
                <span v-if="evt.query"> ➜ {{ evt.query }}</span>
                <span v-if="evt.output_length !== undefined"> ({{ evt.output_length }} chars)</span>
                <span v-if="evt.error" style="color: #f56c6c"> ❌ {{ evt.error }}</span>
                <span v-if="evt.tool_name" style="color: #e6a23c"> 🔧 {{ evt.tool_name }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else style="color: #909399; text-align: center; padding: 40px">暂无详情数据</div>
      </div>

      <template #footer>
        <el-button size="small" @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>