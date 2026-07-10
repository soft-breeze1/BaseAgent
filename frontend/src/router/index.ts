import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { guest: true },
  },
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('../views/Chat.vue'),
  },
  {
    path: '/knowledge',
    name: 'KnowledgeList',
    component: () => import('../views/KnowledgeList.vue'),
  },
  {
    path: '/knowledge/:id',
    name: 'KnowledgeDetail',
    component: () => import('../views/KnowledgeDetail.vue'),
  },
  {
    path: '/models',
    name: 'ModelList',
    component: () => import('../views/ModelList.vue'),
  },
  {
    path: '/tools',
    name: 'ToolList',
    component: () => import('../views/ToolList.vue'),
  },
  {
    path: '/mcp',
    name: 'MCPList',
    component: () => import('../views/MCPList.vue'),
  },
  {
    path: '/skills',
    name: 'SkillList',
    component: () => import('../views/SkillList.vue'),
  },
  {
    path: '/system-prompt',
    name: 'SystemPrompt',
    component: () => import('../views/SystemPrompt.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()

  if (to.meta.guest) {
    if (authStore.isAuthenticated) {
      return next('/chat')
    }
    return next()
  }

  if (!authStore.isAuthenticated) {
    return next('/login')
  }
  next()
})

export default router