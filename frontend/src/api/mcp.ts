import apiClient from './client'

// HTTP 模式 + Stdio 模式统一接口
export interface MCPServerItem {
  id: string
  name: string
  type: string            // http | stdio
  config: Record<string, any> | null
  status: string          // connected, disconnected, error, connecting
  user_id: string
  created_at: string | null
  updated_at: string | null
}

export interface MCPStdioServerItem {
  name: string
  command: string
  args: string[]
  status: string            // running, stopped, crashed, failed
  tool_count: number
  started_at: string | null
  restart_count: number
}

export interface MCPToolItem {
  name: string
  display_name: string
  description: string
}

export interface MCPToolCallRequest {
  tool_name: string
  arguments: Record<string, any>
}

// 创建/更新 Server 的请求
export interface MCPServerCreateRequest {
  name: string
  type: string        // "http" | "stdio"
  config: Record<string, any>
  status?: string
}

export interface MCPServerTestRequest {
  type: string
  config: Record<string, any>
}

export interface MCPServerTestResult {
  success: boolean
  message: string
  tool_count: number
  tools: Record<string, any>[]
}

export const mcpApi = {
  // ── DB-backed CRUD（统一管理 HTTP + Stdio） ──
  listServers() {
    return apiClient.get<MCPServerItem[]>('/mcp/servers')
  },

  createServer(data: MCPServerCreateRequest) {
    return apiClient.post<MCPServerItem>('/mcp/servers', data)
  },

  getServer(serverId: string) {
    return apiClient.get<MCPServerItem>(`/mcp/servers/${serverId}`)
  },

  updateServer(serverId: string, data: Partial<MCPServerCreateRequest>) {
    return apiClient.put<MCPServerItem>(`/mcp/servers/${serverId}`, data)
  },

  deleteServer(serverId: string) {
    return apiClient.delete(`/mcp/servers/${serverId}`)
  },

  // ── 连接测试 ──
  testConnection(data: MCPServerTestRequest) {
    return apiClient.post<MCPServerTestResult>('/mcp/servers/test', data)
  },

  // ── Stdio 进程管理 ──
  connectStdio(serverId: string) {
    return apiClient.post<MCPServerItem>(`/mcp/servers/stdio?server_id=${serverId}`)
  },

  disconnectStdio(serverName: string) {
    return apiClient.delete(`/mcp/servers/stdio/${serverName}`)
  },

  // ── 旧版兼容 ──
  connect(server_name: string, server_url: string) {
    return apiClient.post<MCPToolItem[]>('/mcp/servers', {
      name: server_name,
      type: 'http',
      config: { url: server_url },
    })
  },

  disconnect(server_name: string) {
    return apiClient.delete(`/mcp/servers/${server_name}`)
  },

  listStdioServers() {
    return apiClient.get<MCPStdioServerItem[]>('/mcp/servers/stdio')
  },

  getStdioServer(server_name: string) {
    return apiClient.get<MCPStdioServerItem>(`/mcp/servers/stdio/${server_name}`)
  },

  loadConfig() {
    return apiClient.post('/mcp/load-config')
  },

  // ── 缓存管理 ──
  clearCache(sessionId?: string) {
    const params = sessionId ? `?session_id=${sessionId}` : ''
    return apiClient.post(`/mcp/cache/clear${params}`)
  },
}