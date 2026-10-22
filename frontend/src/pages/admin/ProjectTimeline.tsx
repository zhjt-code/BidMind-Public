import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { errorLogService } from '@/services/errorLogApi'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { toast } from 'sonner'
import {
  Loader2, AlertTriangle, AlertCircle, XCircle, ChevronLeft, ChevronRight,
  PlayCircle, CheckCircle2, XOctagon, Flag, UserCog, Crosshair, Link2,
  ArrowLeft,
} from 'lucide-react'

// ==================== 类型 ====================

interface TimelineProject {
  id: number
  name: string
  status: string | null
  project_type: string | null
  owner_id: number | null
  owner_username: string | null
}

interface TimelineSummary {
  total_events: number
  errors: number
  warnings: number
  milestones: number
  task_starts: number
  task_completed: number
  task_failed: number
  audit_events: number
  incident_count: number
  total_task_duration_ms: number
}

interface ErrorLogTimelineItem {
  kind: 'error_log'
  ts: string | null
  id: number
  level: string
  category: string | null
  task_type: string | null
  module: string | null
  message: string
  incident_id: string | null
  chain_seq: number | null
  is_root_cause: boolean
  exception_type: string | null
  origin_module: string | null
  origin_function: string | null
  origin_line: number | null
  resolved: boolean
}

interface TaskObsTimelineItem {
  kind: 'task_observation'
  ts: string | null
  id: number
  task_id: string
  flow: string
  task_type: string
  phase: string
  stage: string | null
  outcome: string | null
  duration_ms: number | null
  failure_state: string | null
  last_error: string | null
  degraded: boolean
}

interface MilestoneTimelineItem {
  kind: 'milestone'
  ts: string | null
  id: number
  task_type: string | null
  event_key: string
  summary: string | null
  incident_id: string | null
  payload_json: string | null
}

interface AuditTimelineItem {
  kind: 'audit'
  ts: string | null
  id: number
  user_id: number | null
  username: string | null
  action: string
  resource_type: string
  resource_id: number | null
  detail: string | null
}

type TimelineItem =
  | ErrorLogTimelineItem
  | TaskObsTimelineItem
  | MilestoneTimelineItem
  | AuditTimelineItem

