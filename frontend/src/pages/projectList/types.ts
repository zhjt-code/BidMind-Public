import type { Project } from '@/stores/projectStore'
import { getWritingProjectRoute } from '../writing/writingConstants.ts'

// ---------- 状态配置 ----------

export type BadgeVariantType = 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'outline'

interface StatusConfigItem {
  label: string
  badgeVariant: BadgeVariantType
  actionLabel: string
  stepIndex: { bid_review: number; collusion_check: number; bid_writing: number }
}

export const STATUS_CONFIG: Record<string, StatusConfigItem> = {
  created:          { label: '待上传',     badgeVariant: 'secondary',   actionLabel: '去上传',   stepIndex: { bid_review: 0, collusion_check: 0, bid_writing: 0 } },
  uploading:        { label: '上传中',     badgeVariant: 'outline',     actionLabel: '查看',     stepIndex: { bid_review: 1, collusion_check: 1, bid_writing: 1 } },
  parsing:          { label: '解析中',     badgeVariant: 'outline',     actionLabel: '查看',     stepIndex: { bid_review: 2, collusion_check: 1, bid_writing: 2 } },
  parsed:           { label: '待提取',     badgeVariant: 'warning',     actionLabel: '继续提取', stepIndex: { bid_review: 3, collusion_check: 1, bid_writing: 3 } },
  extracting:       { label: '提取中',     badgeVariant: 'warning',     actionLabel: '查看要求', stepIndex: { bid_review: 3, collusion_check: 2, bid_writing: 3 } },
  extracted:        { label: '待确认',     badgeVariant: 'warning',     actionLabel: '去确认',   stepIndex: { bid_review: 4, collusion_check: 2, bid_writing: 4 } },
  reviewing:        { label: 'AI分析中',   badgeVariant: 'default',     actionLabel: '查看进度', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 4 } },
  outlining:        { label: '生成目录中', badgeVariant: 'default',     actionLabel: '查看目录', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 5 } },
  outlined:         { label: '目录就绪',   badgeVariant: 'warning',     actionLabel: '查看目录', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 6 } },
  matching:         { label: '素材匹配中', badgeVariant: 'default',     actionLabel: '查看匹配', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 7 } },
  matched:          { label: '匹配就绪',   badgeVariant: 'warning',     actionLabel: '确认绑定', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 8 } },
  writing:          { label: '内容生成中', badgeVariant: 'default',     actionLabel: '查看编辑', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 9 } },
  composed:         { label: '预览就绪',   badgeVariant: 'warning',     actionLabel: '预览导出', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 10 } },
  formatting:       { label: '格式渲染中', badgeVariant: 'default',     actionLabel: '查看预览', stepIndex: { bid_review: 5, collusion_check: 2, bid_writing: 11 } },
  completed:        { label: '已完成',     badgeVariant: 'success',     actionLabel: '查看结果', stepIndex: { bid_review: 6, collusion_check: 3, bid_writing: 12 } },
  failed:           { label: '失败',       badgeVariant: 'destructive', actionLabel: '查看失败/恢复', stepIndex: { bid_review: 6, collusion_check: 3, bid_writing: 12 } },
}

export const TOTAL_STEPS: Record<string, number> = { bid_review: 6, collusion_check: 3, bid_writing: 12 }

const FALLBACK_STATUS: StatusConfigItem = {
  label: '未知',
  badgeVariant: 'secondary',
  actionLabel: '查看',
  stepIndex: { bid_review: 0, collusion_check: 0, bid_writing: 0 },
}

const PARSED_COLLUSION_CONFIG: StatusConfigItem = {
  label: '待分析',
  badgeVariant: 'warning',
  actionLabel: '开始分析',
  stepIndex: { bid_review: 3, collusion_check: 1, bid_writing: 3 },
}

export function getStatusConfig(status: string, projectType?: string): StatusConfigItem {
  if (status === 'parsed' && projectType === 'collusion_check') {
    return PARSED_COLLUSION_CONFIG
  }
  return STATUS_CONFIG[status] ?? FALLBACK_STATUS
}

/**
 * 职责: 用步骤下标除以该类型总步数，得到列表进度百分比
 * 不做: 不读心跳、不发请求、不把失败画成 100
 */
export function listProgressPercent(status: string, projectType?: string): number | null {
  if (status === 'failed') return null
  const type = projectType === 'collusion_check' || projectType === 'bid_writing' ? projectType : 'bid_review'
  const total = TOTAL_STEPS[type]
  const step = getStatusConfig(status, type).stepIndex[type]
  return Math.round((step / total) * 100)
}

/** 会自己往前走的状态。待确认等闸门不在这里，避免列表空转轮询。 */
export const LIST_POLL_STATUSES: readonly string[] = [
  'uploading',
  'parsing',
  'extracting',
  'reviewing',
  'outlining',
  'matching',
  'writing',
  'formatting',
]

// ---------- 项目类型配置 ----------

export const PROJECT_TYPE_CONFIG: Record<string, {
  label: string
  className: string
  iconName: 'FileText' | 'ShieldAlert' | 'PenTool'
}> = {
  bid_writing:     { label: '标书编写', className: 'bg-teal-50 text-teal-700 border border-teal-200', iconName: 'PenTool' },
  bid_review:      { label: '投标审查', className: 'bg-blue-50 text-blue-700 border border-blue-200', iconName: 'FileText' },
  collusion_check: { label: '串标审查', className: 'bg-[#F6F1FC] text-[#6C40A2] border border-[#E3D5F5]', iconName: 'ShieldAlert' },
}

