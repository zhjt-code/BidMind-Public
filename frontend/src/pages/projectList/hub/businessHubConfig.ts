/**
 * 职责: 三大业务域 Hub 的静态展示配置（文案、色板、流程步骤、指标定义）
 * 不做: 不含任何数据请求、状态逻辑或路由跳转
 */
import type { ProjectTypeSelection, StatusFilter } from '../types'

export type BusinessHubAccent = 'teal' | 'blue' | 'iris'
export type BusinessHubIconName = 'PenTool' | 'ClipboardCheck' | 'ShieldAlert'
/** 统计条五项：全部 + 四个互斥状态桶，与列表 StatusFilter 分区一致 */
export type BusinessHubMetricKey = 'all' | 'pending' | 'processing' | 'completed' | 'failed'
export type BusinessHubMetricTone = 'blue' | 'slate' | 'orange' | 'green' | 'red'
export type BusinessHubMetricIconName = 'FolderOpen' | 'Upload' | 'Loader' | 'CheckCircle' | 'AlertCircle'

export interface BusinessHubTheme {
  accent: BusinessHubAccent
  /** 外层卡片边框/底色 */
  cardClass: string
  /** 右上角氛围渐变 */
  glowClass: string
  /** 图标底色 */
  iconWrapClass: string
  /** 图标颜色 */
  iconClass: string
  /** 主按钮 */
  primaryButtonClass: string
  /** 步骤序号圆点 */
  stepDotClass: string
  /** 步骤卡片描边/底色 */
  stepCardClass: string
  /** 步骤间连接箭头 */
  stepConnectorClass: string
}

export interface BusinessHubMetricToneClass {
  iconWrap: string
  icon: string
  action: string
  hover: string
}

/** 浅底 Hero 上的状态语义色（蓝全部 / 灰待上传 / 橙处理中 / 绿完成 / 红失败） */
export const HUB_METRIC_TONE_CLASS: Record<BusinessHubMetricTone, BusinessHubMetricToneClass> = {
  blue: {
    iconWrap: 'bg-blue-100',
    icon: 'text-blue-600',
    action: 'text-blue-600',
    hover: 'border-blue-200/80 bg-blue-50/90 hover:bg-blue-100',
  },
  slate: {
    iconWrap: 'bg-slate-100',
    icon: 'text-slate-600',
    action: 'text-slate-600',
    hover: 'border-slate-200/80 bg-slate-50/90 hover:bg-slate-100',
  },
  orange: {
    iconWrap: 'bg-orange-100',
    icon: 'text-orange-600',
    action: 'text-orange-600',
    hover: 'border-orange-200/80 bg-orange-50/90 hover:bg-orange-100',
  },
  green: {
    iconWrap: 'bg-green-100',
    icon: 'text-green-600',
    action: 'text-green-600',
    hover: 'border-green-200/80 bg-green-50/90 hover:bg-green-100',
  },
  red: {
    iconWrap: 'bg-red-100',
    icon: 'text-red-600',
    action: 'text-red-600',
    hover: 'border-red-200/80 bg-red-50/90 hover:bg-red-100',
  },
}

export interface BusinessHubMetricDef {
  key: BusinessHubMetricKey
  label: string
  actionLabel: string
  statusFilter: StatusFilter
  tone: BusinessHubMetricTone
  iconName: BusinessHubMetricIconName
}

export interface BusinessHubFlowStep {
  label: string
}

export interface BusinessHubConfig {
  type: ProjectTypeSelection
  title: string
  tagline: string
  iconName: BusinessHubIconName
  theme: BusinessHubTheme
  flowSteps: BusinessHubFlowStep[]
  metrics: BusinessHubMetricDef[]
}

const SHARED_METRICS: BusinessHubMetricDef[] = [
  {
    key: 'all',
    label: '全部',
    actionLabel: '查看全部',
    statusFilter: 'all',
    tone: 'blue',
    iconName: 'FolderOpen',
  },
  {
    key: 'pending',
    label: '待上传',
    actionLabel: '查看待上传',
    statusFilter: 'pending',
    tone: 'slate',
    iconName: 'Upload',
  },
  {
    key: 'processing',
    label: '处理中',
    actionLabel: '查看进度',
    statusFilter: 'processing',
    tone: 'orange',
    iconName: 'Loader',
  },
  {
    key: 'completed',
    label: '已完成',
    actionLabel: '查看结果',
    statusFilter: 'completed',
    tone: 'green',
    iconName: 'CheckCircle',
  },
  {
    key: 'failed',
    label: '失败',
    actionLabel: '查看失败',
    statusFilter: 'failed',
    tone: 'red',
    iconName: 'AlertCircle',
  },
]

