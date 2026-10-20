import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

export interface MessageItem {
  id: number
  userId: number
  type: string
  title: string
  content: string
  isRead: boolean
  readAt: string | null
  refType: string | null
  refId: number | null
  createdAt: string
  updatedAt: string
}

export interface MessageListResponse {
  items: MessageItem[]
  total: number
  page: number
  pageSize: number
}

const transformMessage = (raw: Record<string, unknown>): MessageItem => ({
  id: raw.id as number,
  userId: raw.user_id as number,
  type: raw.type as string,
  title: raw.title as string,
  content: raw.content as string,
  isRead: raw.is_read as boolean,
  readAt: (raw.read_at as string) || null,
  refType: (raw.ref_type as string) || null,
  refId: (raw.ref_id as number) || null,
  createdAt: raw.created_at as string,
  updatedAt: raw.updated_at as string,
})

export const messageService = {
  getList: async (params: { page?: number; page_size?: number; unread_only?: boolean; source?: 'message' | 'broadcast' } = {}): Promise<MessageListResponse> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>('/messages', { params })
    const d = response.data
    const rawItems = (d.items as unknown[]) || []
    return {
      items: rawItems.map((item) => transformMessage(item as Record<string, unknown>)),
      total: d.total as number,
      page: d.page as number,
      pageSize: d.page_size as number,
    }
  },

  getUnreadCount: async (): Promise<number> => {
    const response = await api.get<ApiResponse<{ count: number }>>('/messages/unread_count')
    return response.data.count
  },

  markRead: async (id: number): Promise<MessageItem> => {
    const response = await api.patch<ApiResponse<Record<string, unknown>>>(`/messages/${id}/read`)
    return transformMessage(response.data)
  },

  markAllRead: async (): Promise<void> => {
    await api.post('/messages/read_all')
  },

  /** 标记单条广播为已读（不会让 Banner 消失） */
  markBroadcastRead: async (broadcastId: number): Promise<number> => {
    const response = await api.patch<ApiResponse<{ count: number }>>(`/messages/broadcasts/${broadcastId}/read`)
    return response.data.count
  },

  /** 关闭广播 Banner（仍在通知中心可见，同时标记已读） */
  dismissBroadcast: async (broadcastId: number): Promise<number> => {
    const response = await api.post<ApiResponse<{ count: number }>>(`/messages/broadcasts/${broadcastId}/dismiss`)
    return response.data.count
  },
}
