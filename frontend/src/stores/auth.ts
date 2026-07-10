import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type UserInfo } from '../api/auth'
import { userApi, type UserProfile } from '../api/user'
import { ElMessage } from 'element-plus'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('token'))
  const refreshToken = ref<string | null>(localStorage.getItem('refreshToken'))
  const user = ref<UserInfo | null>(null)

  // 扩展用户信息（头像、昵称等，从 user_profiles 表获取）
  const profile = ref<UserProfile | null>(null)

  // 初始化标记
  const initialized = ref(false)

  const isAuthenticated = computed(() => !!token.value)

  // 显示用的昵称：优先使用 profile.nickname，再使用 user.username
  const displayName = computed(() => {
    return profile.value?.nickname || user.value?.username || ''
  })

  // 显示用的头像URL
  const avatarUrl = computed(() => {
    return profile.value?.avatar || ''
  })

  // 初始化：页面刷新时自动加载用户信息
  async function init() {
    if (!token.value || initialized.value) return
    initialized.value = true
    try {
      const res = await authApi.getMe()
      user.value = res.data
    } catch {
      // token无效，清除
      logout()
      return
    }
    try {
      const res = await userApi.getInfo()
      profile.value = res.data
    } catch {
      // profile加载失败不影响使用
    }
  }

  async function login(username: string, password: string) {
    const res = await authApi.login({ username, password })
    token.value = res.data.access_token
    refreshToken.value = res.data.refresh_token
    localStorage.setItem('token', res.data.access_token)
    localStorage.setItem('refreshToken', res.data.refresh_token)
    initialized.value = true
    await fetchUser()
    await fetchProfile()
    ElMessage.success('登录成功')
  }

  async function register(username: string, email: string, password: string) {
    const res = await authApi.register({ username, email, password })
    token.value = res.data.access_token
    refreshToken.value = res.data.refresh_token
    localStorage.setItem('token', res.data.access_token)
    localStorage.setItem('refreshToken', res.data.refresh_token)
    initialized.value = true
    await fetchUser()
    await fetchProfile()
    ElMessage.success('注册成功')
  }

  async function fetchUser() {
    try {
      const res = await authApi.getMe()
      user.value = res.data
    } catch {
      // silently fail
    }
  }

  async function fetchProfile() {
    try {
      const res = await userApi.getInfo()
      profile.value = res.data
    } catch {
      // silently fail
    }
  }

  async function logout() {
    token.value = null
    refreshToken.value = null
    user.value = null
    profile.value = null
    initialized.value = false
    localStorage.removeItem('token')
    localStorage.removeItem('refreshToken')
  }

  return {
    token,
    refreshToken,
    user,
    profile,
    displayName,
    avatarUrl,
    isAuthenticated,
    initialized,
    init,
    login,
    register,
    fetchUser,
    fetchProfile,
    logout,
  }
})
