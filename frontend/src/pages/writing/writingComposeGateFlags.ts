/**
 * P2.1c: 把 SectionDraft.gateFlags 映射为前端可展示的 badge 列表。
 *
 * 职责: 将后端 gate_flags raw 结构归一化为 UI 友好的短标签 (label) + 详情 (hint) + 严重等级 (severity)。
 * 不做: 不感知具体组件样式 (颜色由 UI 层按 severity 决定)、不访问 store / API。
 *
 * 抽成纯函数便于单元测试, 与 writingPreviewDownloadHelpers 风格对齐。
 */

import type { GateFlagEntry } from '@/services/writingApi'
import { toUserFacingClauseText } from './writingClauseDisplay.ts'

/**
 * Gate badge 的严重等级:
 * - 'high': 废标级风险, 必须人工处理 (例: 固定模板缺少可回查原文)
 * - 'normal': 一般告警, 可选关注 (例: 表格填充率偏低, AI 套话)
 */
export type GateBadgeSeverity = 'high' | 'normal'

export interface GateBadge {
  /** 稳定 key, 用作 React key */
  key: string
  /** 短标签文本 (章节列表角标用), 必须为中文短语, 禁止透传英文标识符 */
  label: string
  /** 鼠标悬停时的详细描述, 优先透传后端 reason */
  hint: string
  /** 严重等级, UI 据此切换琥珀 / 红色 */
  severity: GateBadgeSeverity
}

export interface GateWarningSource {
  nodeId: number
  title: string
  gateFlags: Record<string, GateFlagEntry> | null | undefined
}

export interface GateWarningSummaryItem {
  nodeId: number
  title: string
  badges: GateBadge[]
}

export interface GateWarningSummary {
  total: number
  high: number
  items: GateWarningSummaryItem[]
}

const BADGE_SEVERITY_RANK: Record<GateBadgeSeverity, number> = {
  high: 0,
  normal: 1,
}

const BADGE_KEY_RANK: Record<string, number> = {
  fixed_template_requires_review: 0,
  fixed_template_placeholder: 10,
  unbound_cited: 20,
  material_trace_missing: 30,
  material_low_confidence: 40,
  writer_cove: 50,
  table_fill_rate: 60,
  ai_phrase_count: 70,
}

function getBadgeKeyRank(key: string): number {
  return BADGE_KEY_RANK[key] ?? 999
}

function compareBadgesByImportance(a: GateBadge, b: GateBadge): number {
  const severityDelta = BADGE_SEVERITY_RANK[a.severity] - BADGE_SEVERITY_RANK[b.severity]
  if (severityDelta !== 0) return severityDelta
  return getBadgeKeyRank(a.key) - getBadgeKeyRank(b.key)
}

function sortGateBadgesByImportance(badges: GateBadge[]): GateBadge[] {
  return badges
    .map((badge, index) => ({ badge, index }))
    .sort((a, b) => compareBadgesByImportance(a.badge, b.badge) || a.index - b.index)
    .map(({ badge }) => badge)
}

function sortSummaryItemsByImportance(items: GateWarningSummaryItem[]): GateWarningSummaryItem[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const badgeDelta = compareBadgesByImportance(a.item.badges[0], b.item.badges[0])
      return badgeDelta || a.index - b.index
    })
    .map(({ item }) => item)
}

function cleanGateHint(rawText: string | null | undefined, fallback: string): string {
  return toUserFacingClauseText(rawText) || fallback
}

/**
 * 把 raw gate_flags (后端按 gate_name 分组) 映射成 UI 友好的 badge 列表。
 * 未知 gate_name 会走 default 兜底, 不会暴露原始标识符。
 */
export function gateFlagsToBadges(
  flags: Record<string, GateFlagEntry> | null | undefined,
): GateBadge[] {
  if (!flags || typeof flags !== 'object') return []
  const badges: GateBadge[] = []

  for (const [name, entry] of Object.entries(flags)) {
    if (!entry || typeof entry !== 'object') continue
    const badge = transformSingle(name, entry)
    if (badge) badges.push(badge)
  }

  return sortGateBadgesByImportance(badges)
}