export const BUSINESS_HUB_CONFIGS: Record<ProjectTypeSelection, BusinessHubConfig> = {
  bid_writing: {
    type: 'bid_writing',
    title: '标书编写',
    tagline: '从约束到成稿：上传招标文件，生成目录、章节正文与投标文件初稿。',
    iconName: 'PenTool',
    theme: {
      accent: 'teal',
      cardClass: 'border-teal-200/70 bg-linear-to-br from-white via-teal-50/40 to-teal-100/30',
      glowClass: 'bg-[radial-gradient(circle_at_top_right,rgba(20,184,166,0.18),transparent_42%)]',
      iconWrapClass: 'bg-linear-to-br from-teal-500 to-teal-600',
      iconClass: 'text-white',
      primaryButtonClass: 'bg-teal-600 text-white hover:bg-teal-500',
      stepDotClass: 'bg-teal-100 text-teal-700',
      stepCardClass: 'border-teal-200/80 bg-white/80',
      stepConnectorClass: 'text-teal-300',
    },
    flowSteps: [
      { label: '上传招标文件' },
      { label: '确认约束与目录' },
      { label: '匹配素材并成稿' },
      { label: '预览并导出' },
    ],
    metrics: SHARED_METRICS,
  },
  bid_review: {
    type: 'bid_review',
    title: '投标审查',
    tagline: '确认要求、审查响应：对标招标要求，逐项核验投标文件响应情况。',
    iconName: 'ClipboardCheck',
    theme: {
      accent: 'blue',
      cardClass: 'border-blue-200/70 bg-linear-to-br from-white via-blue-50/40 to-cyan-50/30',
      glowClass: 'bg-[radial-gradient(circle_at_top_right,rgba(37,99,235,0.16),transparent_42%)]',
      iconWrapClass: 'bg-linear-to-br from-blue-500 to-cyan-500',
      iconClass: 'text-white',
      primaryButtonClass: 'bg-blue-600 text-white hover:bg-blue-500',
      stepDotClass: 'bg-blue-100 text-blue-700',
      stepCardClass: 'border-blue-200/80 bg-white/80',
      stepConnectorClass: 'text-blue-300',
    },
    flowSteps: [
      { label: '上传招标与投标文件' },
      { label: '确认招标要求' },
      { label: 'AI 对比审查' },
      { label: '查看审查结果' },
    ],
    metrics: SHARED_METRICS,
  },
  collusion_check: {
    type: 'collusion_check',
    title: '串标审查',
    tagline: '多文件交叉核验：对比多份投标文件，识别相似与关联风险线索。',
    iconName: 'ShieldAlert',
    theme: {
      accent: 'iris',
      cardClass: 'border-[#E3D5F5]/70 bg-linear-to-br from-white via-[#F6F1FC]/40 to-[#F6F1FC]/30',
      glowClass: 'bg-[radial-gradient(circle_at_top_right,rgba(144,86,216,0.16),transparent_42%)]',
      iconWrapClass: 'bg-linear-to-br from-[#7165EA] to-[#9056D8]',
      iconClass: 'text-white',
      primaryButtonClass: 'bg-[#9056D8] text-white hover:bg-[#6C40A2]',
      stepDotClass: 'bg-[#F6F1FC] text-[#6C40A2]',
      stepCardClass: 'border-[#E3D5F5]/80 bg-white/80',
      stepConnectorClass: 'text-[#C4B0EA]',
    },
    flowSteps: [
      { label: '上传多份投标文件' },
      { label: '启动串标分析' },
      { label: '查看风险与证据' },
    ],
    metrics: SHARED_METRICS,
  },
}

export const BUSINESS_HUB_ACCENTS: readonly BusinessHubAccent[] = ['teal', 'blue', 'iris']

export const BUSINESS_HUB_TYPES: readonly ProjectTypeSelection[] = [
  'bid_writing',
  'bid_review',
  'collusion_check',
]

export function getBusinessHubConfig(type: ProjectTypeSelection): BusinessHubConfig {
  return BUSINESS_HUB_CONFIGS[type]
}

export interface HubMetricStatsInput {
  total: number
  pending: number
  processing: number
  completed: number
  failed: number
}

export function resolveHubMetricValue(
  key: BusinessHubMetricKey,
  stats: HubMetricStatsInput,
): number {
  if (key === 'all') return stats.total
  if (key === 'pending') return stats.pending
  if (key === 'processing') return stats.processing
  if (key === 'completed') return stats.completed
  return stats.failed
}
