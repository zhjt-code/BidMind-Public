/**
 * 站内信状态管理（含广播 Banner）
 *
 * 管理未读数、最近消息列表、Banner 横幅，提供轮询刷新和已读/关闭操作。
 */

import { create } from 'zustand'
import { messageService, type MessageItem } from '@/services/messageApi'
import { bannerService, type BannerItem } from '@/services/bannerApi'

interface MessageState {
  unreadCount: number
  recent: MessageItem[]
  banners: BannerItem[]

  refreshUnread: () => Promise<void>
  fetchRecent: () => Promise<void>
  fetchBanners: () => Promise<void>
  markRead: (msg: MessageItem) => Promise<void>
  markAllRead: () => Promise<void>
  dismissBanner: (broadcastId: number) => Promise<void>
  reset: () => void
}

/** 判断条目是否为广播（合并列表中通过 type/refType 区分）。 */
const isBroadcastItem = (msg: MessageItem): boolean =>
  msg.type === 'broadcast' || msg.refType === 'broadcast'

export const useMessageStore = create<MessageState>()((set) => ({
  unreadCount: 0,
  recent: [],
  banners: [],

  refreshUnread: async () => {
    try {
      const count = await messageService.getUnreadCount()
      set({ unreadCount: count })
    } catch {
      // 静默失败，不打扰用户
    }
  },

  fetchRecent: async () => {
    try {
      const result = await messageService.getList({ page: 1, page_size: 10 })
      set({ recent: result.items })
      const freshCount = await messageService.getUnreadCount()
      set({ unreadCount: freshCount })
    } catch {
      // 静默失败
    }
  },

  fetchBanners: async () => {
    try {
      const items = await bannerService.list()
      set({ banners: items })
    } catch {
      // 静默失败
    }
  },

  markRead: async (msg: MessageItem) => {
    try {
      if (isBroadcastItem(msg) && msg.refId != null) {
        // 广播：调专用接口；后端返回最新未读总数
        const newCount = await messageService.markBroadcastRead(msg.refId)
        set((state) => ({
          recent: state.recent.map((m) =>
            m.id === msg.id ? { ...m, isRead: true } : m,
          ),
          unreadCount: newCount,
        }))
      } else {
        const updated = await messageService.markRead(msg.id)
        set((state) => ({
          recent: state.recent.map((m) => (m.id === updated.id ? updated : m)),
          unreadCount: Math.max(0, state.unreadCount - 1),
        }))
      }
    } catch {
      // 静默失败
    }
  },

  markAllRead: async () => {
    try {
      await messageService.markAllRead()
      set((state) => ({
        recent: state.recent.map((m) => ({ ...m, isRead: true })),
        unreadCount: 0,
      }))
    } catch {
      // 静默失败
    }
  },

  dismissBanner: async (broadcastId: number) => {
    try {
      const newCount = await messageService.dismissBroadcast(broadcastId)
      set((state) => ({
        banners: state.banners.filter((b) => b.id !== broadcastId),
        // dismiss 同时标记已读 -- 用后端返回的最新未读数刷新
        unreadCount: newCount,
        // 通知中心列表里同步标已读（按 refId 匹配广播条目）
        recent: state.recent.map((m) =>
          isBroadcastItem(m) && m.refId === broadcastId ? { ...m, isRead: true } : m,
        ),
      }))
    } catch {
      // 静默失败：保留 banner 仍可见
    }
  },

  reset: () => set({ unreadCount: 0, recent: [], banners: [] }),
}))

export type { MessageItem, BannerItem }
export { isBroadcastItem }
