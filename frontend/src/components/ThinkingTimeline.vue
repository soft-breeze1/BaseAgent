<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps<{
  steps: string[]
  isStreaming: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

// ── 状态 ──
const expanded = ref(false)
const hasTokenStarted = ref(false)
const autoCollapsed = ref(false)

// 展开/收起
function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) autoCollapsed.value = false
  emit('toggle')
}

// 检测 token 开始 → 自动折叠
watch(() => props.isStreaming, (streaming) => {
  if (!streaming) return
  if (!hasTokenStarted.value) {
    hasTokenStarted.value = true
    // 延迟一小段时间确保 tokens 已开始渲染
    setTimeout(() => {
      expanded.value = false
      autoCollapsed.value = true
    }, 300)
  }
})

// 重置
watch(() => props.steps.length, (n, old) => {
  if (n === 0) {
    expanded.value = false
    hasTokenStarted.value = false
    autoCollapsed.value = false
  } else if (old === 0) {
    expanded.value = true
    hasTokenStarted.value = false
    autoCollapsed.value = false
  }
})

// ── 步骤解析 ──

interface StepNode {
  icon: string
  label: string
  detail: string
  color: string
  status: 'active' | 'completed' | 'pending'
}

const stepNodes = computed<StepNode[]>(() => {
  return props.steps.map((text, i) => {
    const isLast = i === props.steps.length - 1
    const status = isLast && props.isStreaming ? 'active' : 'completed'
    const match = classifyStep(text)
    return {
      icon: match.icon,
      label: match.label,
      detail: text,
      color: match.color,
      status,
    }
  })
})

function classifyStep(text: string): { icon: string; label: string; color: string } {
  const t = text.toLowerCase()

  if (/分析|意图|问题类别|需求/i.test(t)) {
    return { icon: '🧠', label: '需求分析', color: '#6366f1' }
  }
  if (/召回|工具|匹配/i.test(t)) {
    return { icon: '🔧', label: '工具匹配', color: '#8b5cf6' }
  }
  if (/react|第.*轮|推理|决策|决定/i.test(t)) {
    return { icon: '🔁', label: '推理决策', color: '#2563eb' }
  }
  if (/搜索|调用|执行|工具.*结果/i.test(t)) {
    return { icon: '🖥️', label: '工具执行', color: '#0891b2' }
  }
  if (/生成|回答|最终|思考中/i.test(t)) {
    return { icon: '✍️', label: '回答生成', color: '#059669' }
  }
  if (/失败|错误|异常|兜底|超时/i.test(t)) {
    return { icon: '⚠️', label: '异常提示', color: '#dc2626' }
  }
  if (/知识库|rag|检索/i.test(t)) {
    return { icon: '📚', label: '知识检索', color: '#d97706' }
  }
  if (/技能|skill|加载/i.test(t)) {
    return { icon: '📋', label: '技能加载', color: '#7c3aed' }
  }
  if (/完成|成功|done/i.test(t)) {
    return { icon: '✅', label: '完成', color: '#16a34a' }
  }

  return { icon: '⚙️', label: '处理中', color: '#64748b' }
}

