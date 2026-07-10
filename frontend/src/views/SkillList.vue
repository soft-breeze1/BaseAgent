<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { skillApi, type SkillItem } from '../api/skills'
import { Collection } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const loading = ref(false)
const skills = ref<SkillItem[]>([])

async function loadSkills() {
  loading.value = true
  try {
    await skillApi.sync()
    const res = await skillApi.list()
    skills.value = res.data.items
  } catch {} finally {
    loading.value = false
  }
}

onMounted(() => { loadSkills() })

function getIcon(file: string): string {
  if (file.endsWith('/')) return '📁'
  if (file.endsWith('.md')) return '📝'
  if (file.endsWith('.py')) return '🐍'
  if (file.endsWith('.js') || file.endsWith('.ts')) return '⚡'
  if (file.endsWith('.json')) return '📋'
  if (file.endsWith('.yaml') || file.endsWith('.yml')) return '⚙️'
  return '📄'
}

function getIndent(file: string): number {
  const parts = file.split('/')
  return parts.length - 1
}
</script>

<template>
  <div>
    <div class="page-header">
      <div>
        <h2>⚙️ Skills 管理</h2>
        <p class="page-desc">管理和查看已安装的技能包（Skills），每个技能包包含 <code>SKILL.md</code> 描述文件和执行脚本。</p>
      </div>
      <el-button type="primary" @click="ElMessage.info('请将包含 SKILL.md 的技能包解压到 skills/ 目录')">
        <el-icon><Plus /></el-icon> 新建 Skill
      </el-button>
    </div>

    <div v-loading="loading" class="card-list">
      <el-card v-for="skill in skills" :key="skill.name" shadow="never" class="skill-card">
        <template #header>
          <div class="skill-card-header">
            <span class="skill-card-name">{{ skill.name }}</span>
          </div>
        </template>
        <div class="skill-files">
          <div v-for="file in skill.files" :key="file" class="skill-file-row" :style="{ paddingLeft: 12 + getIndent(file) * 20 + 'px' }">
            <span class="skill-file-icon">{{ getIcon(file) }}</span>
            <span class="skill-file-path">{{ file }}</span>
          </div>
        </div>
      </el-card>
      <div v-if="!loading && skills.length === 0" class="empty-state">
        <el-icon style="font-size: 48px; margin-bottom: 16px; color: #94a3b8"><Collection /></el-icon>
        <p style="font-size: 15px">暂无已发现的 Skills</p>
        <p style="font-size: 12px; margin-top: 8px; color: #94a3b8">将包含 <code>SKILL.md</code> 的技能包解压到 <code>skills/</code> 目录</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skill-card {
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color);
  background: #fff;
  transition: box-shadow var(--transition-base), border-color var(--transition-base), transform var(--transition-base);
  overflow: hidden;
}
.skill-card:hover {
  border-color: #a7f3d0;
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
.skill-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-light);
}
.skill-card :deep(.el-card__body) {
  padding: 12px 20px 16px;
}

.skill-card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 0;
}
.skill-card :deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-light);
  background: linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%);
}
.skill-card-name {
  font-weight: 700;
  font-size: 16px;
  color: #4338ca;
  letter-spacing: 0.01em;
}

.skill-files {
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: #fafbfc;
}
.skill-file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-light);
  transition: background 0.15s;
}
.skill-file-row:last-child {
  border-bottom: none;
}
.skill-file-row:hover {
  background: #f1f5f9;
}
.skill-file-icon {
  font-size: 15px;
  flex-shrink: 0;
  width: 22px;
  text-align: center;
}
.skill-file-path {
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

</style>