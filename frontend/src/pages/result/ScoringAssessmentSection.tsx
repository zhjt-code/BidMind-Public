/**
 * 结果页右栏"评分明细"区块。
 *
 * 职责: 对评分项在详情面板展示评分标准条目与 AI 逐档判定（rule_assessments），
 * 承接矩阵视图回退后评分细节的唯一展示位。
 * 不做: 不重算得分、不做筛选排序、非评分项或无评分细节时不渲染。
 */
import React from 'react'
import type { ReviewResult } from './types'
import type { RuleAssessment } from './scoringViewTransforms'
import {
  formatScoringRuleText,
  getScoringStatusLabel,
} from './scoringAssessmentDisplay.ts'

const STATUS_STYLES: Record<string, string> = {
  met: 'bg-green-50 text-green-700 border-green-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  not: 'bg-red-50 text-red-700 border-red-200',
  unknown: 'bg-gray-50 text-gray-600 border-gray-200',
}

function getStatusStyle(status: string | undefined): string {
  return STATUS_STYLES[status ?? ''] ?? STATUS_STYLES.unknown
}

interface ScoringAssessmentSectionProps {
  result: ReviewResult
}

/** 是否有可展示的评分细节（评分标准或逐档判定，二者皆无则整块不渲染）。 */
export function hasScoringDetail(result: ReviewResult): boolean {
  const isScoring = result.requirementKind === 'scoring_item' || Boolean(result.isKeyReview)
  if (!isScoring) return false
  const hasRules =
    (Array.isArray(result.scoreRules) && result.scoreRules.length > 0) ||
    (Array.isArray(result.deductionRules) && result.deductionRules.length > 0)
  const hasAssessments = Array.isArray(result.ruleAssessments) && result.ruleAssessments.length > 0
  return hasRules || hasAssessments
}

const ScoringAssessmentSection: React.FC<ScoringAssessmentSectionProps> = ({ result }) => {
  if (!hasScoringDetail(result)) return null

  const scoreRules = Array.isArray(result.scoreRules) ? result.scoreRules : []
  const deductionRules = Array.isArray(result.deductionRules) ? result.deductionRules : []
  const assessments: RuleAssessment[] = Array.isArray(result.ruleAssessments)
    ? (result.ruleAssessments as RuleAssessment[])
    : []

  return (
    <div>
      <div className="text-xs font-medium text-gray-500 mb-1.5">
        评分明细
        {result.scoreItemName && (
          <span className="ml-1.5 text-gray-400 font-normal">{result.scoreItemName}</span>
        )}
      </div>

      {scoreRules.length > 0 && (
        <div className="mb-2">
          <div className="text-[11px] text-gray-400 mb-1">评分标准</div>
          <ol className="list-decimal list-inside space-y-1 text-xs text-gray-700 bg-gray-50 rounded-md p-2">
            {scoreRules.map((rule, idx) => (
              <li key={`rule-${idx}-${formatScoringRuleText(rule).slice(0, 12)}`} className="leading-relaxed">
                {formatScoringRuleText(rule)}
              </li>
            ))}
          </ol>
        </div>
      )}

      {deductionRules.length > 0 && (
        <div className="mb-2">
          <div className="text-[11px] text-gray-400 mb-1">扣分标准</div>
          <ol className="list-decimal list-inside space-y-1 text-xs text-gray-700 bg-gray-50 rounded-md p-2">
            {deductionRules.map((rule, idx) => (
              <li key={`ded-${idx}-${formatScoringRuleText(rule).slice(0, 12)}`} className="leading-relaxed">
                {formatScoringRuleText(rule)}
              </li>
            ))}
          </ol>
        </div>
      )}

      {assessments.length > 0 && (
        <div>
          <div className="text-[11px] text-gray-400 mb-1">
            AI 逐档判定
            <span className="ml-1 text-gray-300">（预估仅供参考，不等于正式评标得分）</span>
          </div>
          <div className="space-y-1.5">
            {assessments.map((a, idx) => (
              <div
                key={`${a.rule_excerpt || 'rule'}-${a.status || 'unknown'}-${a.points ?? 'na'}-${idx}`}
                className={`rounded border px-2 py-1.5 text-xs ${getStatusStyle(a.status)}`}
              >
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <span className="font-medium whitespace-nowrap">{getScoringStatusLabel(a.status)}</span>
                  {a.points != null && (
                    <span className="font-bold whitespace-nowrap">{a.points} 分</span>
                  )}
                </div>
                {a.rule_excerpt && (
                  <div className="text-[10px] text-gray-500 mb-0.5">{a.rule_excerpt}</div>
                )}
                {a.reason && <div className="text-[11px] leading-relaxed">{a.reason}</div>}
                {a.evidence_pages && a.evidence_pages.length > 0 && (
                  <div className="text-[10px] text-blue-500 mt-0.5">
                    证据页: 第 {a.evidence_pages.join('、')} 页
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default ScoringAssessmentSection