// ---------- Tab / 排序 / 筛选 ----------

export type TabKey = 'all' | 'bid_writing' | 'bid_review' | 'collusion_check'
export type SortKey = 'updatedAt' | 'createdAt' | 'name' | 'status'
export type StatusFilter = 'all' | 'pending' | 'processing' | 'completed' | 'failed'
export type UpdatedWithin = 'all' | 'today' | '7d' | '30d'
export type ProjectTypeSelection = 'bid_writing' | 'bid_review' | 'collusion_check'
export type CreateStep = 'type' | 'name'

export const PAGE_SIZE = 10

export const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'updatedAt', label: '最近更新' },
  { value: 'createdAt', label: '创建时间' },
  { value: 'name', label: '项目名称' },
  { value: 'status', label: '项目状态' },
]

export const STATUS_FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'pending', label: '待上传' },
  { value: 'processing', label: '处理中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
]

export const UPDATED_WITHIN_OPTIONS: { value: UpdatedWithin; label: string }[] = [
  { value: 'all', label: '全部时间' },
  { value: 'today', label: '今天' },
  { value: '7d', label: '近7天' },
  { value: '30d', label: '近30天' },
]

const STATUS_FILTER_MAP: Record<StatusFilter, string[]> = {
  all: [],
  pending: ['created'],
  processing: ['uploading', 'parsing', 'parsed', 'extracting', 'extracted', 'reviewing', 'outlining', 'outlined', 'matching', 'matched', 'writing', 'composed', 'formatting'],
  completed: ['completed'],
  failed: ['failed'],
}

// ---------- 路由计算 ----------

export function getTargetRoute(project: Project): string {
  const { status } = project
  if (project.projectType === 'collusion_check') {
    switch (status) {
      case 'created':
      case 'uploading':
      case 'parsing':
      case 'parsed':
        return `/project/${project.id}/collusion/upload`
      case 'reviewing':
        return `/project/${project.id}/collusion/progress`
      case 'completed':
      case 'failed':
        return `/project/${project.id}/collusion/result`
      default:
        return `/project/${project.id}/collusion/upload`
    }
  }
  if (project.projectType === 'bid_writing') {
    switch (status) {
      case 'created':
      case 'uploading':
      case 'parsing':
      case 'extracting':
      case 'extracted':
      case 'outlining':
      case 'outlined':
      case 'matching':
      case 'matched':
      case 'writing':
      case 'composed':
      case 'formatting':
      case 'completed':
      case 'failed':
        return getWritingProjectRoute(project.id, status, project.runMode)
      default:
        return getWritingProjectRoute(project.id, status, project.runMode)
    }
  }
  switch (status) {
    case 'created':
    case 'uploading':
    case 'parsing':
      return `/project/${project.id}/upload`
    case 'parsed':
    case 'extracting':
    case 'extracted':
      return project.runMode === 'express'
        ? `/project/${project.id}/progress`
        : `/project/${project.id}/requirements`
    case 'reviewing':
      return `/project/${project.id}/progress`
    case 'completed':
    case 'failed':
      return `/project/${project.id}/result`
    default:
      return `/project/${project.id}/upload`
  }
}

export function getUploadRoute(projectId: number, projectType: string): string {
  if (projectType === 'collusion_check') {
    return `/project/${projectId}/collusion/upload`
  }
  if (projectType === 'bid_writing') {
    return `/project/${projectId}/writing/upload`
  }
  return `/project/${projectId}/upload`
}

// ---------- 时间格式化 ----------

export function formatRelativeTime(dateString: string | null | undefined): string {
  if (!dateString) return '未知时间'
  const date = new Date(dateString)
  if (isNaN(date.getTime())) return '未知时间'

  const now = Date.now()
  const diff = now - date.getTime()
  const seconds = Math.floor(diff / 1000)

  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}天前`
  return date.toLocaleString('zh-CN')
}

export function formatFullTime(dateString: string | null | undefined): string {
  if (!dateString) return '未知时间'
  const date = new Date(dateString)
  if (isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString('zh-CN')
}

// ---------- 排序 ----------

const STATUS_SORT_WEIGHT: Record<string, number> = {
  formatting: 1,
  composed: 2,
  writing: 3,
  matched: 4,
  matching: 5,
  outlined: 6,
  outlining: 7,
  reviewing: 8,
  extracted: 9,
  extracting: 10,
  parsed: 11,
  parsing: 12,
  uploading: 13,
  created: 14,
  failed: 15,
  completed: 16,
}

export function sortProjects(projects: Project[], sortKey: SortKey): Project[] {
  const sorted = [...projects]
  switch (sortKey) {
    case 'updatedAt':
      return sorted.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    case 'createdAt':
      return sorted.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
    case 'name':
      return sorted.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
    case 'status':
      return sorted.sort((a, b) => (STATUS_SORT_WEIGHT[a.status] ?? 99) - (STATUS_SORT_WEIGHT[b.status] ?? 99))
    default:
      return sorted
  }
}

// ---------- 筛选 ----------

export function filterByStatus(projects: Project[], statusFilter: StatusFilter): Project[] {
  if (statusFilter === 'all') return projects
  const allowedStatuses = STATUS_FILTER_MAP[statusFilter]
  if (!allowedStatuses || allowedStatuses.length === 0) return projects
  return projects.filter(p => allowedStatuses.includes(p.status))
}

export function isProcessingStatus(status: string): boolean {
  return STATUS_FILTER_MAP.processing.includes(status)
}

// B1: 失败合同字段类型（暂不在 UI 引用）
export type { FailureState } from '@/types/failureState'
