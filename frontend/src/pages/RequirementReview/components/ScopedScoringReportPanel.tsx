/**
 * 采购包/标段维度评分对账面板（Task7）。
 *
 * 职责: 在多包模式（globalComparisonAllowed=false）下，按 scope 维度展示
 * 评分对账结果：amber 区列出有缺口的 scope，蓝色区提示适用关系待确认。
 * 不做: 不参与全局 ledger 对账逻辑、不修改 isOk 状态、不渲染行动按钮。
 *
 * 信息架构（与 ScoringTotalsBanner 的层次对应）：
 * - amber 区（anyApplicableGap）：仅列有真实缺口的 scope，逐行文案如
 * "采购包1：要求 100 分，已提取 90 分（少 10 分）"。
 * - 蓝色区（anyUnresolved）：精确提醒文案，不标业务通过，不诱导新增评分项。
 * basisLowConfidence scope 若已被 unresolved 蓝色区覆盖，不再单独重复。
 *
 * globalComparisonAllowed=true 时直接返回 null（全局模式，不介入）。
 *
 * SCOPE_SCORE_TOLERANCE 来自单源导出，禁止在此文件重复定义本地 SCORE_TOLERANCE。
 */
import React from 'react'
import {
  buildScopeReportView,
  formatScopeGapLine,
  isMisclassifiedApplicable,
  MULTI_SCHEME_UNRESOLVED_NOTICE,
  SCOPE_SCORE_TOLERANCE,
  type ScopedReport,
} from '../scoringScopeReportTransforms'

interface ScopedScoringReportPanelProps {
  scopes: ScopedReport[]
  /** false = 多包模式，本面板渲染 scope 级别报告；true = 全局模式，直接返回 null。 */
  globalComparisonAllowed: boolean
}

/**
 * 判断一个 scope 是否存在需列出的异常（applicable 真实缺口，或 误降级缺口）。
 */
function hasApplicableIssue(scope: ScopedReport): boolean {
  if (scope.applicability === 'applicable') {
    if (scope.totalGap != null && Math.abs(scope.totalGap) > SCOPE_SCORE_TOLERANCE) return true
    if (scope.insufficiencyCount > 0) return true
    if (scope.unlabeledSum > SCOPE_SCORE_TOLERANCE) return true
    return false
  }
  // 误降级 unresolved 且有真实 totalGap 同样列入 amber 明细
  return (
    isMisclassifiedApplicable(scope) &&
    scope.totalGap != null &&
    Math.abs(scope.totalGap) > SCOPE_SCORE_TOLERANCE
  )
}

export const ScopedScoringReportPanel: React.FC<ScopedScoringReportPanelProps> = ({
  scopes,
  globalComparisonAllowed,
}) => {
  if (globalComparisonAllowed || scopes.length === 0) return null

  const view = buildScopeReportView(scopes)

  // 全部 applicable scope 均通过且无 unresolved（含 basisLowConfidence）：无需额外展示
  if (view.allApplicableReconciled && !view.anyApplicableGap && !view.anyUnresolved) return null

  const gapScopes = view.scopes.filter(hasApplicableIssue)

  // 有多方案信号时使用 2502 精确文案，否则使用通用 unresolved 提醒
  const hasMultiScheme = view.scopes.some((s) => s.multiSchemeSignal !== '')
  const unresolvedNotice = view.anyUnresolved
    ? hasMultiScheme
      ? MULTI_SCHEME_UNRESOLVED_NOTICE
      : '部分采购包评分基准尚待结合招标公告原文确认，请核对后继续。'
    : null

  // basisLowConfidence 低置信提醒：
  // 仅统计 applicable scope（not_applicable 不触发提醒）。
  // 若 unresolved 蓝色区已存在（unresolvedNotice !== null），则不再单独追加，避免重复。
  // 若无 unresolved 但存在低置信 applicable scope，单独给出通用蓝色提醒。
  const hasBasisLowConfidence = view.scopes.some(
    (s) => s.applicability === 'applicable' && s.basisLowConfidence,
  )
  const basisLowConfidenceNotice =
    hasBasisLowConfidence && !unresolvedNotice
      ? '部分采购包评分总分基准未经招标文件原文交叉核对，请对照确认。'
      : null

  return (
    <div className="space-y-2">
      {(view.anyApplicableGap || view.anyMisclassifiedGap) && gapScopes.length > 0 && (
        <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm font-medium text-amber-900">
            {gapScopes.length === 1 ? '该采购包评分分值需核对' : '部分采购包评分分值需核对'}
          </p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-amber-800">
            {gapScopes.map((scope) => (
              <li key={scope.scopeId}>{formatScopeGapLine(scope)}</li>
            ))}
          </ul>
        </div>
      )}
      {unresolvedNotice && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-3 py-2.5">
          <p className="text-xs font-medium leading-relaxed text-blue-700">{unresolvedNotice}</p>
        </div>
      )}
      {basisLowConfidenceNotice && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-3 py-2.5">
          <p className="text-xs font-medium leading-relaxed text-blue-700">
            {basisLowConfidenceNotice}
          </p>
        </div>
      )}
    </div>
  )
}
