<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import UserProfile from './components/UserProfile.vue'
import baLogo from './assets/images/BA.png'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

// 页面初始化时自动加载用户信息和profile
onMounted(() => {
  authStore.init()
})

const isLoginPage = computed(() => route.name === 'Login')

// 个人信息对话框可见性
const profileDialogVisible = ref(false)

// 显示用的昵称和头像直接从authStore获取
const displayName = computed(() => authStore.displayName)
const avatarUrl = computed(() => authStore.avatarUrl)

const nameChar = computed(() => {
  const name = displayName.value || '?'
  return name.charAt(0)
})

const avatarColors = [
  '#409EFF', '#67C23A', '#E6A23C', '#F56C6C',
  '#909399', '#1890FF', '#52C41A', '#FAAD14',
]
const avatarColor = computed(() => {
  const name = displayName.value || '?'
  const index = name.charCodeAt(0) % avatarColors.length
  return avatarColors[index]
})

// 点击用户信息区域打开个人信息设置
const handleUserClick = () => {
  profileDialogVisible.value = true
}

// 个人信息更新后刷新
const handleProfileUpdate = () => {
  authStore.fetchProfile()
}

const menuItems = [
  { path: '/chat', icon: 'ChatDotRound', label: '智能对话', name: 'Chat' },
  { path: '/knowledge', icon: 'Document', label: '知识库管理', name: 'KnowledgeList' },
  { path: '/models', icon: 'Cpu', label: '模型配置', name: 'ModelList' },
  { path: '/system-prompt', icon: 'EditPen', label: '系统提示词', name: 'SystemPrompt' },
  { path: '/tools', icon: 'SetUp', label: 'Tools 管理', name: 'ToolList' },
  { path: '/mcp', icon: 'Connection', label: 'MCP 扩展', name: 'MCPList' },
  { path: '/skills', icon: 'Collection', label: 'Skills 管理', name: 'SkillList' },
]

const handleMenuClick = (item: typeof menuItems[0]) => {
  router.push(item.path)
}

const handleLogout = () => {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <router-view v-if="isLoginPage" />

  <div v-else class="app-layout">
    <!-- Sidebar -->
    <aside class="sidebar">
      <div class="sidebar-logo" @click="router.push('/chat')" style="cursor: pointer;" aria-label="返回首页">
        <img :src="baLogo" alt="BaseAgent Logo" class="brand-img" />
        <span>BaseAgent</span>
      </div>
      <nav class="sidebar-menu">
        <div
          v-for="item in menuItems"
          :key="item.path"
          class="menu-item"
          :class="{ active: route.path.startsWith(item.path) }"
          @click="handleMenuClick(item)"
        >
          <el-icon class="menu-icon"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </div>
      </nav>
      <div class="sidebar-footer">
        <!-- 用户信息区域 -->
        <div class="sidebar-user" @click="handleUserClick">
          <div class="sidebar-user-avatar">
            <img v-if="avatarUrl" :src="avatarUrl" alt="avatar" />
            <span v-else :style="{ background: avatarColor }">{{ nameChar }}</span>
          </div>
          <span class="sidebar-user-name">{{ displayName }}</span>
        </div>
        <!-- 退出登录按钮 -->
        <el-button size="small" class="sidebar-logout-btn" @click="handleLogout">
          退出登录
        </el-button>
      </div>
    </aside>

    <!-- Main -->
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>

    <!-- 个人信息设置对话框 -->
    <UserProfile v-model:visible="profileDialogVisible" @update="handleProfileUpdate" />
  </div>
</template>
