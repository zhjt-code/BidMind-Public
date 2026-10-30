import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { auditService } from '@/services/auditApi'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { toast } from 'sonner'
import { Loader2, Trash2, ListChecks, X } from 'lucide-react'

interface AuditLogItem {
  id: number
  user_id: number | null
  username: string | null
  category: string
  action: string
  resource_type: string
  resource_id: number | null
  detail: string | null
  ip_address: string | null
  created_at: string | null
}

interface AuditStats {
  total: number
  by_category: Record<string, number>
  by_action: Record<string, number>
}

const CATEGORY_LABELS: Record<string, string> = {
  bid_review: '投标审查',
  collusion_check: '串标审查',
  bid_writing: '标书编写',
  system: '系统管理',
}

const CATEGORY_COLORS: Record<string, string> = {
  bid_review: 'text-blue-700',
  collusion_check: 'text-[#6C40A2]',
  bid_writing: 'text-emerald-700',
  system: 'text-gray-700',
}

const RESOURCE_BY_CATEGORY: Record<string, Record<string, string>> = {
  bid_review: {
    project: '项目',
    requirement: '招标要求',
    review: '审查',
    report: '报告',
  },
  collusion_check: {
    project: '项目',
    collusion_analysis: '串标分析',
    collusion_report: '串标报告',
  },
  bid_writing: {
    project: '项目',
    outline: '目录',
    material: '素材',
    section: '章节',
    writing_report: '编写报告',
  },
  system: {
    user: '用户',
    settings: '设置',
  },
}

const ALL_RESOURCE_LABELS: Record<string, string> = Object.values(RESOURCE_BY_CATEGORY)
  .reduce((acc, map) => ({ ...acc, ...map }), {})

const ACTION_LABELS: Record<string, string> = {
  create: '创建',
  delete: '删除',
  update: '更新',
  confirm: '确认',
  start_review: '启动审查',
  download: '下载报告',
  login: '登录',
  register: '注册',
}

const ACTION_COLORS: Record<string, string> = {
  create: 'bg-green-100 text-green-700',
  delete: 'bg-red-100 text-red-700',
  update: 'bg-blue-100 text-blue-700',
  confirm: 'bg-purple-100 text-purple-700',
  start_review: 'bg-indigo-100 text-indigo-700',
  download: 'bg-cyan-100 text-cyan-700',
  login: 'bg-gray-100 text-gray-700',
  register: 'bg-emerald-100 text-emerald-700',
}

const PAGE_SIZE = 20

// -- 统计卡片 --

const StatsBar: React.FC<{ stats: AuditStats | null; loading: boolean }> = ({ stats, loading }) => {
  if (loading || !stats) {
    return (
      <div className="grid grid-cols-5 gap-4 mb-6">
        {[1, 2, 3, 4, 5].map(i => (
          <div key={i} className="bg-white rounded-lg border p-4 animate-pulse">
            <div className="h-4 w-20 bg-gray-200 rounded mb-2" />
            <div className="h-7 w-12 bg-gray-200 rounded" />
          </div>
        ))}
      </div>
    )
  }

  const cards = [
    { label: '总计', value: stats.total, color: 'text-gray-900' },
    { label: '投标审查', value: stats.by_category?.bid_review || 0, color: 'text-blue-600' },
    { label: '串标审查', value: stats.by_category?.collusion_check || 0, color: 'text-[#9056D8]' },
    { label: '标书编写', value: stats.by_category?.bid_writing || 0, color: 'text-emerald-600' },
    { label: '系统管理', value: stats.by_category?.system || 0, color: 'text-gray-600' },
  ]

  return (
    <div className="grid grid-cols-5 gap-4 mb-6">
      {cards.map(card => (
        <div key={card.label} className="bg-white rounded-lg border p-4">
          <p className="text-xs text-gray-500 mb-1">{card.label}</p>
          <p className={`text-2xl font-bold ${card.color}`}>{card.value}</p>
        </div>
      ))}
    </div>
  )
}

// -- 主页面 --