interface TimelineResponse {
  project: TimelineProject | null
  summary: TimelineSummary
  items: TimelineItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// ==================== 常量 ====================

const PAGE_SIZE = 50

const TASK_LABELS: Record<string, string> = {
  process: '文档处理',
  extract: '要求提取',
  review: '投标审查',
  collusion: '串标分析',
  writing_extract: '约束提取',
  writing_outline: '目录生成',
  writing_match: '素材匹配',
  writing_compose: '内容生成',
  writing_clause_vectorize: '约束向量化',
}

type LevelMode = 'ALL' | 'ERROR_ONLY' | 'INCLUDE_WARNING'

// ==================== 子组件 ====================

const formatTime = (iso: string | null): string => {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const formatDuration = (ms: number | null): string => {
  if (!ms || ms < 0) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}min`
}

const SummaryBar: React.FC<{ project: TimelineProject; summary: TimelineSummary }> = ({ project, summary }) => (
  <div className="bg-white border rounded-lg p-4 mb-4">
    <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">{project.name}</h2>
        <div className="text-xs text-gray-500 mt-0.5 flex items-center gap-2 flex-wrap">
          <span>项目 ID: {project.id}</span>
          {project.owner_username && <span>所有者: {project.owner_username}</span>}
          {project.status && <span>状态: {project.status}</span>}
          {project.project_type && <span>类型: {project.project_type}</span>}
        </div>
      </div>
    </div>
    <div className="grid grid-cols-4 md:grid-cols-7 gap-3 text-xs">
      <div className="bg-gray-50 rounded p-2">
        <div className="text-gray-500">总事件</div>
        <div className="text-base font-semibold text-gray-900">{summary.total_events}</div>
      </div>
      <div className="bg-red-50 rounded p-2">
        <div className="text-gray-500">错误</div>
        <div className="text-base font-semibold text-red-600">{summary.errors}</div>
      </div>
      <div className="bg-amber-50 rounded p-2">
        <div className="text-gray-500">警告</div>
        <div className="text-base font-semibold text-amber-600">{summary.warnings}</div>
      </div>
      <div className="bg-blue-50 rounded p-2">
        <div className="text-gray-500">里程碑</div>
        <div className="text-base font-semibold text-blue-600">{summary.milestones}</div>
      </div>
      <div className="bg-green-50 rounded p-2">
        <div className="text-gray-500">任务完成</div>
        <div className="text-base font-semibold text-green-600">{summary.task_completed}</div>
      </div>
      <div className="bg-rose-50 rounded p-2">
        <div className="text-gray-500">任务失败</div>
        <div className="text-base font-semibold text-rose-600">{summary.task_failed}</div>
      </div>
      <div className="bg-purple-50 rounded p-2">
        <div className="text-gray-500">总耗时</div>
        <div className="text-base font-semibold text-purple-600">{formatDuration(summary.total_task_duration_ms)}</div>
      </div>
    </div>
  </div>
)

const ErrorLogCard: React.FC<{ item: ErrorLogTimelineItem; onOpenChain?: (iid: string) => void }> = ({ item, onOpenChain }) => {
  const Icon = item.level === 'CRITICAL' ? XCircle : item.level === 'ERROR' ? AlertCircle : AlertTriangle
  const colorClass = item.level === 'CRITICAL'
    ? 'border-red-300 bg-red-50/40'
    : item.level === 'ERROR'
      ? 'border-red-200 bg-red-50/30'
      : 'border-amber-200 bg-amber-50/30'
  const iconColor = item.level === 'CRITICAL'
    ? 'text-red-700'
    : item.level === 'ERROR'
      ? 'text-red-500'
      : 'text-amber-500'
  return (
    <div className={`border rounded-md px-3 py-2 ${colorClass}`}>
      <div className="flex items-start gap-2">
        <Icon className={`h-4 w-4 mt-0.5 flex-shrink-0 ${iconColor}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-[11px] font-medium text-gray-700">{item.level}</span>
            {item.is_root_cause && (
              <span className="text-[11px] text-red-700 bg-red-100 px-1.5 py-0.5 rounded inline-flex items-center gap-0.5">
                <Crosshair className="h-3 w-3" />根因
              </span>
            )}
            {item.exception_type && (
              <span className="text-[11px] text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded font-mono">
                {item.exception_type}
              </span>
            )}
            {item.task_type && (
              <span className="text-[11px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">
                {TASK_LABELS[item.task_type] || item.task_type}
              </span>
            )}
            {item.resolved && (
              <span className="text-[11px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded">已处理</span>
            )}
          </div>
          <p className="text-sm text-gray-800 break-words">{item.message}</p>
          <div className="mt-1 text-[11px] text-gray-500 flex items-center gap-3 flex-wrap">
            <span>{formatTime(item.ts)}</span>
            {item.origin_module ? (
              <span className="font-mono text-rose-500">
                {item.origin_module}
                {item.origin_function ? `:${item.origin_function}` : ''}
                {item.origin_line ? `:L${item.origin_line}` : ''}
              </span>
            ) : (
              item.module && <span className="font-mono">{item.module}</span>
            )}
            {item.incident_id && onOpenChain && (
              <button
                type="button"
                onClick={() => onOpenChain(item.incident_id as string)}
                className="font-mono text-blue-500 hover:text-blue-700 inline-flex items-center gap-0.5"
              >
                <Link2 className="h-3 w-3" />链路 {item.incident_id.slice(0, 8)}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const TaskObsCard: React.FC<{ item: TaskObsTimelineItem }> = ({ item }) => {
  const isStart = item.phase === 'start'
  const isEnd = item.phase === 'end'
  const isSuccess = item.outcome === 'success'
  const Icon = isStart ? PlayCircle : isEnd && isSuccess ? CheckCircle2 : isEnd ? XOctagon : Flag
  const iconColor = isStart
    ? 'text-blue-500'
    : isEnd && isSuccess ? 'text-green-500'
    : isEnd ? 'text-red-500'
    : 'text-amber-500'
  const phaseLabel = isStart
    ? '任务开始'
    : isEnd && isSuccess ? '任务完成'
    : isEnd ? `任务失败 (${item.outcome || '-'})`
    : '任务阶段'
  return (
    <div className="border rounded-md px-3 py-2 bg-white">
      <div className="flex items-start gap-2">
        <Icon className={`h-4 w-4 mt-0.5 flex-shrink-0 ${iconColor}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-[11px] font-medium text-gray-700">{phaseLabel}</span>
            <span className="text-[11px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">
              {TASK_LABELS[item.task_type] || item.task_type}
            </span>
            {item.flow && (
              <span className="text-[11px] text-gray-500 font-mono">{item.flow}</span>
            )}
            {item.degraded && (
              <span className="text-[11px] text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded">已降级</span>
            )}
          </div>
          <div className="text-[11px] text-gray-500 flex items-center gap-3 flex-wrap">
            <span>{formatTime(item.ts)}</span>
            {item.stage && <span>阶段: {item.stage}</span>}
            {item.duration_ms != null && <span>耗时: {formatDuration(item.duration_ms)}</span>}
            {item.failure_state && <span className="text-red-500">{item.failure_state}</span>}
          </div>
          {item.last_error && (
            <p className="mt-1 text-[11px] text-red-600 break-words">{item.last_error}</p>
          )}
        </div>
      </div>
    </div>
  )
}

const MilestoneCard: React.FC<{ item: MilestoneTimelineItem; onOpenChain?: (iid: string) => void }> = ({ item, onOpenChain }) => {
  const isFailed = item.event_key.endsWith('.failed')
  return (
    <div className={`border rounded-md px-3 py-2 ${isFailed ? 'border-red-200 bg-red-50/30' : 'border-blue-200 bg-blue-50/30'}`}>
      <div className="flex items-start gap-2">
        <Flag className={`h-4 w-4 mt-0.5 flex-shrink-0 ${isFailed ? 'text-red-500' : 'text-blue-500'}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-[11px] font-medium text-gray-700">里程碑</span>
            <span className="text-[11px] font-mono text-gray-600">{item.event_key}</span>
            {item.task_type && (
              <span className="text-[11px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">
                {TASK_LABELS[item.task_type] || item.task_type}
              </span>
            )}
          </div>
          {item.summary && (
            <p className="text-sm text-gray-800 break-words">{item.summary}</p>
          )}
          <div className="mt-1 text-[11px] text-gray-500 flex items-center gap-3 flex-wrap">
            <span>{formatTime(item.ts)}</span>
            {item.incident_id && onOpenChain && (
              <button
                type="button"
                onClick={() => onOpenChain(item.incident_id as string)}
                className="font-mono text-blue-500 hover:text-blue-700 inline-flex items-center gap-0.5"
              >
                <Link2 className="h-3 w-3" />链路 {item.incident_id.slice(0, 8)}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const AuditCard: React.FC<{ item: AuditTimelineItem }> = ({ item }) => (
  <div className="border rounded-md px-3 py-2 bg-purple-50/40 border-purple-200">
    <div className="flex items-start gap-2">
      <UserCog className="h-4 w-4 mt-0.5 flex-shrink-0 text-purple-500" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <span className="text-[11px] font-medium text-gray-700">用户操作</span>
          <span className="text-[11px] font-mono text-gray-600">{item.action}</span>
          {item.username && (
            <span className="text-[11px] text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded">
              {item.username}
            </span>
          )}
        </div>
        {item.detail && (
          <p className="text-sm text-gray-800 break-words">{item.detail}</p>
        )}
        <div className="mt-1 text-[11px] text-gray-500">
          <span>{formatTime(item.ts)}</span>
        </div>
      </div>
    </div>
  </div>
)

// ==================== 主页面 ====================

const ProjectTimelinePage: React.FC = () => {
  const params = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const projectId = Number(params.projectId)

  const [data, setData] = useState<TimelineResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)

  const [levelMode, setLevelMode] = useState<LevelMode>('ALL')
  const [taskTypeFilter, setTaskTypeFilter] = useState<string>('all')
  const [keyword, setKeyword] = useState('')
  const [searchInput, setSearchInput] = useState('')

  const focusedIncident = searchParams.get('incident') || null

  const queryParams = useMemo(() => {
    const out: Record<string, unknown> = {
      page,
      page_size: PAGE_SIZE,
    }
    if (levelMode === 'ERROR_ONLY') {
      out.levels = ['ERROR', 'CRITICAL']
    } else if (levelMode === 'INCLUDE_WARNING') {
      out.levels = ['WARNING', 'ERROR', 'CRITICAL']
    }
    if (taskTypeFilter !== 'all') out.task_type = taskTypeFilter
    if (keyword) out.keyword = keyword
    return out
  }, [page, levelMode, taskTypeFilter, keyword])

  const load = useCallback(async () => {
    if (!Number.isInteger(projectId) || projectId <= 0) {
      toast.error('项目 ID 无效')
      return
    }
    setLoading(true)
    try {
      const res = await errorLogService.getProjectTimeline(projectId, queryParams) as {
        data?: TimelineResponse
      }
      setData(res?.data || null)
    } catch {
      toast.error('加载项目时间线失败')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [projectId, queryParams])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [levelMode, taskTypeFilter, keyword])

  const handleSearch = () => setKeyword(searchInput.trim())

  const handleOpenChain = useCallback((incidentId: string) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('incident', incidentId)
      return next
    })
    // 暂时简化: 仅高亮聚焦, 完整链路抽屉用 ErrorLog 页的实现
    toast.message('已聚焦链路', { description: `incident_id: ${incidentId}` })
  }, [setSearchParams])

  const filteredItems = useMemo(() => {
    if (!data?.items) return []
    if (!focusedIncident) return data.items
    // 仅显示包含该 incident_id 的事件
    return data.items.filter(item => {
      if (item.kind === 'error_log' || item.kind === 'milestone') {
        return item.incident_id === focusedIncident
      }
      return false
    })
  }, [data, focusedIncident])

  if (!Number.isInteger(projectId) || projectId <= 0) {
    return (
      <div className="p-6 text-center text-red-500">项目 ID 无效</div>
    )
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft className="h-4 w-4 mr-1" />返回
        </Button>
        <h1 className="text-xl font-bold text-gray-900">项目时间线</h1>
        {focusedIncident && (
          <div className="ml-2 inline-flex items-center gap-1 text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded">
            <Link2 className="h-3 w-3" />
            聚焦链路 {focusedIncident.slice(0, 8)}
            <button
              type="button"
              className="ml-1 text-blue-500 hover:text-blue-700"
              onClick={() => setSearchParams(prev => {
                const next = new URLSearchParams(prev)
                next.delete('incident')
                return next
              })}
            >
              清除
            </button>
          </div>
        )}
      </div>

      {data?.project && <SummaryBar project={data.project} summary={data.summary} />}

      <div className="flex items-center gap-3 mb-4 flex-wrap bg-white border rounded-lg p-3">
        <Select value={levelMode} onValueChange={v => setLevelMode(v as LevelMode)}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部事件</SelectItem>
            <SelectItem value="INCLUDE_WARNING">含警告</SelectItem>
            <SelectItem value="ERROR_ONLY">仅错误</SelectItem>
          </SelectContent>
        </Select>

        <Select value={taskTypeFilter} onValueChange={setTaskTypeFilter}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="任务类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部任务</SelectItem>
            {Object.entries(TASK_LABELS).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="flex items-center gap-1">
          <Input
            placeholder="搜索关键词..."
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            className="w-52 h-9"
          />
          <Button size="sm" variant="outline" onClick={handleSearch}>搜索</Button>
        </div>

        {(levelMode !== 'ALL' || taskTypeFilter !== 'all' || keyword) && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setLevelMode('ALL')
              setTaskTypeFilter('all')
              setKeyword('')
              setSearchInput('')
            }}
          >
            重置
          </Button>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        </div>
      ) : !data || filteredItems.length === 0 ? (
        <div className="text-center py-16 text-gray-400 text-sm">
          {focusedIncident ? '该链路下未匹配到事件' : '暂无符合条件的事件'}
        </div>
      ) : (
        <div className="space-y-2">
          {filteredItems.map(item => (
            <div key={`${item.kind}-${item.id}`}>
              {item.kind === 'error_log' && <ErrorLogCard item={item} onOpenChain={handleOpenChain} />}
              {item.kind === 'task_observation' && <TaskObsCard item={item} />}
              {item.kind === 'milestone' && <MilestoneCard item={item} onOpenChain={handleOpenChain} />}
              {item.kind === 'audit' && <AuditCard item={item} />}
            </div>
          ))}
        </div>
      )}

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm text-gray-500">第 {data.page} / {data.total_pages} 页 (共 {data.total} 条)</span>
          <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage(p => p + 1)}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}

export default ProjectTimelinePage
