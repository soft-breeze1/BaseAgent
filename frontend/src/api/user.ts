// User Profile API (用户个人信息接口)
import apiClient from './client'

export interface UserProfile {
  id: string
  username: string
  avatar: string | null
  nickname: string | null
  email: string
}

export interface UserInfoUpdate {
  username?: string
  avatar?: string
}

export const userApi = {
  // 获取当前用户信息
  getInfo() {
    return apiClient.get<UserProfile>('/user/info')
  },

  // 更新用户基本信息（昵称、头像）
  updateInfo(data: UserInfoUpdate) {
    return apiClient.put('/user/info', data)
  },

  // 上传头像
  uploadAvatar(file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post<{ success: boolean; url: string }>('/user/avatar', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  // 修改密码
  modifyPassword(oldPassword: string, newPassword: string, confirmPassword: string) {
    return apiClient.put('/user/password', {
      old_password: oldPassword,
      new_password: newPassword,
      confirm_password: confirmPassword,
    })
  },
}