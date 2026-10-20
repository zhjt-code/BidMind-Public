import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

export type BroadcastLevel = 'info' | 'success' | 'warning' | 'critical'

export interface BannerItem {
  id: number
  level: BroadcastLevel
  title: string
  content: string
  refUrl: string | null
  dismissible: boolean
  isRead: boolean
  createdAt: string
}

const transformBanner = (raw: Record<string, unknown>): BannerItem => ({
  id: raw.id as number,
  level: raw.level as BroadcastLevel,
  title: raw.title as string,
  content: raw.content as string,
  refUrl: (raw.ref_url as string) || null,
  dismissible: raw.dismissible as boolean,
  isRead: raw.is_read as boolean,
  createdAt: raw.created_at as string,
})

export const bannerService = {
  /** 获取当前用户应显示的顶部 Banner（最多 3 条，按级别优先） */
  list: async (): Promise<BannerItem[]> => {
    const response = await api.get<ApiResponse<{ items: Record<string, unknown>[] }>>('/messages/banners')
    const items = response.data.items || []
    return items.map((item) => transformBanner(item))
  },
}
