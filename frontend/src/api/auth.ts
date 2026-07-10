import apiClient from './client'

export interface LoginParams {
  username: string
  password: string
}

export interface RegisterParams {
  username: string
  email: string
  password: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface UserInfo {
  id: string
  username: string
  email: string
  avatar: string | null
  nickname: string | null
  is_active: boolean
  is_superuser: boolean
}

export const authApi = {
  login(params: LoginParams) {
    return apiClient.post<TokenResponse>('/auth/login', params)
  },

  register(params: RegisterParams) {
    return apiClient.post<TokenResponse>('/auth/register', params)
  },

  refreshToken(refreshToken: string) {
    return apiClient.post<TokenResponse>('/auth/refresh', { refresh_token: refreshToken })
  },

  getMe() {
    return apiClient.get<UserInfo>('/auth/me')
  },

  changePassword(oldPassword: string, newPassword: string) {
    return apiClient.post('/auth/change-password', {
      old_password: oldPassword,
      new_password: newPassword,
    })
  },
}