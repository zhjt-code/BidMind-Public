import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Loader2, History, RefreshCw, Search } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog'
import { ErrorLogBatchBar } from '@/pages/ErrorLog/components/ErrorLogBatchBar'
import {
  opsService,
  type OpsBusinessFlow,
  type ProjectLogItem,
  type ProjectLogBatchPayload,
} from '@/services/opsApi'

const PAGE_SIZE = 10
const DAYS = 30

interface Props {
  flow: OpsBusinessFlow
}

function statusBadge(item: ProjectLogItem): { label: string; cls: string } {
  if (item.is_deleted) return { label: '已删除', cls: 'bg-gray-100 text-gray-500 border-gray-300' }
  const s = item.status || ''
  if (s === 'failed') return { label: '失败', cls: 'bg-red-50 text-red-700 border-red-200' }
  if (s === 'completed') return { label: '已完成', cls: 'bg-green-50 text-green-700 border-green-200' }
  return { label: s || '处理中', cls: 'bg-blue-50 text-blue-700 border-blue-200' }
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const ProjectLogsPanel: React.FC<Props> = ({ flow }) => {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [onlyUnresolved, setOnlyUnresolved] = useState(false)

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [allMatching, setAllMatching] = useState(false)
  const [excludedIds, setExcludedIds] = useState<Set<number>>(new Set())

  const [batchResolving, setBatchResolving] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => {
      setKeyword(keywordInput.trim())
      setPage(1)
    }, 500)
    return () => clearTimeout(timer)
  }, [keywordInput])

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['ops-project-logs', flow, page, keyword, onlyUnresolved],
    queryFn: () => opsService.getProjectLogs(flow, {
      page,
      page_size: PAGE_SIZE,
      keyword: keyword || undefined,
      days: DAYS,
      only_unresolved: onlyUnresolved,
    }),
    staleTime: 15_000,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = data?.total_pages ?? 0

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set())
    setAllMatching(false)
    setExcludedIds(new Set())
  }, [])

  // 切换 flow / 筛选时清空选择, 避免跨筛选误删
  useEffect(() => {
    clearSelection()
  }, [flow, keyword, onlyUnresolved, clearSelection])

  const isSelected = useCallback(
    (pid: number) => (allMatching ? !excludedIds.has(pid) : selectedIds.has(pid)),
    [allMatching, excludedIds, selectedIds],
  )

  const currentPageSelectedCount = useMemo(
    () => items.filter((it) => isSelected(it.project_id)).length,
    [items, isSelected],
  )
  const allCurrentPageSelected = items.length > 0 && currentPageSelectedCount === items.length
  const selectedCount = allMatching ? Math.max(total - excludedIds.size, 0) : selectedIds.size

  const toggleSelect = useCallback((pid: number) => {
    if (allMatching) {
      setExcludedIds((prev) => {
        const next = new Set(prev)
        if (next.has(pid)) next.delete(pid)
        else next.add(pid)
        return next
      })
      return
    }
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(pid)) next.delete(pid)
      else next.add(pid)
      return next
    })
  }, [allMatching])

  const toggleSelectAll = useCallback(() => {
    const ids = items.map((it) => it.project_id)
    if (allMatching) {
      setExcludedIds((prev) => {
        const next = new Set(prev)
        if (allCurrentPageSelected) ids.forEach((id) => next.add(id))
        else ids.forEach((id) => next.delete(id))
        return next
      })
      return
    }
    setSelectedIds(allCurrentPageSelected ? new Set() : new Set(ids))
  }, [allCurrentPageSelected, allMatching, items])

  const selectAllMatching = useCallback(() => {
    setAllMatching(true)
    setSelectedIds(new Set())
    setExcludedIds(new Set())
  }, [])

  const buildPayload = useCallback((): ProjectLogBatchPayload => {
    if (allMatching) {
      return {
        select_all: true,
        exclude_project_ids: Array.from(excludedIds),
        keyword: keyword || undefined,
        days: DAYS,
        only_unresolved: onlyUnresolved,
      }
    }
    return { project_ids: Array.from(selectedIds) }
  }, [allMatching, excludedIds, keyword, onlyUnresolved, selectedIds])

  const handleBatchResolve = useCallback(async () => {
    if (selectedCount === 0) return
    setBatchResolving(true)
    try {
      const r = await opsService.batchResolveProjectLogs(flow, buildPayload())
      toast.success(`已标记 ${r.resolved} 条错误为已处理 (${r.projects} 个项目)`)
      clearSelection()
      await refetch()
    } catch (e) {
      toast.error(`批量标记失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBatchResolving(false)
    }
  }, [buildPayload, clearSelection, flow, refetch, selectedCount])

  const handleBatchDelete = useCallback(async () => {
    setShowDeleteConfirm(false)
    if (selectedCount === 0) return
    setBatchDeleting(true)
    try {
      const r = await opsService.batchDeleteProjectLogs(flow, buildPayload())
      toast.success(`已清理 ${r.deleted} 条错误日志 (${r.projects} 个项目)`)
      clearSelection()
      await refetch()
    } catch (e) {
      toast.error(`批量清理失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBatchDeleting(false)
    }
  }, [buildPayload, clearSelection, flow, refetch, selectedCount])

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-sm font-medium text-gray-700">
          项目日志追踪
          <span className="text-xs text-gray-400 ml-1">(近 {DAYS} 天 · 共 {total} 个项目)</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              className="h-3.5 w-3.5"
              checked={onlyUnresolved}
              onChange={(e) => { setOnlyUnresolved(e.target.checked); setPage(1) }}
            />
            仅看未处理错误
          </label>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
            <Input
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              placeholder="项目名 / 用户名"
              className="h-8 w-48 pl-7 text-sm"
            />
          </div>
          <Button variant="outline" size="sm" className="h-8" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </div>

      <ErrorLogBatchBar
        viewMode="flat"
        logsLength={items.length}
        groupsLength={0}
        total={total}
        allCurrentPageSelected={allCurrentPageSelected}
        currentPageSelectedCount={currentPageSelectedCount}
        allMatchingSelected={allMatching}
        excludedIdsSize={excludedIds.size}
        selectedCount={selectedCount}
        selectedFingerprintsSize={0}
        batchDeleting={batchDeleting}
        batchResolving={batchResolving}
        onToggleSelectAll={toggleSelectAll}
        onSelectAllMatching={selectAllMatching}
        onClearSelection={clearSelection}
        onBatchResolve={handleBatchResolve}
        onBatchDelete={() => setShowDeleteConfirm(true)}
      />

      {isLoading ? (
        <div className="flex items-center gap-2 text-gray-500 py-8 justify-center">
          <Loader2 className="h-4 w-4 animate-spin" />加载中…
        </div>
      ) : isError ? (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 p-3 rounded text-sm">项目日志加载失败。</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-400 py-8 text-center">近 {DAYS} 天内该链路暂无项目日志。</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                <th className="py-2 pr-2 w-8" />
                <th className="py-2 pr-4">项目</th>
                <th className="py-2 pr-4">属主</th>
                <th className="py-2 pr-4">状态</th>
                <th className="py-2 pr-4 text-right">错误</th>
                <th className="py-2 pr-4 text-right">警告</th>
                <th className="py-2 pr-4">最近活动</th>
                <th className="py-2 pr-4">最近错误</th>
                <th className="py-2 pr-2" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const sb = statusBadge(it)
                return (
                  <tr key={it.project_id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 pr-2">
                      <input
                        type="checkbox"
                        className="h-4 w-4"
                        checked={isSelected(it.project_id)}
                        onChange={() => toggleSelect(it.project_id)}
                      />
                    </td>
                    <td className="py-2 pr-4">
                      <div className="font-medium text-gray-800 truncate max-w-[180px]" title={it.project_name || ''}>
                        {it.project_name || `#${it.project_id}`}
                      </div>
                      <div className="text-[10px] text-gray-400">#{it.project_id}</div>
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{it.owner_username || '-'}</td>
                    <td className="py-2 pr-4">
                      <span className={`text-[11px] px-1.5 py-0.5 rounded border ${sb.cls}`}>{sb.label}</span>
                    </td>
                    <td className={`py-2 pr-4 text-right font-mono ${it.error_count > 0 ? 'text-red-600' : 'text-gray-400'}`}>
                      {it.error_count}
                      {it.unresolved_count > 0 ? (
                        <span className="text-[10px] text-amber-600"> ({it.unresolved_count}未处理)</span>
                      ) : null}
                    </td>
                    <td className={`py-2 pr-4 text-right font-mono ${it.warning_count > 0 ? 'text-amber-600' : 'text-gray-400'}`}>
                      {it.warning_count}
                    </td>
                    <td className="py-2 pr-4 text-gray-500 whitespace-nowrap">{formatTime(it.last_activity_at)}</td>
                    <td className="py-2 pr-4 text-gray-500 truncate max-w-[220px]" title={it.last_error_summary || ''}>
                      {it.last_error_summary || '-'}
                    </td>
                    <td className="py-2 pr-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-primary"
                        onClick={() => navigate(`/admin/projects/${it.project_id}/timeline`)}
                      >
                        <History className="h-3.5 w-3.5 mr-1" />时间线
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-gray-500 pt-2">
          <span>第 {page} / {totalPages} 页</span>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" className="h-7" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              上一页
            </Button>
            <Button variant="outline" size="sm" className="h-7" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
              下一页
            </Button>
          </div>
        </div>
      )}

      <Dialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认批量清理日志</DialogTitle>
            <DialogDescription>
              将清理选中 {selectedCount} 个项目的错误日志 (ERROR/WARNING/CRITICAL)。
              任务观测与里程碑骨架不受影响。此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteConfirm(false)}>取消</Button>
            <Button variant="destructive" onClick={handleBatchDelete}>确认清理</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default ProjectLogsPanel
