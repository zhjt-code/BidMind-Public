/**
 * 采购包/标段维度评分报告转换器（Task7）。
 *
 * 职责: 将后端 scope_reports 原始数组转换为前端可消费的 ScopedReport 列表，
 * 提供视图聚合与文案构建函数。
 * 不做: 不渲染 UI、不修改账本主报告字段、不推断评分总分默认值、
 * 不按偏离幅度猜测适用性。
 */

export type ScopeStatus = 'reconciled' | 'review_required'
export type ScopeApplicability = 'applicable' | 'unresolved' | 'not_applicable'

/** 单个采购包/标段的评分对账视图（与后端 ScopedReport 一一对应）。 */
export interface ScopedReport {
  /** 唯一标识，用作 React 列表 key；空字符串时该 scope 被丢弃。 */
  scopeId: string
  /** 评分模板标识（业务化展示可映射为"评分模板N"）。 */
  scheme: string
  /** 采购包/标段名称（如"采购包1"）。 */
  lot: string
  /** 对账状态：reconciled=通过，review_required=需复核。 */
  status: ScopeStatus
  /** 基准来源标识（对应后端 basis 枚举，不向用户裸出）。 */
  basis: string
  /** 招标文件要求总分；未找到时为 null。 */
  expectedTotal: number | null
  /** 已提取总分。 */
  extractedTotal: number
  /** 总分差额（extracted − expected）；null 表示不可比较。 */
  totalGap: number | null
  /** 该 scope 对当前文档的适用关系。 */
  applicability: ScopeApplicability
  /** 基准置信度低（未经原文交叉核对）。 */
  basisLowConfidence: boolean
  /** 内部诊断列表（不向用户裸出）。 */
  diagnostics: string[]
  /** 未归入任何评分卷的分值合计。 */
  unlabeledSum: number
  /** 缺少评审标准的评分项数量。 */
  insufficiencyCount: number
  /** 多方案信号（空串 = 无多方案）。 */
  multiSchemeSignal: string
}

/** 多 scope 聚合视图，供 ScopedScoringReportPanel 与 Banner 直接消费。 */
export interface ScopeReportView {
  scopes: ScopedReport[]
/**
 * 任何 applicable scope 存在真实缺口：
 * totalGap≠0 OR insufficiencyCount>0 OR unlabeledSum>0。
 */
  anyApplicableGap: boolean
/**
 * : 任何「误降级为 unresolved、但对账质量本应 applicable」的 scope
 * 存在真实 totalGap（|gap|>容差）。驱动 amber 暴露方案级缺口，不改 applicability。
 */
  anyMisclassifiedGap: boolean
/**
 * 任何 scope 适用关系为 unresolved（蓝色提醒，不标业务通过）。
 * : 软基准自洽 scope（isScopeSoftSelfConsistent）不计入——其唯一疑点
 * 是基准低置信，按绿色横幅内提醒呈现（anyScopeSoftBasisOnly）。
 */
  anyUnresolved: boolean
  /** : 存在被软自洽豁免的低置信/unresolved scope（绿色态下渲染基准提醒）。 */
  anyScopeSoftBasisOnly: boolean
/**
 * 所有 applicable scope 均对账通过。
 * 空集（无 applicable scope）为 false，避免"空集为真"误判。
 */
  allApplicableReconciled: boolean
}

/**
 * 2502 场景：已识别多套评分模板、适用关系待确认时的精确蓝色提醒文案。
 * 不显示全局合计，不诱导新增评分项，不出现内部术语。
 */
export const MULTI_SCHEME_UNRESOLVED_NOTICE =
  '已识别多套评分模板，结构已分别核对；适用关系需结合招标公告附件确认。'

const VALID_SCOPE_STATUSES = new Set<string>(['reconciled', 'review_required'])
const VALID_APPLICABILITIES = new Set<string>(['applicable', 'unresolved', 'not_applicable'])

/**
 * 评分分值比较容差（Panel / transform / add-helper 三者共用此单源导出，
 * 禁止在消费方重复定义本地 SCORE_TOLERANCE）。
 */
export const SCOPE_SCORE_TOLERANCE = 0.01

/**
 * 被前置分支误降级为 unresolved、但按对账质量本应 applicable 的 scope。
 *
 * 复刻后端 determine_applicability 正向条件：expected 非空 + 非低置信 +
 * 无不足项 + 未归卷≈0。满足却仍 unresolved ⟹ is_no_lot/is_unassigned 降级所致。
 * 不做: 不改 applicability、不推断适用关系。
 */
