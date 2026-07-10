import apiClient from './client'

export interface SystemPromptItem {
  id: number
  content: string
  updated_at: string | null
}

export interface SystemPromptUpdateResponse {
  success: boolean
  message: string
}

export interface SystemPromptResetResponse {
  success: boolean
  message: string
  content: string
}

export const systemPromptApi = {
  /** 获取当前启用的系统提示词 */
  get() {
    return apiClient.get<SystemPromptItem>('/system-prompt/')
  },

  /** 更新系统提示词 */
  update(content: string) {
    return apiClient.put<SystemPromptUpdateResponse>('/system-prompt/', { content })
  },

  /** 重置为默认系统提示词 */
  reset() {
    return apiClient.post<SystemPromptResetResponse>('/system-prompt/reset')
  },
}