function transformSingle(name: string, entry: GateFlagEntry): GateBadge | null {
  const trace = entry.trace || {}
  const patch = entry.patch || {}
  const reason = entry.reason || ''

  switch (name) {
    case 'MaterialSourceTraceGate': {
      if (patch.material_trace_missing) {
        return {
          key: 'material_trace_missing',
          label: '素材未引用',
          hint: '生成章节未引用任何绑定素材',
          severity: 'normal',
        }
      }
      if (Array.isArray(patch.unbound_material_ids)
          && (patch.unbound_material_ids as unknown[]).length > 0) {
        return {
          key: 'unbound_cited',
          label: '引用未绑定素材',
          hint: `章节引用了未绑定的素材编号：${
            (patch.unbound_material_ids as unknown[]).join(', ')
          }`,
          severity: 'normal',
        }
      }
      if (patch.low_confidence) {
        return {
          key: 'material_low_confidence',
          label: '素材未绑定',
          hint: '该章节未绑定任何素材, 生成内容仅基于约束与行业画像',
          severity: 'normal',
        }
      }
      return null
    }
    case 'TableFieldFillRateGate': {
      const rate = typeof trace.fill_rate === 'number'
        ? (trace.fill_rate as number)
        : typeof patch.table_fill_rate === 'number'
        ? (patch.table_fill_rate as number)
        : null
      if (rate === null) return null
      const pct = Math.round(rate * 100)
      return {
        key: 'table_fill_rate',
        label: `表格填充率 ${pct}%`,
        hint: `资料填充章节表格单元格非空率 ${pct}%，存在"格式对但内容空"风险`,
        severity: 'normal',
      }
    }
    case 'DeAIFilterGate': {
      const count = typeof trace.ai_phrase_count === 'number'
        ? (trace.ai_phrase_count as number)
        : null
      if (count === null) return null
      return {
        key: 'ai_phrase_count',
        label: `AI 套话 ${count}`,
        hint: `检测到 ${count} 处常见 AI 套话, 建议人工替换为具体表达`,
        severity: 'normal',
      }
    }
    case 'FixedTemplatePlaceholderGate': {
      return {
        key: 'fixed_template_placeholder',
        label: '模板占位未替换',
        hint: cleanGateHint(reason, '固定模板中仍有 [待补充:xxx] 占位符'),
        severity: 'normal',
      }
    }
    case 'WriterCoVe': {
      const outcome = String(trace.outcome || '').toLowerCase()
      return {
        key: 'writer_cove',
        label: '待复核',
        hint: outcome === 'disagree'
          ? '承诺内容与约束或素材存在不一致，请人工核对。'
          : '承诺内容核验不确定，请人工核对。',
        severity: 'normal',
      }
    }
    case 'fixed_template_requires_review': {
      // 固定模板章节缺少可回查原文 / 快照不完整 / 来源不匹配,
      // 走废标级硬门禁, UI 必须用红色 + "需人工复核" 提示, 禁止暴露 snake_case 原始 name。
      return {
        key: 'fixed_template_requires_review',
        label: '需人工复核',
        hint: cleanGateHint(
          reason,
          '固定模板存在风险, 必须人工核对招标原模板后再导出',
        ),
        severity: 'high',
      }
    }
    default: {
      return transformUnknown(name, entry)
    }
  }
}

/**
 * 未知 gate 的兜底转换:
 * - pass verdict 直接丢弃
 * - 类名风格 (*Gate) 剥离后缀作短 label, 视为 normal 严重度
 * - snake_case / 其他风格统一归到 "待复核" 中文短标, 严重度跟随后端 severity
 *
 * 目的: 防止后端任意新增 gate_name 时直接把英文技术标识符暴露给终端用户。
 */
function transformUnknown(name: string, entry: GateFlagEntry): GateBadge | null {
  if ((entry.verdict || '').toLowerCase() === 'pass') return null
  const reason = entry.reason || ''
  const severity: GateBadgeSeverity =
    (entry.severity || '').toLowerCase() === 'high' ? 'high' : 'normal'
  return {
    key: name,
    label: '待复核',
    hint: cleanGateHint(reason || entry.verdict || name, '存在需要人工复核的问题'),
    severity,
  }
}

/** 单章 gate_flags 是否有任何非 pass 的告警。*/
export function hasAnyGateWarning(
  flags: Record<string, GateFlagEntry> | null | undefined,
): boolean {
  return gateFlagsToBadges(flags).length > 0
}

/** 汇总内容编辑页全部章节 gate 告警，供顶栏全局入口展示。 */
export function summarizeGateWarnings(sources: GateWarningSource[]): GateWarningSummary {
  const items = sortSummaryItemsByImportance(sources
    .map((source) => ({
      nodeId: source.nodeId,
      title: source.title,
      badges: gateFlagsToBadges(source.gateFlags),
    }))
    .filter((item) => item.badges.length > 0))
  return {
    total: items.length,
    high: items.filter((item) => item.badges.some((badge) => badge.severity === 'high')).length,
    items,
  }
}
