import React, { useState, useMemo } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { FileText } from 'lucide-react'
import { useAuthStore } from '@/stores'
import { documentService } from '@/services/documentApi'
import { FailureStateBadge } from '@/components/FailureStateBadge'
import SourcePageBadge from '@/components/SourcePageBadge'
import type { ReviewResult } from './types'
import {
  complianceLevelLabels, riskLevelLabels, getCategoryLabel,
  getComplianceBadgeVariant, getRiskColor, subjectScopeLabels,
  evidenceRelationLabels, isPerformanceCategory, shouldShowEvidenceTag,
  responseCompletenessLabels,
} from './types'
import { resolveResponseDisplay } from './responseDisplay'
import { groupSourcePageLocations } from './sourcePageGroups'
import { excerptsForPage } from './sourcePageExcerpts'
import {
  getComplianceDisplayLabel,
  isPendingJudgment,
  PENDING_JUDGMENT_BADGE_CLASS,
  PENDING_JUDGMENT_TOOLTIP,
} from './pendingJudgment'
import {
  getSubRequirementLabel, familyTypeLabels, attributionTypeLabels,
  consistencyFlagLabels, bidPartLabel,
} from '@/lib/constants'
import PdfPreviewDialog from './PdfPreviewDialog'
import ScoringAssessmentSection, { hasScoringDetail } from './ScoringAssessmentSection'

interface ResultDetailProps {
  result: ReviewResult | null
  debugView: boolean
  projectId: string
  bidDocumentId: number | null
  tenderDocumentId: number | null
  siblingResults?: ReviewResult[]
  onSelectSibling?: (id: number) => void
}

function parseSourcePages(raw: string): number[] {
  const pages: number[] = []
  for (const part of raw.split(/[,，]\s*/)) {
    const trimmed = part.trim()
    const rangeMatch = trimmed.match(/^(\d+)\s*[-–]\s*(\d+)$/)
    if (rangeMatch) {
      const start = parseInt(rangeMatch[1], 10)
      const end = parseInt(rangeMatch[2], 10)
      for (let i = start; i <= end; i++) pages.push(i)
    } else {
      const n = parseInt(trimmed, 10)
      if (!isNaN(n)) pages.push(n)
    }
  }
  return pages
}

