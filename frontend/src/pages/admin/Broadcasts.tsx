/**
 * 广播管理页面（管理员）
 *
 * 提供广播的创建、编辑、撤回（软删除）功能。
 * 沿用反馈管理页风格：表格 + 弹窗表单。
 */

import React, { useEffect, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { Megaphone, Plus, Pencil, Trash2 } from 'lucide-react'
import {
  broadcastService,
  type BroadcastResponse,
  type BroadcastCreatePayload,
} from '@/services/broadcastApi'
import type { BroadcastLevel } from '@/services/bannerApi'
import { formatDate } from '@/lib/formatters'
import { Modal } from '@/components'

interface PaginationInfo {
  page: number
  pageSize: number
  total: number
}

const LEVEL_OPTIONS: { value: BroadcastLevel; label: string; className: string }[] = [
  { value: 'info', label: '通知', className: 'bg-blue-100 text-blue-800' },
  { value: 'success', label: '成功', className: 'bg-emerald-100 text-emerald-800' },
  { value: 'warning', label: '警告', className: 'bg-amber-100 text-amber-800' },
  { value: 'critical', label: '紧急', className: 'bg-red-100 text-red-800' },
]

const LEVEL_MAP: Record<BroadcastLevel, { label: string; className: string }> = {
  info: { label: '通知', className: 'bg-blue-100 text-blue-800' },
  success: { label: '成功', className: 'bg-emerald-100 text-emerald-800' },
  warning: { label: '警告', className: 'bg-amber-100 text-amber-800' },
  critical: { label: '紧急', className: 'bg-red-100 text-red-800' },
}

interface FormState {
  level: BroadcastLevel
  title: string
  content: string
  refUrl: string
  showBanner: boolean
  dismissible: boolean
  startsAt: string
  endsAt: string
}

const EMPTY_FORM: FormState = {
  level: 'info',
  title: '',
  content: '',
  refUrl: '',
  showBanner: true,
  dismissible: true,
  startsAt: '',
  endsAt: '',
}

/** 把 datetime-local 输入值转为后端可接受的 ISO 字符串。空字符串保持 null。 */
const toIsoOrNull = (val: string): string | null => {
  if (!val) return null
  const d = new Date(val)
  if (Number.isNaN(d.getTime())) return null
  return d.toISOString()
}

/** 把后端 ISO 字符串转为 datetime-local 输入框需要的本地格式（去掉秒外的时区）。 */
const toLocalInputValue = (iso: string | null): string => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  // datetime-local 只接受 yyyy-MM-ddTHH:mm
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 计算当前广播的展示状态。 */
const computeStatus = (b: BroadcastResponse): { label: string; className: string } => {
  if (b.deletedAt) return { label: '已撤回', className: 'bg-gray-100 text-gray-600' }
  const now = Date.now()
  const start = new Date(b.startsAt).getTime()
  const end = b.endsAt ? new Date(b.endsAt).getTime() : null
  if (now < start) return { label: '待生效', className: 'bg-slate-100 text-slate-600' }
  if (end != null && now > end) return { label: '已过期', className: 'bg-gray-100 text-gray-500' }
  return { label: '生效中', className: 'bg-emerald-100 text-emerald-800' }
}

const BroadcastsPage: React.FC = () => {
  const [items, setItems] = useState<BroadcastResponse[]>([])
  const [pagination, setPagination] = useState<PaginationInfo>({ page: 1, pageSize: 20, total: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 创建/编辑弹窗
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)

  const loadItems = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await broadcastService.list(pagination.page, pagination.pageSize)
      setItems(result.items)
      setPagination((prev) => ({ ...prev, total: result.total }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载广播列表失败')
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.pageSize])

  useEffect(() => { loadItems() }, [loadItems])

  const openCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormOpen(true)
  }

  const openEdit = (b: BroadcastResponse) => {
    setEditingId(b.id)
    setForm({
      level: b.level,
      title: b.title,
      content: b.content,
      refUrl: b.refUrl || '',
      showBanner: b.showBanner,
      dismissible: b.dismissible,
      startsAt: toLocalInputValue(b.startsAt),
      endsAt: toLocalInputValue(b.endsAt),
    })
    setFormOpen(true)
  }

  const handleSave = async () => {
    if (!form.title.trim() || !form.content.trim()) {
      toast.error('标题与内容不能为空')
      return
    }
    setSaving(true)
    try {
      const payload: BroadcastCreatePayload = {
        level: form.level,
        title: form.title.trim(),
        content: form.content.trim(),
        ref_url: form.refUrl.trim() || null,
        show_banner: form.showBanner,
        dismissible: form.dismissible,
        starts_at: toIsoOrNull(form.startsAt),
        ends_at: toIsoOrNull(form.endsAt),
      }
      if (editingId == null) {
        const created = await broadcastService.create(payload)
        setItems((prev) => [created, ...prev])
        toast.success('广播已创建')
      } else {
        const updated = await broadcastService.update(editingId, payload)
        setItems((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))
        toast.success('广播已更新')
      }
      setFormOpen(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (b: BroadcastResponse) => {
    if (!window.confirm(`确认撤回广播"${b.title}"？\n撤回后该广播对所有用户立即不可见。`)) return
    try {
      const updated = await broadcastService.remove(b.id)
      setItems((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
      toast.success('已撤回')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '撤回失败')
    }
  }

  const totalPages = Math.ceil(pagination.total / pagination.pageSize)

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Megaphone className="h-6 w-6 text-amber-500" />
            广播管理
          </h1>
          {pagination.total > 0 && (
            <span className="text-sm text-gray-500">共 {pagination.total} 条</span>
          )}
        </div>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 flex items-center gap-2 transition-colors"
        >
          <Plus className="h-4 w-4" />
          创建广播
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">加载中...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-gray-500">暂无广播。点击右上角"创建广播"开始发布。</div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  级别
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  标题
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  生效时间
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  状态
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  发布者
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {items.map((b) => {
                const levelCfg = LEVEL_MAP[b.level]
                const statusCfg = computeStatus(b)
                return (
                  <tr key={b.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${levelCfg.className}`}>
                        {levelCfg.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 max-w-md">
                      <div className="text-sm font-medium text-gray-900 truncate">{b.title}</div>
                      <div className="text-xs text-gray-500 truncate mt-0.5">
                        {b.content.length > 60 ? `${b.content.slice(0, 60)}...` : b.content}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      <div>{formatDate(b.startsAt)}</div>
                      {b.endsAt && (
                        <div className="text-xs text-gray-400">至 {formatDate(b.endsAt)}</div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusCfg.className}`}>
                        {statusCfg.label}
                      </span>
                      {!b.showBanner && (
                        <span className="ml-2 text-xs text-gray-400">仅通知中心</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {b.creator?.username || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <div className="flex justify-end gap-3">
                        <button
                          onClick={() => openEdit(b)}
                          className="text-primary hover:text-blue-700 flex items-center gap-1"
                          title="编辑"
                        >
                          <Pencil className="h-4 w-4" />
                          编辑
                        </button>
                        {!b.deletedAt && (
                          <button
                            onClick={() => handleDelete(b)}
                            className="text-red-600 hover:text-red-700 flex items-center gap-1"
                            title="撤回"
                          >
                            <Trash2 className="h-4 w-4" />
                            撤回
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {!loading && items.length > 0 && (
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

      {/* 创建/编辑弹窗 */}
      <Modal
        visible={formOpen}
        title={editingId == null ? '创建广播' : '编辑广播'}
        onClose={() => !saving && setFormOpen(false)}
        maxWidth="max-w-2xl"
        maskClosable={!saving}
        footer={
          <>
            <button
              onClick={() => setFormOpen(false)}
              disabled={saving}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">级别</label>
            <div className="flex flex-wrap gap-2">
              {LEVEL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setForm((f) => ({ ...f, level: opt.value }))}
                  className={`px-3 py-1.5 text-sm rounded-full border transition-colors ${
                    form.level === opt.value
                      ? 'bg-primary text-white border-primary'
                      : 'bg-white text-gray-600 border-gray-200 hover:border-primary hover:text-primary'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">标题</label>
            <input
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              maxLength={200}
              placeholder="例：系统将于 4/30 22:00 - 23:00 维护"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">正文</label>
            <textarea
              value={form.content}
              onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
              rows={4}
              maxLength={2000}
              placeholder="详细说明，支持纯文本"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg resize-none focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
            <p className="text-xs text-gray-400 mt-1">{form.content.length} / 2000</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              点击横幅跳转的内部路径（可选）
            </label>
            <input
              value={form.refUrl}
              onChange={(e) => setForm((f) => ({ ...f, refUrl: e.target.value }))}
              maxLength={500}
              placeholder="例：/account 或 https://example.com"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">生效时间</label>
              <input
                type="datetime-local"
                value={form.startsAt}
                onChange={(e) => setForm((f) => ({ ...f, startsAt: e.target.value }))}
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
              />
              <p className="text-xs text-gray-400 mt-1">留空则立即生效</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">失效时间</label>
              <input
                type="datetime-local"
                value={form.endsAt}
                onChange={(e) => setForm((f) => ({ ...f, endsAt: e.target.value }))}
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
              />
              <p className="text-xs text-gray-400 mt-1">留空则长期有效</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 pt-2">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.showBanner}
                onChange={(e) => setForm((f) => ({ ...f, showBanner: e.target.checked }))}
                className="rounded"
              />
              在顶部 Banner 展示（取消后仅进通知中心）
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={form.dismissible}
                onChange={(e) => setForm((f) => ({ ...f, dismissible: e.target.checked }))}
                className="rounded"
              />
              允许用户关闭 Banner（紧急公告建议取消勾选）
            </label>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default BroadcastsPage