export function isMisclassifiedApplicable(scope: ScopedReport): boolean {
  return (
    scope.applicability === 'unresolved' &&
    scope.expectedTotal != null &&
    !scope.basisLowConfidence &&
    scope.insufficiencyCount === 0 &&
    scope.unlabeledSum <= SCOPE_SCORE_TOLERANCE
  )
}

/**
 * scope 级软基准自洽豁免（与单包 isLedgerSelfConsistentWithSoftBasis 同构）。
 *
 * 该 scope 分值完全自洽（基准非空且缺口/未归组≈0、无不足项、无多方案信号），
 * 唯一疑点仅剩"基准低置信/适用关系未确认"——按绿色横幅内提醒呈现，不驱动蓝色阻断。
 * 与后端 scoring_green_verdict._scope_soft_self_consistent 逐条件对齐。
 */
export function isScopeSoftSelfConsistent(scope: ScopedReport): boolean {
  return (
    scope.expectedTotal != null &&
    scope.totalGap != null &&
    Math.abs(scope.totalGap) <= SCOPE_SCORE_TOLERANCE &&
    scope.unlabeledSum <= SCOPE_SCORE_TOLERANCE &&
    scope.insufficiencyCount === 0 &&
    scope.multiSchemeSignal === ''
  )
}
/** 私有：分值格式化（对齐 requirementReviewTransforms.formatScore，不引入循环依赖）。 */
function fmt(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return String(Number(value.toFixed(4)))
}

function toFiniteOr<T>(v: unknown, fallback: T): number | T {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

/**
 * 将后端单个 scope_report 原始对象转换为 ScopedReport；
 * 以下情况返回 null（丢弃）：
 * - 非对象
 * - scope_id 非 string 或 trim 后为空（无法作为稳定 React key）
 * - status 不在枚举 {reconciled, review_required}
 * - applicability 不在枚举 {applicable, unresolved, not_applicable}
 */
export function transformScopedReportItem(raw: unknown): ScopedReport | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>

  const scopeId = typeof r.scope_id === 'string' ? r.scope_id.trim() : ''
  if (!scopeId) return null

  const status = r.status
  if (typeof status !== 'string' || !VALID_SCOPE_STATUSES.has(status)) return null

  const applicability = r.applicability
  if (typeof applicability !== 'string' || !VALID_APPLICABILITIES.has(applicability)) return null

  const diagnostics = Array.isArray(r.diagnostics)
    ? (r.diagnostics as unknown[]).filter((d): d is string => typeof d === 'string')
    : []

  return {
    scopeId,
    scheme: typeof r.scheme === 'string' ? r.scheme : '',
    lot: typeof r.lot === 'string' ? r.lot : '',
    status: status as ScopeStatus,
    basis: typeof r.basis === 'string' ? r.basis : '',
    expectedTotal: toFiniteOr(r.expected_total, null),
    extractedTotal: toFiniteOr(r.extracted_total, 0),
    totalGap: toFiniteOr(r.total_gap, null),
    applicability: applicability as ScopeApplicability,
    basisLowConfidence: Boolean(r.basis_low_confidence),
    diagnostics,
    unlabeledSum: toFiniteOr(r.unlabeled_sum, 0),
    insufficiencyCount: toFiniteOr(r.insufficiency_count, 0),
    multiSchemeSignal:
      typeof r.multi_scheme_signal === 'string' ? r.multi_scheme_signal.trim() : '',
  }
}

/**
 * 将后端 scope_reports 原始数组转换为 ScopedReport 列表；
 * 非数组或 null 时返回空数组，非法项静默丢弃（不中止）。
 *
 * 去重：同 scopeId 取首个出现的，后续重复项丢弃（防御后端重复推送）。
 * 排序：lot 优先（字典序），scheme 次之，scopeId 最后——保证列表顺序稳定可测试。
 */
export function transformScopedReportList(rawList: unknown): ScopedReport[] {
  if (!Array.isArray(rawList)) return []
  const parsed = rawList
    .map(transformScopedReportItem)
    .filter((s): s is ScopedReport => s !== null)

  // 稳定去重：同 scopeId 保留首个
  const seen = new Set<string>()
  const deduped = parsed.filter((s) => {
    if (seen.has(s.scopeId)) return false
    seen.add(s.scopeId)
    return true
  })

  // 稳定排序：lot → scheme → scopeId（中文字典序，指定 zh-Hans-CN locale 保证一致性）
  return deduped.sort((a, b) => {
    if (a.lot !== b.lot) return a.lot.localeCompare(b.lot, 'zh-Hans-CN')
    if (a.scheme !== b.scheme) return a.scheme.localeCompare(b.scheme, 'zh-Hans-CN')
    return a.scopeId.localeCompare(b.scopeId, 'zh-Hans-CN')
  })
}

