/**
 * 标书编写 -- 评分项按卷满分核计横幅。
 *
 * 在约束确认页"写作引导" Tab 顶部展示后端权威评分项满分核计结果。
 * report 为空时不渲染；核计通过显示绿色通过态，待复核显示琥珀提示。
 * 校验口径与投标审查确认页 ScoringTotalsBanner 同源（复用 requirementReviewTransforms），
 * 展示口径同样对齐：只列有差额的卷（闭合卷不占版面）、诊断收进折叠区、
 * 标题已说"请核对"后不再重复同义泛化建议，只保留写标特有的失分后果。
 */
import React from 'react'
import { Button } from '@/components/ui/button'
import {
  buildScoringTotalsSummary,
  formatScore,
  volumeCheckHint,
  volumeLabel,
  type ScoringTotalsReport,
} from '../requirementReviewTransforms'
import { shouldShowAddScoringItem } from '../RequirementReview/scoringAddItemVisibility'
import { buildSuspectedAssessmentNotice } from '../RequirementReview/scoringBannerCopy'
import { ScoringBannerChrome } from '../RequirementReview/components/ScoringBannerChrome'

export interface WritingScoringTotalsBannerProps {
  report: ScoringTotalsReport | null
  onAddScoringItem?: () => void
}

const WritingScoringTotalsBanner: React.FC<WritingScoringTotalsBannerProps> = ({
  report,
  onAddScoringItem,
}) => {
  if (!report) return null

  // 只列有差额的卷：闭合卷渲染出来是"已识别X分/应为X分，正常"，对核对零信息量
  const problemVolumes = Object.entries(report.volumeChecks).filter(([, c]) => !c.within)
  const isOk = !report.needsReview
  // 有评分说明条目但全部无分值(item_count=0): 提示"未识别到有效评分项"而非"分值缺失"
  const noValued = !isOk && report.itemCount === 0
  const isLlmOnlyScoring = report.authorityCount === 0 && report.itemCount > 0
  const summary = buildScoringTotalsSummary(report)
  const shareText =
    summary.shares.length > 0
      ? `，其中 ${summary.shares
          .map((share) => `${share.label} ${formatScore(share.score)} 分`)
          .join('、')}`
      : ''
  // 多包文档按包展示各包合计（各包独立 100 分制），禁止相加成"合计 200 分"误导
  const okText =
    isLlmOnlyScoring
      ? `已识别 ${summary.itemCount} 个带分值评分项，合计 ${formatScore(
          summary.totalScore,
        )} 分${shareText}。后续目录与章节生成将据此响应评分点。`
      : summary.lotShares.length > 0
      ? `已从评分表提取 ${summary.itemCount} 个评分项并完成分值校验，${summary.lotShares
          .map((share) => `${share.label} 合计 ${formatScore(share.score)} 分`)
          .join('、')}。后续目录与章节生成将据此响应评分点。`
      : `已从评分表提取 ${summary.itemCount} 个评分项并完成分值校验，合计 ${formatScore(
          summary.totalScore,
        )} 分${shareText}。后续目录与章节生成将据此响应评分点。`
  const containerClass = isOk
    ? 'mb-4 px-4 py-2.5 bg-green-50 rounded-lg border border-green-200'
    : 'mb-4 px-4 py-2.5 bg-amber-50 rounded-lg border border-amber-300'
  const iconClass = isOk
    ? 'w-5 h-5 shrink-0 text-green-500'
    : 'w-5 h-5 shrink-0 text-amber-500'
  const titleClass = isOk
    ? 'text-sm font-medium text-green-900'
    : 'text-sm font-medium text-amber-900'
  const bodyClass = isOk ? 'text-sm text-green-800' : 'text-sm text-amber-800'
  const hintClass = isOk ? 'text-xs text-green-700' : 'text-xs text-amber-600'
  const addScoringItemVisible =
    Boolean(onAddScoringItem) &&
    shouldShowAddScoringItem({
      isOk,
      expectedTotal: Object.values(report.volumeChecks).length
        ? Object.values(report.volumeChecks).reduce((acc, c) => acc + c.expected, 0)
        : null,
      extractedTotal: summary.totalScore,
      absoluteExtractedTotal: summary.totalScore,
      missingScoreCount: report.missingScoreCount,
      volumeChecks: report.volumeChecks,
    })

  const hasCollapsibleDetails =
    !isOk || isLlmOnlyScoring || report.suspectedAssessmentCount > 0
  const actions = addScoringItemVisible ? (
    <Button
      size="sm"
      type="button"
      className="h-8 bg-amber-600 text-white shadow-xs hover:bg-amber-700"
      onClick={onAddScoringItem}
      title="手动新增一条评分项"
    >
      新增评分项
    </Button>
  ) : null

  return (
    <ScoringBannerChrome
      testId="writing-scoring-totals-banner"
      containerClass={containerClass}
      iconClass={iconClass}
      iconPath="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      hasDetails={hasCollapsibleDetails}
      attentionVisible={!isOk}
      headline={
        isOk ? (
          <p className={titleClass}>{okText}</p>
        ) : noValued ? (
          <p className={titleClass}>未识别到带分值的有效评分项，请核对招标文件评分表</p>
        ) : (
          <p className={titleClass}>评分项分值需核对，请确认识别结果</p>
        )
      }
      actions={actions}
      details={
        <>
          {noValued && (
            <p className={hintClass}>
              当前评分说明条目均无分值（多为评标办法总述、格式/保证金/报价等告知类），
              系统未提取到可核算的评分因素。若招标文件确有评分表，请核对是否被正确识别。
            </p>
          )}
          {!isOk && !noValued && (
            <>
              {problemVolumes.length > 0 && (
                <ul className={`mt-1 list-disc space-y-1 pl-5 ${bodyClass}`}>
                  {problemVolumes.map(([vol, c]) => (
                    <li key={vol}>
                      {volumeLabel(vol)}：已识别 {c.sum} 分 / 应为 {c.expected} 分
                      {volumeCheckHint(c)}
                    </li>
                  ))}
                </ul>
              )}
              {report.diagnostics.length > 0 && (
                <details className="mt-1">
                  <summary className={`cursor-pointer select-none ${hintClass} hover:underline`}>
                    分值核对明细
                  </summary>
                  <ul className={`mt-1 list-disc space-y-1 pl-5 text-xs ${hintClass}`}>
                    {report.diagnostics.map((item) => (
                      <li key={item} className="break-all">
                        {item}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <p className={hintClass}>
                分值异常会影响后续章节响应重点；需要时可使用“新增评分项”手动录入。
              </p>
            </>
          )}
          {isLlmOnlyScoring && (
            <p className={hintClass}>
              当前评分项来自 AI 自由提取，分值未经评分表校验，请对照招标文件评分表人工核对。
            </p>
          )}
          {report.suspectedAssessmentCount > 0 && (
            <p className={hintClass} title="疑似履约考核，建议排除">
              {buildSuspectedAssessmentNotice(
                report.suspectedAssessmentCount,
                report.suspectedAssessmentScore,
              )}
            </p>
          )}
        </>
      }
    />
  )
}

export default WritingScoringTotalsBanner
