/**
 * 反馈管理页面
 *
 * 管理员专属页面，提供反馈列表、详情查看、状态管理和批注功能。
 */

import React, { useEffect, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { feedbackService, type FeedbackItem } from '@/services/feedbackApi'
import { formatDate } from '@/lib/formatters'
import { Modal } from '@/components'

interface PaginationInfo {
  page: number
  pageSize: number
  total: number
}

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'resolved', label: '已解决' },
  { value: 'closed', label: '已关闭' },
]

const TYPE_OPTIONS = [
  { value: '', label: '全部类型' },
  { value: 'bug', label: 'Bug报告' },
  { value: 'feature', label: '功能建议' },
  { value: 'review_question', label: '审查疑问' },
  { value: 'other', label: '其他' },
]

const STATUS_LABELS: Record<string, { label: string; className: string }> = {
  pending:    { label: '待处理', className: 'bg-yellow-100 text-yellow-800' },
  processing: { label: '处理中', className: 'bg-blue-100 text-blue-800' },
  resolved:   { label: '已解决', className: 'bg-green-100 text-green-800' },
  closed:     { label: '已关闭', className: 'bg-gray-100 text-gray-600' },
}

const TYPE_LABELS: Record<string, string> = {
  bug: 'Bug报告',
  feature: '功能建议',
  review_question: '审查疑问',
  other: '其他',
}

