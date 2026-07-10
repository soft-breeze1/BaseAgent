// Tools API

import client from './client'

export interface ToolItem {
  name: string
  display_name: string
  description: string
  tool_type: string
  is_enabled: boolean
  config: Record<string, any>
}

export const toolApi = {
  list() {
    return client.get<ToolItem[]>('/tools')
  },

  get(name: string) {
    return client.get<ToolItem>(`/tools/${name}`)
  },

  toggle(name: string, is_enabled: boolean) {
    return client.put<ToolItem>(`/tools/${name}/toggle`, { is_enabled })
  },

  updateConfig(name: string, config: Record<string, any>) {
    return client.put<ToolItem>(`/tools/${name}/config`, { config })
  },

  testTavily(api_key: string) {
    return client.post<{ success: boolean; message: string }>('/tools/tavily-test', { api_key })
  },

  testUnsplash(api_key: string) {
    return client.post<{ success: boolean; message: string }>('/tools/unsplash-test', { api_key })
  },

  translate(text: string) {
    return client.post<{ translated: string }>('/tools/translate', { text })
  },
}
