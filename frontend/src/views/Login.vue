<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { ElMessage } from 'element-plus'
import baLogo from '../assets/images/BA.png'

const router = useRouter()
const authStore = useAuthStore()

const isRegister = ref(false)
const loading = ref(false)

const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名长度 3-50 字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入有效邮箱', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 100, message: '密码长度 6-100 字符', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    {
      validator: (_rule: any, value: string, callback: any) => {
        if (value !== form.password) {
          callback(new Error('两次输入密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

const formRef = ref()

async function handleSubmit() {
  if (!formRef.value) {
    ElMessage.error('表单组件未加载，请刷新页面重试')
    return
  }

  let valid: boolean
  try {
    valid = await formRef.value.validate()
  } catch (e) {
    console.error('表单验证异常:', e)
    ElMessage.error('表单验证出错，请检查输入')
    return
  }
  if (!valid) return

  loading.value = true
  try {
    if (isRegister.value) {
      await authStore.register(form.username, form.email, form.password)
    } else {
      await authStore.login(form.username, form.password)
    }
    router.push('/chat')
  } catch (error: any) {
    // 登录失败时，只清空密码字段，保留用户名
    form.password = ''
    form.confirmPassword = ''
    console.error('登录请求失败:', error)
    // 网络错误（后端未启动）时显示提示
    if (!error.response) {
      ElMessage.error('无法连接到服务器，请检查后端服务是否启动')
    } else if (error.response?.status === 401) {
      // 401 由 client.ts 拦截器统一处理，这里只是兜底
      const detail = error.response?.data?.detail
      if (detail) ElMessage.error(detail)
    } else {
      // 其他 HTTP 错误（500 等）显示后端返回的错误信息
      const message = error.response?.data?.detail || error.message || '登录失败，请重试'
      ElMessage.error(message)
    }
  } finally {
    loading.value = false
  }
}

function toggleMode() {
  isRegister.value = !isRegister.value
  form.username = ''
  form.email = ''
  form.password = ''
  form.confirmPassword = ''
  formRef.value?.resetFields()
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div style="display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 6px;">
        <img :src="baLogo" alt="BaseAgent Logo" style="width: 32px; height: 32px; object-fit: contain;" />
        <h2 style="margin: 0;">BaseAgent</h2>
      </div>
      <p class="subtitle">智能知识库与Agent平台</p>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @keydown.enter="handleSubmit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            :prefix-icon="'User'"
            size="large"
          />
        </el-form-item>

        <el-form-item v-if="isRegister" label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            placeholder="请输入邮箱"
            :prefix-icon="'Message'"
            size="large"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="'Lock'"
            size="large"
            show-password
          />
        </el-form-item>

        <el-form-item v-if="isRegister" label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            :prefix-icon="'Lock'"
            size="large"
            show-password
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            style="width: 100%"
            @click="handleSubmit"
          >
            {{ isRegister ? '注册' : '登录' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div style="text-align: center">
        <el-button type="info" link @click="toggleMode">
          {{ isRegister ? '已有账号？去登录' : '没有账号？去注册' }}
        </el-button>
      </div>
    </div>
  </div>
</template>