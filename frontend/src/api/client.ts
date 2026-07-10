import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

// Request interceptor: attach Bearer token
apiClient.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.token) {
    config.headers.Authorization = `Bearer ${authStore.token}`
  }
  return config
})

// Response interceptor: handle 401
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const isLoginRequest = error.config?.url?.includes('/auth/login')
    // 非登录请求的401：清除token跳转登录页
    if (error.response?.status === 401 && !isLoginRequest) {
      const authStore = useAuthStore()
      authStore.logout()
      window.location.href = '/login'
      return Promise.reject(error)
    }
    // 登录请求的401：只显示错误提示（不跳转）
    if (error.response?.status === 401 && isLoginRequest) {
      const message = error.response?.data?.detail || '账号或密码不正确'
      ElMessage.error(message)
    }
    return Promise.reject(error)
  }
)

export default apiClient