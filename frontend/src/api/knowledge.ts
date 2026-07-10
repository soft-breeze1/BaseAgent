import apiClient from './client'

export interface KnowledgeBaseItem {
  id: string
  user_id: string
  name: string
  description: string | null
  embedding_model: string
  chunk_size: number
  chunk_overlap: number
  collection_name: string
  document_count: number
  status: string
  created_at: string
  updated_at: string
}

export interface KnowledgeDocumentItem {
  id: string
  kb_id: string
  filename: string
  file_type: string
  file_size: number
  chunk_count: number
  status: string
  error_message: string | null
  created_at: string
}

export interface CreateKBParams {
  name: string
  description?: string
  embedding_model?: string
}

export const knowledgeApi = {
  // Knowledge Bases
  listKBs() {
    return apiClient.get<KnowledgeBaseItem[]>('/knowledge/')
  },

  getKB(id: string) {
    return apiClient.get<KnowledgeBaseItem>(`/knowledge/${id}`)
  },

  createKB(params: CreateKBParams) {
    return apiClient.post<KnowledgeBaseItem>('/knowledge/', params)
  },

  updateKB(id: string, params: Partial<CreateKBParams>) {
    return apiClient.put<KnowledgeBaseItem>(`/knowledge/${id}`, params)
  },

  deleteKB(id: string) {
    return apiClient.delete(`/knowledge/${id}`)
  },

  // Documents
  listDocuments(kbId: string) {
    return apiClient.get<KnowledgeDocumentItem[]>(`/knowledge/${kbId}/documents`)
  },

uploadDocument(kbId: string, file: File) {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post<KnowledgeDocumentItem>(`/knowledge/${kbId}/upload`, formData, {
      timeout: 120000,
    })
  },

  deleteDocument(kbId: string, docId: string) {
    return apiClient.delete(`/knowledge/${kbId}/documents/${docId}`)
  },

  reprocessDocument(kbId: string, docId: string) {
    return apiClient.post(`/knowledge/${kbId}/documents/${docId}/reprocess`)
  },
}