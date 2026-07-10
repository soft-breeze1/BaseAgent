<!-- 个人信息设置模态框 -->
<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { userApi, type UserProfile } from '../api/user'
import { useAuthStore } from '../stores/auth'
import { useRouter } from 'vue-router'

const emit = defineEmits(['update'])
const authStore = useAuthStore()
const router = useRouter()

// 对话框可见性
const dialogVisible = defineModel<boolean>('visible', { default: false })

// 当前激活的标签页
const activeTab = ref('basic')

// 用户信息
const userInfo = reactive<UserProfile>({
  id: '',
  username: '',
  avatar: null,
  nickname: null,
  email: '',
})

// 表单模板引用
const basicFormRef = ref()
const passwordFormRef = ref()

// 基本信息表单
const basicForm = reactive({
  username: '',
  avatar: '',
})
const basicLoading = ref(false)

// 头像上传（未使用，保留注释说明）
// 头像预览URL直接在模板中使用 avatarPreviewUrl

// 根据昵称生成首字头像的颜色
const nameChar = computed(() => {
  const name = userInfo.nickname || userInfo.username || '?'
  return name.charAt(0)
})

const avatarColors = [
  '#409EFF', '#67C23A', '#E6A23C', '#F56C6C',
  '#909399', '#1890FF', '#52C41A', '#FAAD14',
]
const avatarColor = computed(() => {
  const name = userInfo.nickname || userInfo.username || '?'
  const index = name.charCodeAt(0) % avatarColors.length
  return avatarColors[index]
})

// 密码修改表单
const passwordForm = reactive({
  oldPassword: '',
  newPassword: '',
  confirmPassword: '',
})
const passwordLoading = ref(false)