const AuditLogPage: React.FC = () => {
  const [logs, setLogs] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(0)
  const [loading, setLoading] = useState(true)

  const [stats, setStats] = useState<AuditStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)

  // 筛选
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [actionFilter, setActionFilter] = useState<string>('all')
  const [resourceFilter, setResourceFilter] = useState<string>('all')
  const [keyword, setKeyword] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [username, setUsername] = useState('')
  const [usernameInput, setUsernameInput] = useState('')

  // 批量管理
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [allMatchingSelected, setAllMatchingSelected] = useState(false)
  const [excludedIds, setExcludedIds] = useState<Set<number>>(new Set())
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [jumpPageInput, setJumpPageInput] = useState('1')

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set())
    setAllMatchingSelected(false)
    setExcludedIds(new Set())
  }, [])

  const exitSelectionMode = useCallback(() => {
    setSelectionMode(false)
    clearSelection()
  }, [clearSelection])

  // 联动: 当 category 变化时重置 resource filter
  useEffect(() => {
    setResourceFilter('all')
  }, [categoryFilter])

  const currentResourceOptions = useMemo(() => {
    if (categoryFilter === 'all') return ALL_RESOURCE_LABELS
    return RESOURCE_BY_CATEGORY[categoryFilter] || {}
  }, [categoryFilter])

  const activeFilters = useMemo<Record<string, unknown>>(() => {
    const filters: Record<string, unknown> = {}
    if (categoryFilter !== 'all') filters.category = categoryFilter
    if (actionFilter !== 'all') filters.action = actionFilter
    if (resourceFilter !== 'all') filters.resource_type = resourceFilter
    if (username) filters.username = username
    if (keyword) filters.keyword = keyword
    return filters
  }, [categoryFilter, actionFilter, resourceFilter, username, keyword])

  const isLogSelected = useCallback((id: number) => {
    if (allMatchingSelected) return !excludedIds.has(id)
    return selectedIds.has(id)
  }, [allMatchingSelected, excludedIds, selectedIds])

  const selectedCount = useMemo(() => {
    if (allMatchingSelected) return Math.max(total - excludedIds.size, 0)
    return selectedIds.size
  }, [allMatchingSelected, excludedIds, selectedIds, total])

  const currentPageSelectedCount = useMemo(
    () => logs.filter(log => isLogSelected(log.id)).length,
    [logs, isLogSelected],
  )

  const allCurrentPageSelected = logs.length > 0 && currentPageSelectedCount === logs.length

  const getBatchPayload = useCallback(() => {
    if (allMatchingSelected) {
      return {
        select_all: true,
        exclude_ids: Array.from(excludedIds),
        filters: activeFilters,
      }
    }
    return { log_ids: Array.from(selectedIds) }
  }, [activeFilters, allMatchingSelected, excludedIds, selectedIds])

  const handlePageChange = useCallback((nextPage: number) => {
    const safePage = Math.min(Math.max(nextPage, 1), Math.max(totalPages, 1))
    setPage(safePage)
  }, [totalPages])

  const handleJumpToPage = useCallback(() => {
    const targetPage = Number(jumpPageInput)
    if (!Number.isInteger(targetPage)) {
      toast.error('请输入有效页码')
      return
    }
    if (targetPage < 1 || targetPage > totalPages) {
      toast.error(`页码范围为 1 - ${totalPages}`)
      return
    }
    handlePageChange(targetPage)
  }, [handlePageChange, jumpPageInput, totalPages])

  const selectAllMatchingLogs = () => {
    setAllMatchingSelected(true)
    setSelectedIds(new Set())
    setExcludedIds(new Set())
  }

  const toggleSelect = (id: number) => {
    if (allMatchingSelected) {
      setExcludedIds(prev => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
      return
    }
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    const allIds = logs.map(l => l.id)
    if (allMatchingSelected) {
      setExcludedIds(prev => {
        const next = new Set(prev)
        if (allCurrentPageSelected) {
          allIds.forEach(id => next.add(id))
        } else {
          allIds.forEach(id => next.delete(id))
        }
        return next
      })
      return
    }
    if (allCurrentPageSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(allIds))
    }
  }

  const loadStats = useCallback(async () => {
    setStatsLoading(true)
    try {
      const res = await auditService.getStats() as { data?: AuditStats }
      setStats(res?.data || null)
    } catch {
      setStats(null)
    } finally {
      setStatsLoading(false)
    }
  }, [])

  const loadLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = {
        page,
        page_size: PAGE_SIZE,
        ...activeFilters,
      }
      const res = (await auditService.getLogs(params)) as {
        data?: { items?: AuditLogItem[]; total?: number; total_pages?: number }
      }
      const data = res?.data
      setLogs(data?.items || [])
      setTotal(data?.total || 0)
      setTotalPages(data?.total_pages || 0)
    } catch {
      setLogs([])
      setTotal(0)
      setTotalPages(0)
    } finally {
      setLoading(false)
    }
  }, [activeFilters, page])

  useEffect(() => { loadStats() }, [loadStats])
  useEffect(() => { loadLogs() }, [loadLogs])
  useEffect(() => { setPage(1) }, [categoryFilter, actionFilter, resourceFilter, username, keyword])
  useEffect(() => { setJumpPageInput(String(page)) }, [page])

  useEffect(() => {
    if (selectionMode) clearSelection()
  }, [clearSelection, username, keyword, categoryFilter, actionFilter, resourceFilter, selectionMode])

  useEffect(() => {
    if (selectionMode && !allMatchingSelected) setSelectedIds(new Set())
  }, [allMatchingSelected, page, selectionMode])

  const handleBatchDelete = async () => {
    if (selectedCount === 0) return
    if (!window.confirm(`确认删除选中的 ${selectedCount} 条审计日志? 此操作不可恢复。`)) return

    setBatchDeleting(true)
    try {
      const res = await auditService.batchDelete(getBatchPayload()) as {
        data?: { deleted?: number }
      }
      const deleted = res?.data?.deleted || 0
      toast.success(`已删除 ${deleted} 条审计日志`)
      loadLogs()
      loadStats()
      exitSelectionMode()
    } catch {
      toast.error('批量删除失败')
    } finally {
      setBatchDeleting(false)
    }
  }

  const handleSearch = () => {
    setUsername(usernameInput.trim())
    setKeyword(searchInput.trim())
  }

  const hasFilters = categoryFilter !== 'all' || actionFilter !== 'all' || resourceFilter !== 'all' || username || keyword

  const formatTime = (iso: string | null) => {
    if (!iso) return '-'
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  }

  return (
    <div>
      {/* 标题 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">关键操作审计</h1>
          <p className="text-sm text-gray-500 mt-1">记录后台关键操作与敏感变更轨迹</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">共 {total} 条记录</span>
          <Button
            variant={selectionMode ? 'default' : 'outline'}
            size="sm"
            onClick={() => selectionMode ? exitSelectionMode() : setSelectionMode(true)}
          >
            {selectionMode ? (
              <><X className="h-4 w-4 mr-1" />退出批量管理</>
            ) : (
              <><ListChecks className="h-4 w-4 mr-1" />批量管理</>
            )}
          </Button>
        </div>
      </div>

      {/* 统计卡片 */}
      <StatsBar stats={stats} loading={statsLoading} />

      {/* 筛选栏 */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="业务类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部业务</SelectItem>
            {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
              <SelectItem key={key} value={key}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={resourceFilter} onValueChange={setResourceFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="资源类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部资源</SelectItem>
            {Object.entries(currentResourceOptions).map(([key, label]) => (
              <SelectItem key={key} value={key}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={actionFilter} onValueChange={setActionFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="操作类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部操作</SelectItem>
            {Object.entries(ACTION_LABELS).map(([key, label]) => (
              <SelectItem key={key} value={key}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="flex items-center gap-1">
          <Input
            placeholder="搜索用户名..."
            value={usernameInput}
            onChange={e => setUsernameInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            className="w-40 h-9"
            aria-label="按用户名搜索"
          />
          <Input
            placeholder="搜索详情关键词..."
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            className="w-52 h-9"
          />
          <Button size="sm" variant="outline" onClick={handleSearch}>
            搜索
          </Button>
        </div>

        {hasFilters && (
          <Button
            variant="ghost" size="sm"
            onClick={() => {
              setCategoryFilter('all')
              setActionFilter('all')
              setResourceFilter('all')
              setUsername('')
              setUsernameInput('')
              setKeyword('')
              setSearchInput('')
            }}
          >
            重置筛选
          </Button>
        )}
      </div>

      {/* 批量操作栏 */}
      {selectionMode && logs.length > 0 && (
        <div className="px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg mb-3 flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                checked={allCurrentPageSelected}
                onChange={toggleSelectAll}
              />
              全选当前页 ({currentPageSelectedCount}/{logs.length})
            </label>

            {!allMatchingSelected && total > logs.length && (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-primary hover:text-primary"
                onClick={selectAllMatchingLogs}
              >
                全选符合当前筛选的 {total} 条
              </Button>
            )}

            {allMatchingSelected && (
              <>
                <span className="text-sm text-primary">
                  已选中全部匹配日志 {selectedCount} 条
                  {excludedIds.size > 0 ? `，已排除 ${excludedIds.size} 条` : ''}
                </span>
                <Button variant="ghost" size="sm" className="h-8 px-2" onClick={clearSelection}>
                  清空选择
                </Button>
              </>
            )}
          </div>

          {selectedCount > 0 && (
            <div className="flex items-center gap-2">
              <Button
                variant="destructive"
                size="sm"
                disabled={batchDeleting}
                onClick={handleBatchDelete}
              >
                {batchDeleting ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-1" />
                )}
                批量删除 ({selectedCount})
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 日志表格 */}
      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          </div>
        ) : logs.length === 0 ? (
          <div className="text-center py-16 text-gray-400 text-sm">
            {hasFilters ? '没有匹配的审计日志' : '暂无审计日志'}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {selectionMode && <TableHead className="w-[40px]" />}
                <TableHead className="w-[170px]">时间</TableHead>
                <TableHead className="w-[80px]">用户</TableHead>
                <TableHead className="w-[90px]">业务类型</TableHead>
                <TableHead className="w-[90px]">操作</TableHead>
                <TableHead className="w-[90px]">资源类型</TableHead>
                <TableHead className="w-[70px]">资源ID</TableHead>
                <TableHead>详情</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.map(log => (
                <TableRow
                  key={log.id}
                  className={`${selectionMode && isLogSelected(log.id) ? 'bg-primary/5' : ''}`}
                  onClick={selectionMode ? () => toggleSelect(log.id) : undefined}
                >
                  {selectionMode && (
                    <TableCell>
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                        checked={isLogSelected(log.id)}
                        onChange={() => toggleSelect(log.id)}
                        onClick={e => e.stopPropagation()}
                      />
                    </TableCell>
                  )}
                  <TableCell className="text-xs text-gray-500">{formatTime(log.created_at)}</TableCell>
                  <TableCell className="text-sm">{log.username || '-'}</TableCell>
                  <TableCell>
                    <span className={`text-xs font-medium ${CATEGORY_COLORS[log.category] || 'text-gray-600'}`}>
                      {CATEGORY_LABELS[log.category] || log.category}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[log.action] || 'bg-gray-100 text-gray-600'}`}>
                      {ACTION_LABELS[log.action] || log.action}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs">
                      {ALL_RESOURCE_LABELS[log.resource_type] || log.resource_type}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-gray-500">{log.resource_id ?? '-'}</TableCell>
                  <TableCell className="text-xs text-gray-600 max-w-[300px] truncate">{log.detail || '-'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
          <Button
            variant="outline" size="sm"
            disabled={page <= 1}
            onClick={() => handlePageChange(page - 1)}
          >
            上一页
          </Button>
          <span className="text-sm text-gray-500">第 {page} / {totalPages} 页</span>
          <div className="flex items-center gap-2">
            <Input
              value={jumpPageInput}
              inputMode="numeric"
              placeholder="页码"
              className="w-20 h-9"
              onChange={e => setJumpPageInput(e.target.value.replace(/\D/g, ''))}
              onKeyDown={e => e.key === 'Enter' && handleJumpToPage()}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!jumpPageInput || Number(jumpPageInput) === page}
              onClick={handleJumpToPage}
            >
              跳转
            </Button>
          </div>
          <Button
            variant="outline" size="sm"
            disabled={page >= totalPages}
            onClick={() => handlePageChange(page + 1)}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  )
}

export default AuditLogPage
