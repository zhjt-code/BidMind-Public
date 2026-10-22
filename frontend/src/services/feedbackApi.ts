import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

export interface FeedbackItem {
  id: number
  userId: number
  user: { id: number; username: string; email: string } | null
  feedbackType: string
  content: string
  pageUrl: string | null
  projectId: number | null
  project: { id: number; name: string } | null
  contextInfo: Record<string, unknown> | null
  status: string
  adminNote: string | null
  createdAt: string
  updatedAt: string
}

export interface FeedbackListResponse {
  items: FeedbackItem[]
  total: number
  page: number
  pageSize: number
}

interface FeedbackCreateRequest {
  feedback_type: string
  content: string
  page_url?: string
  project_id?: number
  context_info?: Record<string, unknown>
}

const transformFeedback = (raw: Record<string, unknown>): FeedbackItem => {
  const user = raw.user as Record<string, unknown> | null
  const project = raw.project as Record<string, unknown> | null
  return {
    id: raw.id as number,
    userId: raw.user_id as number,
    user: user ? { id: user.id as number, username: user.username as string, email: user.email as string } : null,
    feedbackType: raw.feedback_type as string,
    content: raw.content as string,
    pageUrl: raw.page_url as string | null,
    projectId: raw.project_id as number | null,
    project: project ? { id: project.id as number, name: project.name as string } : null,
    contextInfo: raw.context_info as Record<string, unknown> | null,
    status: raw.status as string,
    adminNote: raw.admin_note as string | null,
    createdAt: raw.created_at as string,
    updatedAt: raw.updated_at as string,
  }
}

export const feedbackService = {
  create: async (data: FeedbackCreateRequest): Promise<FeedbackItem> => {
    const response = await api.post<ApiResponse<Record<string, unknown>>>('/feedback', data)
    return transformFeedback(response.data)
  },

  getList: async (params: {
    page?: number
    page_size?: number
    status?: string
    type?: string
  } = {}): Promise<FeedbackListResponse> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>('/feedback', { params })
    const d = response.data
    const rawItems = (d.items as unknown[]) || []
    return {
      items: rawItems.map((item) => transformFeedback(item as Record<string, unknown>)),
      total: d.total as number,
      page: d.page as number,
      pageSize: d.page_size as number,
    }
  },

  getDetail: async (id: number): Promise<FeedbackItem> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(`/feedback/${id}`)
    return transformFeedback(response.data)
  },

  updateStatus: async (id: number, data: { status: string; admin_note?: string }): Promise<FeedbackItem> => {
    const response = await api.patch<ApiResponse<Record<string, unknown>>>(`/feedback/${id}/status`, data)
    return transformFeedback(response.data)
  },
}