const FeedbackManagementPage: React.FC = () => {
  const [feedbacks, setFeedbacks] = useState<FeedbackItem[]>([])
  const [pagination, setPagination] = useState<PaginationInfo>({
    page: 1,
    pageSize: 10,
    total: 0,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  // 详情弹窗
  const [selectedFeedback, setSelectedFeedback] = useState<FeedbackItem | null>(null)
  const [editStatus, setEditStatus] = useState('')
  const [editNote, setEditNote] = useState('')
  const [saving, setSaving] = useState(false)

  const loadFeedbacks = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const result = await feedbackService.getList({
        page: pagination.page,
        page_size: pagination.pageSize,
        status: statusFilter || undefined,
        type: typeFilter || undefined,
      })

      setFeedbacks(result.items)
      setPagination((prev) => ({ ...prev, total: result.total }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载反馈列表失败')
    } finally {
      setLoading(false)
    }
  }, [pagination.page, pagination.pageSize, statusFilter, typeFilter])

  useEffect(() => {
    loadFeedbacks()
  }, [loadFeedbacks])

  const openDetail = (fb: FeedbackItem) => {
    setSelectedFeedback(fb)
    setEditStatus(fb.status)
    setEditNote(fb.adminNote || '')
  }

  const handleSave = async () => {
    if (!selectedFeedback) return
    setSaving(true)

    try {
      const updated = await feedbackService.updateStatus(selectedFeedback.id, {
        status: editStatus,
        admin_note: editNote || undefined,
      })

      setFeedbacks((prev) =>
        prev.map((fb) => (fb.id === updated.id ? updated : fb))
      )
      setSelectedFeedback(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '更新失败')
    } finally {
      setSaving(false)
    }
  }


  const pendingCount = feedbacks.filter((fb) => fb.status === 'pending').length
  const totalPages = Math.ceil(pagination.total / pagination.pageSize)

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-gray-900">反馈管理</h1>
          {pagination.total > 0 && (
            <span className="text-sm text-gray-500">
              共 {pagination.total} 条
              {pendingCount > 0 && (
                <span className="ml-2 px-2 py-0.5 bg-yellow-100 text-yellow-800 text-xs rounded-full font-medium">
                  {pendingCount} 条待处理
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* 筛选栏 */}
      <div className="bg-white rounded-lg shadow-sm p-4 mb-6">
        <div className="flex flex-wrap gap-4">
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setPagination((prev) => ({ ...prev, page: 1 }))
            }}
            className="border rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value)
              setPagination((prev) => ({ ...prev, page: 1 }))
            }}
            className="border rounded-md px-3 py-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 反馈列表 */}
      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-500">
            <svg
              className="animate-spin h-8 w-8 mx-auto mb-2 text-primary"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            加载中...
          </div>
        ) : feedbacks.length === 0 ? (
          <div className="p-8 text-center text-gray-500">暂无反馈数据</div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  提交时间
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  用户
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  类型
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  内容摘要
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  状态
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {feedbacks.map((fb) => {
                const statusCfg = STATUS_LABELS[fb.status] || STATUS_LABELS.pending
                return (
                  <tr key={fb.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(fb.createdAt)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <div className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center text-xs font-medium">
                          {fb.user?.username?.charAt(0).toUpperCase() || '?'}
                        </div>
                        <div className="ml-3">
                          <div className="text-sm font-medium text-gray-900">
                            {fb.user?.username || '未知用户'}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                      {TYPE_LABELS[fb.feedbackType] || fb.feedbackType}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-600 max-w-xs truncate">
                      {fb.content.length > 50
                        ? fb.content.slice(0, 50) + '...'
                        : fb.content}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusCfg.className}`}
                      >
                        {statusCfg.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <button
                        onClick={() => openDetail(fb)}
                        className="text-primary hover:text-blue-700"
                      >
                        查看详情
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {/* 分页 */}
        {!loading && feedbacks.length > 0 && (
          <div className="bg-white px-4 py-3 flex items-center justify-between border-t border-gray-200 sm:px-6">
            <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
              <p className="text-sm text-gray-700">
                共 <span className="font-medium">{pagination.total}</span> 条记录，
                第 <span className="font-medium">{pagination.page}</span> /{' '}
                <span className="font-medium">{totalPages}</span> 页
              </p>
              <div className="flex space-x-2">
                <button
                  onClick={() =>
                    setPagination((prev) => ({ ...prev, page: prev.page - 1 }))
                  }
                  disabled={pagination.page <= 1}
                  className="relative inline-flex items-center px-3 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  上一页
                </button>
                <button
                  onClick={() =>
                    setPagination((prev) => ({ ...prev, page: prev.page + 1 }))
                  }
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

      {/* 详情弹窗 */}
      <Modal
        visible={!!selectedFeedback}
        title="反馈详情"
        onClose={() => setSelectedFeedback(null)}
        maxWidth="max-w-2xl"
        maskClosable={!saving}
        footer={
          <>
            <button
              onClick={() => setSelectedFeedback(null)}
              disabled={saving}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 border border-gray-300 rounded-lg disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 flex items-center gap-2"
            >
              {saving && (
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
              {saving ? '保存中...' : '保存'}
            </button>
          </>
        }
      >
        {selectedFeedback && (
          <div className="space-y-4">
            {/* 基本信息 */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">提交用户</label>
                <p className="text-sm text-gray-900">
                  {selectedFeedback.user?.username || '未知'}
                  <span className="text-gray-400 ml-1">
                    ({selectedFeedback.user?.email || '-'})
                  </span>
                </p>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">提交时间</label>
                <p className="text-sm text-gray-900">{formatDate(selectedFeedback.createdAt)}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">反馈类型</label>
                <p className="text-sm text-gray-900">
                  {TYPE_LABELS[selectedFeedback.feedbackType] || selectedFeedback.feedbackType}
                </p>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">关联项目</label>
                <p className="text-sm text-gray-900">
                  {selectedFeedback.project
                    ? `${selectedFeedback.project.name} (ID: ${selectedFeedback.project.id})`
                    : '无'}
                </p>
              </div>
            </div>

            {/* 反馈内容 */}
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">反馈内容</label>
              <div className="p-3 bg-gray-50 rounded-lg text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                {selectedFeedback.content}
              </div>
            </div>

            {/* 来源页面 */}
            {selectedFeedback.pageUrl && (
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">来源页面</label>
                <p className="text-sm text-gray-600 break-all">{selectedFeedback.pageUrl}</p>
              </div>
            )}

            {/* 环境信息 */}
            {selectedFeedback.contextInfo && (
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">环境信息</label>
                <div className="p-2 bg-gray-50 rounded text-xs text-gray-500 font-mono break-all">
                  {JSON.stringify(selectedFeedback.contextInfo, null, 2)}
                </div>
              </div>
            )}

            <hr className="border-gray-200" />

            {/* 管理员操作区 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">处理状态</label>
              <div className="flex flex-wrap gap-2">
                {STATUS_OPTIONS.filter((o) => o.value !== '').map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setEditStatus(opt.value)}
                    className={`px-3 py-1.5 text-sm rounded-full border transition-colors ${
                      editStatus === opt.value
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
              <label className="block text-sm font-medium text-gray-700 mb-2">管理员批注</label>
              <textarea
                value={editNote}
                onChange={(e) => setEditNote(e.target.value)}
                placeholder="添加处理备注（可选）..."
                rows={3}
                maxLength={1000}
                className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg resize-none focus:outline-hidden focus:ring-2 focus:ring-primary/20 focus:border-primary"
              />
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

export default FeedbackManagementPage
