import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'
import type { BroadcastLevel } from './bannerApi'

export interface BroadcastResponse {
  id: number
  level: BroadcastLevel
  title: string
  content: string
  refUrl: string | null
  showBanner: boolean
  dismissible: boolean
  startsAt: string
  endsAt: string | null
  createdBy: number | null
  creator: { id: number; username: string } | null
  deletedAt: string | null
  createdAt: string
  updatedAt: string
}

const transformBroadcast = (raw: Record<string, unknown>): BroadcastResponse => ({
  id: raw.id as number,
  level: raw.level as BroadcastLevel,
  title: raw.title as string,
  content: raw.content as string,
  refUrl: (raw.ref_url as string) || null,
  showBanner: raw.show_banner as boolean,
  dismissible: raw.dismissible as boolean,
  startsAt: raw.starts_at as string,
  endsAt: (raw.ends_at as string) || null,
  createdBy: (raw.created_by as number) ?? null,
  creator: (raw.creator as { id: number; username: string }) || null,
  deletedAt: (raw.deleted_at as string) || null,
  createdAt: raw.created_at as string,
  updatedAt: raw.updated_at as string,
})

export interface BroadcastCreatePayload {
  level: BroadcastLevel
  title: string
  content: string
  ref_url?: string | null
  show_banner?: boolean
  dismissible?: boolean
  starts_at?: string | null
  ends_at?: string | null
}

export interface BroadcastUpdatePayload extends Partial<BroadcastCreatePayload> {}

export const broadcastService = {
  list: async (page = 1, pageSize = 20): Promise<{ items: BroadcastResponse[]; total: number; page: number; pageSize: number }> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>('/admin/broadcasts', {
      params: { page, page_size: pageSize },
    })
    const d = response.data
    const rawItems = (d.items as unknown[]) || []
    return {
      items: rawItems.map((item) => transformBroadcast(item as Record<string, unknown>)),
      total: d.total as number,
      page: d.page as number,
      pageSize: d.page_size as number,
    }
  },

  get: async (id: number): Promise<BroadcastResponse> => {
    const response = await api.get<ApiResponse<Record<string, unknown>>>(`/admin/broadcasts/${id}`)
    return transformBroadcast(response.data)
  },

  create: async (payload: BroadcastCreatePayload): Promise<BroadcastResponse> => {
    const response = await api.post<ApiResponse<Record<string, unknown>>>('/admin/broadcasts', payload)
    return transformBroadcast(response.data)
  },

  update: async (id: number, payload: BroadcastUpdatePayload): Promise<BroadcastResponse> => {
    const response = await api.patch<ApiResponse<Record<string, unknown>>>(`/admin/broadcasts/${id}`, payload)
    return transformBroadcast(response.data)
  },

  remove: async (id: number): Promise<BroadcastResponse> => {
    const response = await api.delete<ApiResponse<Record<string, unknown>>>(`/admin/broadcasts/${id}`)
    return transformBroadcast(response.data)
  },
}