function formatTime(): string {
  const now = new Date()
  return now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
</script>

<template>
  <!-- 浮动卡片（折叠后） -->
  <div v-if="autoCollapsed && stepNodes.length > 0" class="timeline-floating-pill" @click="toggle">
    <span class="pill-icon">🧠</span>
    <span class="pill-text">查看思考过程</span>
    <span class="pill-badge">{{ stepNodes.length }}</span>
  </div>

  <!-- 时间线主体 -->
  <div v-if="expanded || (!autoCollapsed && stepNodes.length > 0)" class="timeline-container">
    <div class="timeline-toggle" @click="toggle">
      <span class="toggle-arrow" :class="{ open: expanded }">▶</span>
      <span class="toggle-label">思考过程</span>
      <span class="toggle-badge">{{ stepNodes.length }} 步</span>
    </div>

    <TransitionGroup name="tl" tag="div" class="timeline-body" v-show="expanded">
      <div v-for="(node, i) in stepNodes" :key="i" class="tl-node" :class="node.status">
        <!-- 时间线竖线 -->
        <div class="tl-line" :style="{ borderColor: node.color }">
          <div v-if="i < stepNodes.length - 1" class="tl-connector" :style="{ background: stepNodes[i + 1]?.color || '#e2e8f0' }"></div>
        </div>
        <!-- 图标 -->
        <div class="tl-icon-wrap" :style="{ background: node.status === 'active' ? node.color : '#f1f5f9', borderColor: node.color }">
          <span v-if="node.status === 'active'" class="tl-icon tl-spin">{{ node.icon }}</span>
          <span v-else class="tl-icon">{{ node.icon }}</span>
        </div>
        <!-- 内容 -->
        <div class="tl-content">
          <div class="tl-label" :style="{ color: node.color }">{{ node.label }}</div>
          <div class="tl-detail">{{ node.detail }}</div>
          <div class="tl-time">{{ formatTime() }}</div>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
/* ── 浮动卡片 ── */
.timeline-floating-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  cursor: pointer;
  font-size: 13px;
  color: #475569;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: all 0.2s;
  user-select: none;
  margin-bottom: 8px;
}
.timeline-floating-pill:hover {
  border-color: #6366f1;
  color: #6366f1;
  box-shadow: 0 4px 12px rgba(99,102,241,0.15);
}
.pill-icon { font-size: 14px; }
.pill-text { font-weight: 500; }
.pill-badge {
  background: #6366f1;
  color: #fff;
  border-radius: 10px;
  padding: 0 7px;
  font-size: 11px;
  line-height: 18px;
  font-weight: 600;
}

/* ── 容器 ── */
.timeline-container {
  margin-bottom: 12px;
  border-left: none;
}

/* ── 折叠头 ── */
.timeline-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 6px 0;
  user-select: none;
}
.toggle-arrow {
  font-size: 10px;
  transition: transform 0.25s ease;
  color: #6366f1;
  font-weight: bold;
}
.toggle-arrow.open { transform: rotate(90deg); }
.toggle-label {
  font-weight: 600;
  font-size: 13px;
  color: #334155;
}
.toggle-badge {
  background: #eef2ff;
  color: #6366f1;
  border-radius: 10px;
  padding: 0 8px;
  font-size: 11px;
  line-height: 18px;
  font-weight: 600;
}

/* ── 时间线主体 ── */
.timeline-body {
  position: relative;
  padding-left: 0;
}

/* ── 节点 ── */
.tl-node {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 0;
  position: relative;
}

/* ── 竖线 ── */
.tl-line {
  width: 2px;
  flex-shrink: 0;
  margin-left: 18px;
  min-height: 20px;
  position: relative;
}
.tl-connector {
  position: absolute;
  top: 0;
  left: 0;
  width: 2px;
  height: 100%;
}

/* ── 图标 ── */
.tl-icon-wrap {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid;
  flex-shrink: 0;
  transition: all 0.3s;
}
.tl-icon { font-size: 16px; line-height: 1; }
.tl-spin { animation: tl-spin 2s linear infinite; }

@keyframes tl-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── 内容 ── */
.tl-content {
  flex: 1;
  min-width: 0;
  padding-top: 2px;
}
.tl-label {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}
.tl-detail {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
  line-height: 1.4;
  word-break: break-word;
}
.tl-time {
  font-size: 10px;
  color: #cbd5e1;
  margin-top: 2px;
}

/* ── 节点状态 ── */
.tl-node.completed .tl-icon-wrap { border-color: #e2e8f0 !important; }
.tl-node.completed .tl-icon { opacity: 0.7; }
.tl-node.completed .tl-label { opacity: 0.7; }
.tl-node.active .tl-icon-wrap { 
  box-shadow: 0 0 0 3px rgba(99,102,241,0.2);
}

/* ── TransitionGroup 动画 ── */
.tl-enter-active {
  transition: all 0.35s ease;
}
.tl-enter-from {
  opacity: 0;
  transform: translateY(-12px);
}
.tl-leave-active {
  transition: all 0.2s ease;
}
.tl-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
.tl-move {
  transition: transform 0.3s ease;
}
</style>