// 密码表单验证规则
const passwordRules = {
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '新密码长度不能少于6位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule: any, value: string, callback: Function) => {
        if (value !== passwordForm.newPassword) {
          callback(new Error('两次输入的密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

// 基本信息表单验证规则
const basicRules = {
  username: [
    { min: 2, max: 20, message: '昵称长度在2-20个字符之间', trigger: 'blur' },
  ],
}

// 获取用户信息
const fetchUserInfo = async () => {
  try {
    const res = await userApi.getInfo()
    Object.assign(userInfo, res.data)
    basicForm.username = userInfo.nickname || userInfo.username
  } catch {
    // 静默处理
  }
}

// 打开对话框时获取最新信息
const handleOpen = () => {
  fetchUserInfo()
}

// 上传头像
const handleAvatarUpload = async (uploadFile: File) => {
  const isJpgPng = uploadFile.type === 'image/jpeg' || uploadFile.type === 'image/png' || uploadFile.type === 'image/jpg'
  if (!isJpgPng) {
    ElMessage.error('仅支持jpg、png格式的图片')
    return false
  }
  const isLt2M = uploadFile.size / 1024 / 1024 < 2
  if (!isLt2M) {
    ElMessage.error('头像图片大小不能超过2MB')
    return false
  }
  return true
}

// 头像上传成功回调
const handleAvatarSuccess = (response: { success: boolean; url: string }) => {
  if (response.success) {
    basicForm.avatar = response.url
    userInfo.avatar = response.url
    ElMessage.success('头像上传成功，请点击"保存"按钮生效')
  }
}

// 头像上传错误回调
const handleAvatarError = () => {
  ElMessage.error('头像上传失败')
}

// 保存基本信息
const handleSaveBasic = async (formEl: any) => {
  if (!formEl) return
  try {
    await formEl.validate()
  } catch {
    return
  }

  basicLoading.value = true
  try {
    await userApi.updateInfo({
      username: basicForm.username,
      avatar: basicForm.avatar || userInfo.avatar || undefined,
    })
    // 更新本地用户信息
    authStore.fetchUser()
    ElMessage.success('信息更新成功')
    emit('update')
  } catch {
    // 错误已在拦截器中处理
  } finally {
    basicLoading.value = false
  }
}

// 修改密码
const handleChangePassword = async (formEl: any) => {
  if (!formEl) return
  try {
    await formEl.validate()
  } catch {
    return
  }

  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    ElMessage.error('两次输入的密码不一致')
    return
  }

  passwordLoading.value = true
  try {
    const res = await userApi.modifyPassword(
      passwordForm.oldPassword,
      passwordForm.newPassword,
      passwordForm.confirmPassword,
    )
    ElMessage.success('密码修改成功，请重新登录')
    // 清除本地token并跳转到登录页
    authStore.logout()
    router.push('/login')
  } catch {
    // 错误已在拦截器中处理
  } finally {
    passwordLoading.value = false
  }
}

// 取消
const handleCancel = () => {
  dialogVisible.value = false
}

// 头像预览URL
const avatarPreviewUrl = computed(() => {
  return basicForm.avatar || userInfo.avatar || ''
})
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="个人信息设置"
    width="500px"
    :close-on-click-modal="true"
    :close-on-press-escape="true"
    @open="handleOpen"
    @close="handleCancel"
    destroy-on-close
  >
    <el-tabs v-model="activeTab">
      <!-- 基本信息标签页 -->
      <el-tab-pane label="基本信息" name="basic">
        <el-form
          ref="basicFormRef"
          :model="basicForm"
          :rules="basicRules"
          label-width="80px"
          style="padding: 20px 0"
        >
          <!-- 头像 -->
          <el-form-item label="头像">
            <div style="display: flex; align-items: center; gap: 16px;">
              <!-- 显示当前头像 -->
              <div
                v-if="avatarPreviewUrl"
                style="width: 64px; height: 64px; border-radius: 50%; overflow: hidden; border: 2px solid #e4e7ed; flex-shrink: 0;"
              >
                <img
                  :src="avatarPreviewUrl"
                  style="width: 100%; height: 100%; object-fit: cover;"
                  alt="头像"
                />
              </div>
              <div
                v-else
                style="width: 64px; height: 64px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 28px; color: #fff; background: v-bind(avatarColor); flex-shrink: 0;"
              >
                {{ nameChar }}
              </div>
              <el-upload
                :show-file-list="false"
                :http-request="(options) => {
                  const file = options.file as File
                  handleAvatarUpload(file).then(valid => {
                    if (valid) {
                      userApi.uploadAvatar(file).then(res => {
                        handleAvatarSuccess(res.data)
                      }).catch(() => {
                        handleAvatarError()
                      })
                    }
                  })
                }"
                accept=".jpg,.jpeg,.png"
              >
                <el-button size="small" type="primary">选择图片</el-button>
                <template #tip>
                  <div style="font-size: 12px; color: #909399; margin-top: 4px;">
                    支持jpg、png格式，大小不超过2MB
                  </div>
                </template>
              </el-upload>
            </div>
          </el-form-item>

          <!-- 昵称 -->
          <el-form-item label="昵称" prop="username">
            <el-input
              v-model="basicForm.username"
              placeholder="请输入昵称（2-20个字符）"
              maxlength="20"
              show-word-limit
            />
          </el-form-item>
        </el-form>

        <div style="text-align: right; padding-top: 8px; border-top: 1px solid #f0f0f0;">
          <el-button @click="handleCancel">取消</el-button>
          <el-button type="primary" :loading="basicLoading" @click="handleSaveBasic(basicFormRef)">
            保存
          </el-button>
        </div>
      </el-tab-pane>

      <!-- 密码修改标签页 -->
      <el-tab-pane label="密码修改" name="password">
        <el-form
          ref="passwordFormRef"
          :model="passwordForm"
          :rules="passwordRules"
          label-width="100px"
          style="padding: 20px 0"
        >
          <el-form-item label="原密码" prop="oldPassword">
            <el-input
              v-model="passwordForm.oldPassword"
              type="password"
              placeholder="请输入原密码"
              show-password
            />
          </el-form-item>
          <el-form-item label="新密码" prop="newPassword">
            <el-input
              v-model="passwordForm.newPassword"
              type="password"
              placeholder="请输入新密码（至少6位）"
              show-password
            />
          </el-form-item>
          <el-form-item label="确认新密码" prop="confirmPassword">
            <el-input
              v-model="passwordForm.confirmPassword"
              type="password"
              placeholder="请再次输入新密码"
              show-password
            />
          </el-form-item>
        </el-form>

        <div style="text-align: right; padding-top: 8px; border-top: 1px solid #f0f0f0;">
          <el-button @click="handleCancel">取消</el-button>
          <el-button type="primary" :loading="passwordLoading" @click="handleChangePassword(passwordFormRef)">
            修改密码
          </el-button>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog__body) {
  padding-top: 12px;
}
</style>
