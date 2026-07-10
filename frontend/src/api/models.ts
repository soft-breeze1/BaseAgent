import apiClient from './client'

export type ModelType = 'llm' | 'embedding'

export interface ModelConfigItem {
  id: string
  user_id: string
  provider: string
  model_name: string
  model_type: ModelType
  api_key: string
  api_base: string | null
  is_default: boolean
  is_active: boolean
  extra_config: string | null
  created_at: string
  updated_at: string
}

export interface CreateModelParams {
  provider: string
  model_name: string
  model_type?: ModelType
  api_key: string
  api_base?: string
  is_default?: boolean
  extra_config?: string
}

export interface OllamaModelItem {
  name: string
  size: string
  modified_at?: string
}

export interface TestConnectionResult {
  success: boolean
  message: string
}

export const modelApi = {
  list(modelType?: ModelType) {
    const params = modelType ? `?model_type=${modelType}` : ''
    return apiClient.get<ModelConfigItem[]>(`/models/${params}`)
  },

  listOllamaModels() {
    return apiClient.get<OllamaModelItem[]>('/models/ollama/models')
  },

  get(id: string) {
    return apiClient.get<ModelConfigItem>(`/models/${id}`)
  },

  create(params: CreateModelParams) {
    return apiClient.post<ModelConfigItem>('/models/', params)
  },

  update(id: string, params: Partial<CreateModelParams & { is_active: boolean }>) {
    return apiClient.put<ModelConfigItem>(`/models/${id}`, params)
  },

  delete(id: string) {
    return apiClient.delete(`/models/${id}`)
  },

  testConnection(id: string) {
    return apiClient.post<TestConnectionResult>(`/models/${id}/test`)
  },
}