const ResultDetail: React.FC<ResultDetailProps> = ({
  result,
  debugView,
  projectId,
  bidDocumentId,
  tenderDocumentId,
  siblingResults,
  onSelectSibling,
}) => {
  const [pdfOpen, setPdfOpen] = useState(false)
  const [pdfPage, setPdfPage] = useState(1)
  const [previewDocumentId, setPreviewDocumentId] = useState<number | null>(null)
  const [pdfExcerpts, setPdfExcerpts] = useState<string[]>([])

  const pdfUrl = useMemo(() => {
    if (!previewDocumentId) return ''
    return documentService.getPreviewUrl(parseInt(projectId), previewDocumentId)
  }, [projectId, previewDocumentId])

  const accessToken = useAuthStore(s => s.accessToken)

  const handlePageClick = (page: number, documentId: number, excerpts: string[] = []) => {
    setPreviewDocumentId(documentId)
    setPdfPage(page)
    setPdfExcerpts(excerpts)
    setPdfOpen(true)
  }

  if (!result) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 px-6">
        <FileText className="h-12 w-12 mb-3 opacity-40" />
        <p className="text-sm text-center">点击中间列表的审查项<br />查看完整详情</p>
      </div>
    )
  }

  const pageNumbers = (result.sourcePageNumbers && result.sourcePageNumbers.length > 0)
    ? result.sourcePageNumbers
    : (result.sourcePages ? parseSourcePages(result.sourcePages) : [])
  // 投标信封分卷定位(整本/历史数据为空数组, 回退 pageNumbers + bidDocumentId 路径)
  const pageLocations = result.sourcePageLocations || []
  const requirementPageNumbers = (result.requirementSourcePages && result.requirementSourcePages.length > 0)
    ? result.requirementSourcePages
    : (result.requirementSourcePage != null ? [result.requirementSourcePage] : [])
  const canPreviewResponse = bidDocumentId != null && pageNumbers.length > 0
  const canPreviewRequirement = tenderDocumentId != null && requirementPageNumbers.length > 0
  const requirementText = result.requirementDescription || result.requirementSummary
  const responseDisplay = resolveResponseDisplay(result)

  return (
    <>
      <ScrollArea className="h-full min-h-0">
        <div className="p-4 space-y-4">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge
                variant={isPendingJudgment(result) ? 'outline' : getComplianceBadgeVariant(result.complianceLevel)}
                className={isPendingJudgment(result) ? PENDING_JUDGMENT_BADGE_CLASS : undefined}
                title={isPendingJudgment(result) ? PENDING_JUDGMENT_TOOLTIP : undefined}
              >
                {getComplianceDisplayLabel(result, complianceLevelLabels)}
              </Badge>
              <Badge variant="outline">{getCategoryLabel(result.category)}</Badge>
              {/* 投标信封分卷: 证据来源卷徽标(商务标/技术标; 整本不显示) */}
              {(result.sourceParts || []).map((part) => (
                <Badge
                  key={part}
                  variant="outline"
                  className="text-gray-600"
                  title={`投标响应证据来自${bidPartLabel(part)}`}
                >
                  {bidPartLabel(part)}
                </Badge>
              ))}
              {(result.requirementKind === 'scoring_item' || result.isKeyReview) && (
                <Badge
                  variant="secondary"
                  className="bg-amber-100 text-amber-800 hover:bg-amber-100"
                  title="评分项：AI 预估得分仅供参考，不等于正式评标得分"
                >
                  评分项 · 重点
                </Badge>
              )}
              <FailureStateBadge state={result.failureState} />
              {result.consistencyFlag === 'cross_conflict' && (
                <Badge variant="destructive">跨类别冲突</Badge>
              )}
              {debugView && result.consistencyFlag === 'needs_review' && (
                <Badge variant="destructive">待复核</Badge>
              )}
              {debugView && result.consistencyFlag === 'conflicted' && (
                <Badge variant="destructive">存在冲突</Badge>
              )}
            </div>
            <div className="text-right flex-shrink-0">
              <div className="flex items-center justify-end gap-1.5">
                <span className="text-[10px] text-gray-400">符合度</span>
                <span
                  className="text-2xl font-bold text-blue-500 leading-none"
                  title="符合度评分（0-100），衡量响应质量，非评标得分"
                >
                  {result.score}
                </span>
                <span className={`text-xs ${getRiskColor(result.riskLevel)}`}>
                  {riskLevelLabels[result.riskLevel]}
                </span>
              </div>
              {(result.requirementKind === 'scoring_item' || result.isKeyReview) && result.maxScore != null && result.maxScore > 0 && (
                <div className="mt-1 text-right">
                  <div
                    className="text-xs font-medium text-amber-700"
                    title="AI 预估评标得分，仅供参考，不等于正式评标得分"
                  >
                    预估评标 {result.estimatedScore != null ? result.estimatedScore : '—'} / 满分 {result.maxScore}
                  </div>
                  <div className="mt-0.5 text-[10px] leading-snug text-amber-600">
                    预估仅供参考，不代表实际评标得分
                  </div>
                </div>
              )}
            </div>
          </div>

          <RelatedFactPanel
            current={result}
            siblings={siblingResults || []}
            onSelectSibling={onSelectSibling}
          />

          <Separator />

          <div>
            <div className="text-xs font-medium text-gray-500 mb-1.5">招标要求</div>
            <div className="text-sm text-gray-800 leading-relaxed">{requirementText}</div>
            {requirementPageNumbers.length > 0 && (
              <div className="mt-2 text-xs text-gray-500 flex items-center gap-1 flex-wrap">
                <span>招标来源页码:</span>
                {requirementPageNumbers.map((p, i) => (
                  <SourcePageBadge
                    key={`${p}-${i}`}
                    label={`P${p}`}
                    onClick={canPreviewRequirement && tenderDocumentId != null
                      ? () => handlePageClick(
                          p,
                          tenderDocumentId,
                          requirementText ? [requirementText] : [],
                        )
                      : undefined}
                    title={canPreviewRequirement
                      ? `打开招标文件第 ${p} 页`
                      : '招标文档未就绪，无法打开预览'}
                  />
                ))}
              </div>
            )}
            {result.requirementSubRequirements && result.requirementSubRequirements.length > 0 && (
              <div className="mt-2 text-xs text-gray-600">
                <span className="font-medium">子项:</span> {result.requirementSubRequirements.map(getSubRequirementLabel).join('；')}
              </div>
            )}
          </div>

          <Separator />

          <div>
            <div className="text-xs font-medium text-gray-500 mb-1.5 flex items-center gap-2 flex-wrap">
              <span>投标响应</span>
              {responseDisplay.statusLabel && (
                <Badge variant="outline" className="text-amber-800 border-amber-300 bg-amber-50">
                  {responseDisplay.statusLabel}
                </Badge>
              )}
            </div>
            {responseDisplay.hint && (
              <div className="text-xs text-amber-700 mb-1.5">{responseDisplay.hint}</div>
            )}
            <div className="text-sm text-gray-800 leading-relaxed">{responseDisplay.body}</div>
            {result.sourcePages && (
              <div className="mt-2 text-xs text-gray-500 space-y-1" data-guide="result-page-number">
                <div>来源页码:</div>
                {pageLocations.length > 0 ? (
                  // 按信封卷角色分行: 卷名一行、页码可点，同页号跨卷不串档
                  groupSourcePageLocations(pageLocations).map((group) => (
                    <div
                      key={group.partKey ?? 'integral'}
                      className="flex items-center gap-1 flex-wrap"
                    >
                      <span className="flex-shrink-0">{group.label}:</span>
                      {group.locations.map((location, i) => (
                        <SourcePageBadge
                          key={`${location.documentId}-${location.page}-${i}`}
                          label={`P${location.page}`}
                          onClick={() => handlePageClick(
                            location.page,
                            location.documentId,
                            excerptsForPage({
                              page: location.page,
                              documentId: location.documentId,
                              locations: pageLocations,
                              pageExcerpts: result.sourcePageExcerpts,
                            }),
                          )}
                          title={`打开${group.label}第 ${location.page} 页`}
                        />
                      ))}
                    </div>
                  ))
                ) : pageNumbers.length > 0 ? (
                  <div className="flex items-center gap-1 flex-wrap">
                    {pageNumbers.map((p, i) => (
                      <SourcePageBadge
                        key={`${p}-${i}`}
                        label={`P${p}`}
                        onClick={canPreviewResponse && bidDocumentId != null
                          ? () => handlePageClick(
                              p,
                              bidDocumentId,
                              excerptsForPage({
                                page: p,
                                documentId: bidDocumentId,
                                locations: pageLocations,
                                pageExcerpts: result.sourcePageExcerpts,
                              }),
                            )
                          : undefined}
                        title={canPreviewResponse
                          ? `打开投标文件第 ${p} 页`
                          : '投标文档未就绪，无法打开预览'}
                      />
                    ))}
                  </div>
                ) : (
                  <span className="text-primary">{result.sourcePages}</span>
                )}
              </div>
            )}
            {(result.responseCompleteness || responseDisplay.missingElements.length || result.responseEvidenceAnchor) && (
              <div className="mt-3 space-y-1 text-xs text-gray-600 bg-gray-50 rounded-md p-2">
                {result.responseCompleteness && (
                  <div>
                    响应完整性: {responseCompletenessLabels[result.responseCompleteness] || result.responseCompleteness}
                  </div>
                )}
                {result.responseEvidenceAnchor && (
                  <div>证据锚点: {result.responseEvidenceAnchor}</div>
                )}
                {result.responseLinkedTo != null && (
                  <div className="text-amber-700">
                    关联主响应: {result.responseLinkedTo}（AI 推测，需人工核实）
                  </div>
                )}
                {responseDisplay.missingElements.length > 0 && (
                  <div>缺失要素: {responseDisplay.missingElements.join('；')}</div>
                )}
              </div>
            )}
          </div>

          <Separator />

          <div>
            <div className="text-xs font-medium text-gray-500 mb-1.5">审查意见</div>
            <div className="text-sm text-gray-800 leading-relaxed bg-gray-50 rounded-md p-3">
              {result.comment}
            </div>
            {(result.consistencyFlag === 'cross_conflict' ||
              result.consistencyFlag === 'needs_review' ||
              result.consistencyFlag === 'conflicted') && (
              <div className="mt-2 text-xs text-amber-700 bg-amber-50 rounded-md p-2">
                复核原因: {result.reconcileNote || '证据关联不足，需人工确认'}
              </div>
            )}
          </div>

          {hasScoringDetail(result) && (
            <>
              <Separator />
              <ScoringAssessmentSection result={result} />
            </>
          )}

          {debugView && (
            <>
              <Separator />
              <div>
                <div className="text-xs font-medium text-gray-500 mb-2">技术字段 (Debug)</div>
                <div className="space-y-1.5">
                  {isPerformanceCategory(result.category) && (
                    <DebugField label="主体范围" value={subjectScopeLabels[result.subjectScope || 'generic'] || result.subjectScope} />
                  )}
                  {shouldShowEvidenceTag(result) && (
                    <DebugField label="证据关系" value={evidenceRelationLabels[result.evidenceRelation || ''] || result.evidenceRelation} />
                  )}
                  <DebugField label="归并键" value={result.canonicalKey} />
                  <DebugField label="事实键" value={result.factKey} />
                  <DebugField label="证据族" value={familyTypeLabels[result.familyType || ''] || result.familyType} />
                  <DebugField label="归因类型" value={attributionTypeLabels[result.attributionType || ''] || result.attributionType} />
                  <DebugField label="一致性标记" value={consistencyFlagLabels[result.consistencyFlag || ''] || result.consistencyFlag} />
                  {result.relevanceScore != null && (
                    <DebugField label="相关性分数" value={`${result.relevanceScore}`} />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </ScrollArea>

      {previewDocumentId != null && pdfUrl && (
        <PdfPreviewDialog
          open={pdfOpen}
          onOpenChange={setPdfOpen}
          pdfUrl={pdfUrl}
          initialPage={pdfPage}
          accessToken={accessToken}
          excerpts={pdfExcerpts}
        />
      )}
    </>
  )
}

const DebugField: React.FC<{ label: string; value?: string | null }> = ({ label, value }) => {
  if (!value) return null
  return (
    <div className="flex text-xs">
      <span className="text-gray-400 w-20 flex-shrink-0">{label}</span>
      <span className="text-gray-600 font-mono">{value}</span>
    </div>
  )
}

interface RelatedFactPanelProps {
  current: ReviewResult
  siblings: ReviewResult[]
  onSelectSibling?: (id: number) => void
}

const RelatedFactPanel: React.FC<RelatedFactPanelProps> = ({ current, siblings, onSelectSibling }) => {
  const sharedEvidence = current.evidenceRelation === 'shared'
  const linkedTo = current.responseLinkedTo
  if (siblings.length === 0 && !sharedEvidence && linkedTo == null) {
    return null
  }

  const total = siblings.length + 1

  return (
    <div className="rounded-md border border-gray-200 bg-gray-50/70 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-gray-700">
          {siblings.length > 0
            ? <>同源事实 <span className="text-gray-500">（共 {total} 条相关要求）</span></>
            : '关联提示'}
        </div>
      </div>

      {sharedEvidence && (
        <div className="text-xs text-blue-700 bg-blue-50 rounded px-2 py-1">
          该结论由 {total} 个要求共享同一份证据，可一次审定。
        </div>
      )}

      {linkedTo != null && (
        <div className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">
          AI 推测：本响应可能也覆盖了要求 #{linkedTo}，请人工核实
          {onSelectSibling && (
            <button
              type="button"
              className="ml-2 underline hover:text-amber-800"
              onClick={() => onSelectSibling(linkedTo)}
              title="AI 推测可能有误，请核实后再跳转"
            >
              查看该要求
            </button>
          )}
        </div>
      )}

      {siblings.length > 0 && (
        <div className="space-y-1">
          {siblings.map(s => (
            <button
              key={s.id}
              type="button"
              className="w-full text-left flex items-center gap-1.5 px-2 py-1.5 rounded hover:bg-white transition-colors"
              onClick={() => onSelectSibling?.(s.id)}
              title="跳转到该同源条目"
            >
              <Badge
                variant={isPendingJudgment(s) ? 'outline' : getComplianceBadgeVariant(s.complianceLevel)}
                className={`text-[10px] px-1.5 py-0 flex-shrink-0 ${
                  isPendingJudgment(s) ? PENDING_JUDGMENT_BADGE_CLASS : ''
                }`}
                title={isPendingJudgment(s) ? PENDING_JUDGMENT_TOOLTIP : undefined}
              >
                {getComplianceDisplayLabel(s, complianceLevelLabels)}
              </Badge>
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 flex-shrink-0">
                {getCategoryLabel(s.category)}
              </Badge>
              {s.consistencyFlag === 'cross_conflict' && (
                <Badge variant="destructive" className="text-[10px] px-1.5 py-0 flex-shrink-0">冲突</Badge>
              )}
              <span className="text-xs text-gray-700 truncate flex-1 min-w-0">
                {(s.requirementDescription || s.requirementSummary || '').slice(0, 60)}
              </span>
              <span className="text-[10px] text-gray-400 flex-shrink-0">{s.score}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default ResultDetail
