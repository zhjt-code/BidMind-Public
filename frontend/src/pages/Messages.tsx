/**
 * 站内信页面
 *
 * 展示当前用户的所有站内信（含广播），支持类型筛选、分页和标记已读。
 * 广播条目通过 type 字段区分，渲染独立图标；标已读会调用专用接口。
 */

import React, { useEffect, useState, useCallback } from 'react'
import { Megaphone, MessageSquare, Settings2, Inbox } from 'lucide-react'
import { messageService, type MessageItem } from '@/services/messageApi'
import { formatDate } from '@/lib/formatters'
import { useMessageStore, isBroadcastItem } from '@/stores/messageStore'

interface PaginationInfo {
  page: number
  pageSize: number
  total: number
}

type FilterMode = 'all' | 'unread' | 'broadcast' | 'feedback'

const FILTER_OPTIONS: { value: FilterMode; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'unread', label: '未读' },
  { value: 'broadcast', label: '广播' },
  { value: 'feedback', label: '反馈' },
]

interface TypeIconConfig {
  Icon: React.ElementType
  colorClass: string
}

const TYPE_ICON_MAP: Record<string, TypeIconConfig> = {
  broadcast: { Icon: Megaphone, colorClass: 'text-amber-500' },
  feedback_reply: { Icon: MessageSquare, colorClass: 'text-blue-500' },
  system: { Icon: Settings2, colorClass: 'text-slate-500' },
}

const getTypeIcon = (msg: MessageItem): TypeIconConfig =>
  TYPE_ICON_MAP[msg.type]
  ?? TYPE_ICON_MAP[msg.refType ?? '']
  ?? { Icon: Inbox, colorClass: 'text-gray-400' }

const MessagesPage: React.FC = () => {
  const [messages, setMessages] = useState<MessageItem[]>([])
  const [pagination, setPagination] = useState<PaginationInfo>({ page: 1, pageSize: 20, total: 0 })
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<FilterMode>('all')
  const { refreshUnread, markRead: storeMarkRead, markAllRead: storeMarkAllRead } = useMessageStore()

  const loadMessages = useCallback(async () => {
    setLoading(true)
    try {
      const params: Parameters<typeof messageService.getList>[0] = {
        page: pagination.page,
        page_size: pagination.pageSize,
      }
      if (filter === 'unread') {
        params.unread_only = true
      } else if (filter === 'broadcast') {
        params.source = 'broadcast'
      } else if (filter === 'feedback') {
        params.source = 'message'
      }
      const result = await messageService.getList(params)
      setMessages(result.items)
      setPagination((prev) => ({ ...prev, total: result.total }))
    } catch {
      // 静默
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.pageSize, filter])

  useEffect(() => {
    loadMessages()
  }, [loadMessages])

  const handleMarkRead = async (msg: MessageItem) => {
    if (isBroadcastItem(msg) && msg.refId != null) {
      // 广播：调用 store 已封装的逻辑（同步 unreadCount）
      await storeMarkRead(msg)
      setMessages((prev) =>
        prev.map((m) => (m.id === msg.id ? { ...m, isRead: true } : m)),
      )
    } else {
      try {
        const updated = await messageService.markRead(msg.id)
        setMessages((prev) => prev.map((m) => (m.id === updated.id ? updated : m)))
        refreshUnread()
      } catch {
        // 静默
      }
    }
  }

  const handleMarkAllRead = async () => {
    await storeMarkAllRead()
    setMessages((prev) => prev.map((m) => ({ ...m, isRead: true })))
  }

  const totalPages = Math.ceil(pagination.total / pagination.pageSize)
  const hasUnread = messages.some((m) => !m.isRead)

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-gray-900">消息中心</h1>
          {pagination.total > 0 && (
            <span className="text-sm text-gray-500">共 {pagination.total} 条</span>
          )}
        </div>
        {hasUnread && (
          <button
            onClick={handleMarkAllRead}
            className="px-3 py-1.5 text-sm text-primary border border-primary rounded-md hover:bg-primary/5 transition-colors"
          >
            全部标记已读
          </button>
        )}
      </div>

      <div className="bg-white rounded-lg shadow-sm p-4 mb-6">
        <div className="flex flex-wrap gap-2">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => { setFilter(opt.value); setPagination((p) => ({ ...p, page: 1 })) }}
              className={`px-3 py-1.5 text-sm rounded-full border transition-colors ${
                filter === opt.value
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-gray-600 border-gray-200 hover:border-primary hover:text-primary'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">
            <svg className="animate-spin h-8 w-8 mx-auto mb-2 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            加载中...
          </div>
        ) : messages.length === 0 ? (
          <div className="p-8 text-center text-gray-500">暂无消息</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {messages.map((msg) => {
              const { Icon, colorClass } = getTypeIcon(msg)
              return (
                <div
                  key={msg.id}
                  className={`px-6 py-4 flex items-start gap-4 transition-colors ${
                    msg.isRead ? 'bg-white' : 'bg-blue-50/40'
                  }`}
                >
                  <div className="pt-1 shrink-0">
                    <Icon className={`h-4 w-4 ${colorClass}`} />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      {!msg.isRead && (
                        <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                      )}
                      <span className={`text-sm ${msg.isRead ? 'text-gray-600' : 'text-gray-900 font-medium'}`}>
                        {msg.title}
                      </span>
                      <span className="text-xs text-gray-400">{formatDate(msg.createdAt)}</span>
                    </div>
                    <p className="text-sm text-gray-500 whitespace-pre-wrap leading-relaxed">
                      {msg.content}
                    </p>
                  </div>

                  {!msg.isRead && (
                    <button
                      onClick={() => handleMarkRead(msg)}
                      className="shrink-0 text-xs text-primary hover:underline mt-1"
                    >
                      标记已读
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {!loading && messages.length > 0 && (
          <div className="bg-white px-4 py-3 flex items-center justify-between border-t border-gray-200 sm:px-6">
            <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
              <p className="text-sm text-gray-700">
                共 <span className="font-medium">{pagination.total}</span> 条记录，
                第 <span className="font-medium">{pagination.page}</span> /{' '}
                <span className="font-medium">{totalPages}</span> 页
              </p>
              <div className="flex space-x-2">
                <button
                  onClick={() => setPagination((prev) => ({ ...prev, page: prev.page - 1 }))}
                  disabled={pagination.page <= 1}
                  className="relative inline-flex items-center px-3 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  上一页
                </button>
                <button
                  onClick={() => setPagination((prev) => ({ ...prev, page: prev.page + 1 }))}
                  disabled={pagination.page >= totalPages}
                  className="relative inline-flex items-center px-3 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default MessagesPage
