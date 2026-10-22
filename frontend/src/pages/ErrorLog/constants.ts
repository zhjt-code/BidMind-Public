import {
  AlertTriangle, AlertCircle, XCircle, Server, Cpu,
} from 'lucide-react'
import type React from 'react'

export const LEVEL_CONFIG: Record<string, { label: string; className: string; icon: React.ElementType }> = {
  WARNING: { label: '警告', className: 'bg-amber-100 text-amber-700 border-amber-200', icon: AlertTriangle },
  ERROR: { label: '错误', className: 'bg-red-100 text-red-700 border-red-200', icon: AlertCircle },
  CRITICAL: { label: '严重', className: 'bg-red-200 text-red-900 border-red-300', icon: XCircle },
}

export const SOURCE_CONFIG: Record<string, { label: string; icon: React.ElementType }> = {
  backend: { label: '接口服务', icon: Server },
  worker: { label: '异步任务', icon: Cpu },
}

export const TASK_LABELS: Record<string, string> = {
  process: '文档处理',
  extract: '要求提取',
  review: '投标审查',
  collusion: '串标分析',
  writing_extract: '约束提取',
  writing_clause_vectorize: '约束向量化',
  writing_outline: '目录生成',
  writing_match: '素材匹配',
  writing_compose: '内容生成',
}

export const CATEGORY_LABELS: Record<string, string> = {
  bid_review: '投标审查',
  collusion_check: '串标审查',
  bid_writing: '标书编写',
  system: '系统',
}

export const TASK_BY_CATEGORY: Record<string, Record<string, string>> = {
  bid_review: { process: '文档处理', extract: '要求提取', review: '投标审查' },
  collusion_check: { collusion: '串标分析' },
  bid_writing: {
    writing_extract: '约束提取',
    writing_clause_vectorize: '约束向量化',
    writing_outline: '目录生成',
    writing_match: '素材匹配',
    writing_compose: '内容生成',
  },
}

export const STAGE_LABELS: Record<string, string> = {
  start: '开始',
  end: '结束',
}

export const TIME_RANGE_OPTIONS: { label: string; hours: number }[] = [
  { label: '近1小时', hours: 1 },
  { label: '近24小时', hours: 24 },
  { label: '近7天', hours: 168 },
  { label: '全部时间', hours: 0 },
]

export const PAGE_SIZE = 20

export const GROUP_PAGE_SIZE = 15