/**
 * 从 ScopedReport 列表构建聚合视图。
 *
 * anyApplicableGap 仅统计 applicable scope，unresolved scope 的缺口不计入
 * （适用性未确定时无法断言缺口属实，不得以 amber 示警用户）。
 *
 * anyUnresolved 的防御性统计：
 * - applicability=unresolved（适用关系未确认）
 * - basisLowConfidence=true（总分基准未经原文交叉核对）
 * 两者均属"尚不能断言业务通过"，均驱动蓝色中性态，不驱动 amber。
 *
 * allApplicableReconciled 的严格条件：
 * applicable + status=reconciled + !basisLowConfidence
 * + unlabeledSum≈0 + insufficiencyCount=0 + (totalGap≈0 or totalGap=null)
 * 任一条件不满足即视为未完全通过，避免"有缺陷的 reconciled"误判为绿色。
 */
export function buildScopeReportView(scopes: ScopedReport[]): ScopeReportView {
  const applicable = scopes.filter((s) => s.applicability === 'applicable')
  const anyApplicableGap = applicable.some(
    (s) =>
      (s.totalGap != null && Math.abs(s.totalGap) > SCOPE_SCORE_TOLERANCE) ||
      s.insufficiencyCount > 0 ||
      s.unlabeledSum > SCOPE_SCORE_TOLERANCE,
  )
  // 误降级 unresolved 但仍有真实 totalGap 的方案级缺口
  const anyMisclassifiedGap = scopes.some(
    (s) =>
      isMisclassifiedApplicable(s) &&
      s.totalGap != null &&
      Math.abs(s.totalGap) > SCOPE_SCORE_TOLERANCE,
  )
  const uncertainScopes = scopes.filter(
    (s) => s.applicability === 'unresolved' || (s.applicability === 'applicable' && s.basisLowConfidence),
  )
  // 软基准自洽 scope 不驱动蓝色阻断，改为绿色态内的基准提醒
  const anyUnresolved = uncertainScopes.some((s) => !isScopeSoftSelfConsistent(s))
  const anyScopeSoftBasisOnly = uncertainScopes.some((s) => isScopeSoftSelfConsistent(s))
  const allApplicableReconciled =
    applicable.length > 0 &&
    applicable.every(
      (s) =>
        s.status === 'reconciled' &&
        !s.basisLowConfidence &&
        s.unlabeledSum <= SCOPE_SCORE_TOLERANCE &&
        s.insufficiencyCount === 0 &&
        (s.totalGap == null || Math.abs(s.totalGap) <= SCOPE_SCORE_TOLERANCE),
    )
  return {
    scopes,
    anyApplicableGap,
    anyMisclassifiedGap,
    anyUnresolved,
    anyScopeSoftBasisOnly,
    allApplicableReconciled,
  }
}

/**
 * 单 scope 异常行文案（amber 区块逐项列出时使用）：
 *
 * 有 gap： "采购包1：要求 100 分，已提取 90 分（少 10 分）"
 * 无 gap： "采购包1：3 项缺少评审标准" / "采购包1：5 分未能归入评分卷"
 * 无基准： "采购包1：已提取 85 分（未找到要求总分）"
 *
 * 标签优先顺序：lot > scheme > scopeId（均为空时兜底 scopeId）。
 * 禁止出现 scope/ledger/basis/scheme_signal 等内部术语。
 */
export function formatScopeGapLine(scope: ScopedReport): string {
  const label = scope.lot || scope.scheme || scope.scopeId

  if (scope.expectedTotal == null) {
    return `${label}：已提取 ${fmt(scope.extractedTotal)} 分（未找到要求总分）`
  }

  const gap = scope.totalGap
  if (gap != null && Math.abs(gap) > SCOPE_SCORE_TOLERANCE) {
    const direction =
      gap < 0
        ? `少 ${fmt(Math.abs(gap))} 分`
        : `多 ${fmt(gap)} 分`
    return `${label}：要求 ${fmt(scope.expectedTotal)} 分，已提取 ${fmt(scope.extractedTotal)} 分（${direction}）`
  }

  if (scope.insufficiencyCount > 0) {
    return `${label}：${scope.insufficiencyCount} 项缺少评审标准`
  }

  if (scope.unlabeledSum > SCOPE_SCORE_TOLERANCE) {
    return `${label}：${fmt(scope.unlabeledSum)} 分未能归入评分卷`
  }

  return `${label}：需人工确认`
}
