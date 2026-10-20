import React, { useMemo, useCallback } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { FileSearch } from 'lucide-react'
import RichTextContent from '@/components/RichTextContent'
import SourcePageBadge from '@/components/SourcePageBadge'
import { documentService } from '@/services/documentApi'
import { FailureStateBadge } from '@/components/FailureStateBadge'
import { normalizeLocationLabel, formatPageRangeLabel } from '@/services/sourcePageLabels'
import { sanitizeUserFacingText } from '@/services/progressStatusTransforms'
import type { CollusionPairResult, CollusionFinding, AttentionItem } from '@/services/collusionApi'
import {
  getRiskConfig, getEvidenceLevelInfo,
  DIMENSION_LABELS, DIMENSION_KEYS, CERT_KEY_MAP,
} from './types'
import { PAGE_ONLY_EVIDENCE_NOTICE, UNVERIFIABLE_FINDING_NOTICE } from './evidenceDisplay'
import { getFindingDimensionLabel, getCrossFieldRiskPatternLabel, getSourceFieldLabel } from '@/lib/constants'
import { buildPairOverviewMessages } from './pairOverviewMessages'
import PdfEvidencePreviewHost from '../result/pdfPreview/PdfEvidencePreviewHost'
import { usePdfEvidencePreview } from '../result/pdfPreview/usePdfEvidencePreview'

interface AttentionCategoryStyle {
  label: string
  groupTitle: string
  containerClass: string
  badgeClass: string
  titleClass: string
}

const ATTENTION_CATEGORY_STYLES: Record<string, AttentionCategoryStyle> = {
  negative_finding: {
    label: '分析观察',
    groupTitle: '分析观察（已确认无串标线索）',
    containerClass: 'bg-gray-50 border-gray-200',
    badgeClass: 'border-gray-300 text-gray-600',
    titleClass: 'text-gray-700',
  },
  // C2: 推测性观察 — LLM 给出"假设/疑似/可能/例如"等推测语，
  // 缺乏可复核实证，明确标识为"非实证仅供参考"，与正式证据视觉区隔
  speculative_finding: {
    label: '推测性观察',
    groupTitle: '推测性观察（无具体证据，仅供参考）',
    containerClass: 'bg-amber-50/70 border-amber-200',
    badgeClass: 'border-amber-300 text-amber-700',
    titleClass: 'text-amber-900',
  },
  business_relationship: {
    label: '业务往来',
    groupTitle: '业务往来（不构成串标，但存在联络渠道）',
    containerClass: 'bg-sky-50 border-sky-200',
    badgeClass: 'border-sky-300 text-sky-700',
    titleClass: 'text-sky-900',
  },
  unverified_quote: {
    label: '待核实引文',
    groupTitle: '待核实引文（页码或位置无法验证）',
    containerClass: 'bg-amber-50 border-amber-200',
    badgeClass: 'border-amber-300 text-amber-700',
    titleClass: 'text-amber-900',
  },
}

const ATTENTION_CATEGORY_ORDER = [
  'negative_finding',
  'speculative_finding',
  'business_relationship',
  'unverified_quote',
]

const DEFAULT_ATTENTION_STYLE: AttentionCategoryStyle = {
  label: '其他关注项',
  groupTitle: '其他关注项',
  containerClass: 'bg-gray-50 border-gray-200',
  badgeClass: 'border-gray-300 text-gray-600',
  titleClass: 'text-gray-700',
}

const AttentionItemsPanel: React.FC<{ items: AttentionItem[] }> = ({ items }) => {
  const grouped = useMemo(() => {
    const buckets = new Map<string, AttentionItem[]>()
    for (const ai of items) {
      const key = ai.category || 'other'
      const list = buckets.get(key)
      if (list) list.push(ai)
      else buckets.set(key, [ai])
    }
    const ordered: Array<{ category: string; items: AttentionItem[] }> = []
    for (const cat of ATTENTION_CATEGORY_ORDER) {
      const arr = buckets.get(cat)
      if (arr && arr.length > 0) {
        ordered.push({ category: cat, items: arr })
        buckets.delete(cat)
      }
    }
    for (const [cat, arr] of buckets) {
      ordered.push({ category: cat, items: arr })
    }
    return ordered
  }, [items])

  return (
    <div className="mt-3 border-t border-gray-100 pt-3 space-y-3">
      {grouped.map(group => {
        const style = ATTENTION_CATEGORY_STYLES[group.category] || DEFAULT_ATTENTION_STYLE
        return (
          <div key={`attn-group-${group.category}`}>
            <p className={`text-xs font-medium mb-1.5 ${style.titleClass}`}>
              {style.groupTitle}
            </p>
            <div className="space-y-2">
              {group.items.map((ai, i) => (
                <div
                  key={`attn-${group.category}-${i}`}
                  className={`text-xs border rounded px-2 py-1.5 ${style.containerClass}`}
                >
                  <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                    <Badge variant="outline" className={`text-[10px] px-1 py-0 ${style.badgeClass}`}>
                      {style.label}
                    </Badge>
                    {ai.dimension && ai.dimension !== 'unknown' && (
                      <Badge variant="outline" className="text-[10px] px-1 py-0">
                        {getFindingDimensionLabel(ai.dimension)}
                      </Badge>
                    )}
                    <span className={`font-medium ${style.titleClass}`}>{ai.summary}</span>
                  </div>
                  {ai.detail && (
                    <p className="text-gray-600 leading-relaxed">{ai.detail}</p>
                  )}
                  {ai.sourceReason && (
                    <p className="text-gray-400 text-[11px] mt-1">来源：{ai.sourceReason}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// 重合片段面板：展示向量预筛选的高相似 chunk 对，把"到底哪几段重合"显式呈现，
// 并对疑似通用模板/白名单命中的片段做弱化标注，供人工甄别（解决"只给结论不给依据"）。
const EvidenceChunksPanel: React.FC<{
  chunks: NonNullable<CollusionPairResult['evidenceChunks']>
  docAName: string
  docBName: string
  docAId: number
  docBId: number
  onOpenPage: (docId: number, page: number | null | undefined, excerpt?: string) => void
}> = ({ chunks, docAName, docBName, docAId, docBId, onOpenPage }) => {
  if (!chunks || chunks.length === 0) return null
  return (
    <div>
      <div className="text-xs font-medium text-gray-500 mb-2">
        高相似重合片段（{chunks.length} 段，供人工甄别）
      </div>
      <p className="text-[11px] text-gray-400 mb-2 leading-relaxed">
        以下为向量预筛选出的相似片段。标注"疑似通用模板"的片段，其重合更可能源于
        招标模板、法规或行业通用章节，非串通证据，请结合上下文判读。
      </p>
      <div className="space-y-2">
        {chunks.map((ec, i) => {
          const isTemplate = ec.boilerplateFlag || ec.kbRuleMatch
          return (
            <div
              key={`ec-${i}`}
              className={`text-xs border rounded p-2 ${
                isTemplate ? 'bg-gray-50 border-gray-200' : 'bg-orange-50/50 border-orange-200'
              }`}
            >
              <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
                {isTemplate ? (
                  <Badge variant="outline" className="text-[10px] px-1 py-0 border-gray-300 text-gray-500">
                    疑似通用模板
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-[10px] px-1 py-0 border-orange-300 text-orange-600">
                    高相似
                  </Badge>
                )}
                {typeof ec.score === 'number' && (
                  <span className="text-[10px] text-gray-400">语义 {Math.round(ec.score * 100)}%</span>
                )}
                {typeof ec.literalScore === 'number' && (
                  <span className="text-[10px] text-gray-400">字面 {Math.round(ec.literalScore * 100)}%</span>
                )}
              </div>
              <div className="space-y-1.5">
                <div className="border-l-2 border-blue-300 pl-2">
                  <p className="text-[10px] text-blue-600 font-medium">
                    {docAName}
                    {ec.pageA != null && ec.pageA > 0 && (
                      <>
                        {' · '}
                        <SourcePageBadge
                          label={formatPageRangeLabel(ec.pageA)}
                          onClick={() => onOpenPage(docAId, ec.pageA, ec.textA)}
                          title={`打开 ${docAName} 第 ${ec.pageA} 页`}
                          className="!text-[10px]"
                        />
                      </>
                    )}
                  </p>
                  <p className="text-gray-700 italic leading-relaxed">{ec.textA || '（无文本）'}</p>
                </div>
                <div className="border-l-2 border-amber-300 pl-2">
                  <p className="text-[10px] text-amber-600 font-medium">
                    {docBName}
                    {ec.pageB != null && ec.pageB > 0 && (
                      <>
                        {' · '}
                        <SourcePageBadge
                          label={formatPageRangeLabel(ec.pageB)}
                          onClick={() => onOpenPage(docBId, ec.pageB, ec.textB)}
                          title={`打开 ${docBName} 第 ${ec.pageB} 页`}
                          className="!text-[10px]"
                        />
                      </>
                    )}
                  </p>
                  <p className="text-gray-700 italic leading-relaxed">{ec.textB || '（无文本）'}</p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const SEMANTIC_NULLS = new Set(['无', 'null', 'none', 'n/a', 'na', '不适用', '未找到', '无相关内容', '未提取到', '/', '-', '--', '——'])

function isValidQuote(quote: string | undefined | null): boolean {
  if (!quote) return false
  const trimmed = quote.trim().toLowerCase()
  return trimmed.length > 1 && !SEMANTIC_NULLS.has(trimmed)
}

const EVIDENCE_COLORS = [
  { bg: 'bg-blue-50', border: 'border-blue-300', text: 'text-blue-600' },
  { bg: 'bg-amber-50', border: 'border-amber-300', text: 'text-amber-600' },
  { bg: 'bg-green-50', border: 'border-green-300', text: 'text-green-600' },
  { bg: 'bg-purple-50', border: 'border-purple-300', text: 'text-purple-600' },
  { bg: 'bg-rose-50', border: 'border-rose-300', text: 'text-rose-600' },
  { bg: 'bg-teal-50', border: 'border-teal-300', text: 'text-teal-600' },
  { bg: 'bg-indigo-50', border: 'border-indigo-300', text: 'text-indigo-600' },
  { bg: 'bg-orange-50', border: 'border-orange-300', text: 'text-orange-600' },
]

/**
 * PDF 页码预览 hook: 统一 FindingDetail 与 PairOverview 的"点击页码 → 打开 PDF 指定页"链路。
 * 职责: 维护预览弹窗状态并暴露 openPage(docId, page, excerpt), 返回可直接渲染的 dialog 节点。
 * 不做: 页码字符串解析(由调用方用 normalizeLocationLabel/formatPageRangeLabel 决定页号)。
 */
function usePdfPagePreview(projectId: string): {
  openPage: (docId: number, page: number | null | undefined, excerpt?: string) => void
  dialog: React.ReactNode
} {
  const preview = usePdfEvidencePreview()
  const { openPreview } = preview
  const dialog = <PdfEvidencePreviewHost {...preview.hostProps} />

  const openPage = useCallback((docId: number, page: number | null | undefined, excerpt?: string) => {
    if (!page || page <= 0 || !projectId) return
    const trimmed = (excerpt || '').trim()
    openPreview({
      pdfUrl: documentService.getPreviewUrl(parseInt(projectId), docId),
      page,
      excerpts: trimmed ? [trimmed] : [],
    })
  }, [openPreview, projectId])

  return { openPage, dialog }
}

interface PairDetailProps {
  pair: CollusionPairResult | null
  finding: CollusionFinding | null
  replaceRefs: (text: string | undefined) => string
  projectId: string
}

const PairDetail: React.FC<PairDetailProps> = ({ pair, finding, replaceRefs, projectId }) => {
  if (!pair) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 px-6">
        <FileSearch className="h-12 w-12 mb-3 opacity-40" />
        <p className="text-sm text-center">选择左侧对比对<br />查看分析详情</p>
      </div>
    )
  }

  if (finding) {
    return <FindingDetail pair={pair} finding={finding} replaceRefs={replaceRefs} projectId={projectId} />
  }

  return <PairOverview pair={pair} projectId={projectId} />
}

// ==================== 对比对概览（选中对但未选证据） ====================

const PairOverview: React.FC<{
  pair: CollusionPairResult
  projectId: string
}> = ({ pair, projectId }) => {
  const { openPage, dialog } = usePdfPagePreview(projectId)
  const risk = getRiskConfig(pair.riskLevel || pair.overallRisk)
  const overviewMessages = buildPairOverviewMessages(pair)
  const cleanedQualityFlags = pair.qualityFlags
    .map(sanitizeUserFacingText)
    .filter(Boolean)
  const cleanedConsistencyFlags = (pair.consistencyFlags || [])
    .map(sanitizeUserFacingText)
    .filter(Boolean)
  const cleanedRiskExplanation = sanitizeUserFacingText(pair.riskExplanation)

  const certs = pair.dimensionCertainties || {}
  const allDimensions = DIMENSION_KEYS.map(key => ({
    key,
    label: DIMENSION_LABELS[key],
    score: pair[key as keyof CollusionPairResult] as number | undefined | null,
    certainty: (certs[CERT_KEY_MAP[key]] ?? null) as number | null,
  }))

  return (
    <>
    <ScrollArea className="h-full min-h-0">
      <div className="p-4 space-y-4">
        <div>
          <div className="flex items-center gap-2 mb-2">
            {pair.decisionState === 'decidable' && (
              <Badge
                variant={risk.label === '高风险' ? 'destructive' : risk.label === '无风险' ? 'success' : 'warning'}
              >
                {risk.label}
              </Badge>
            )}
            <FailureStateBadge state={pair.failureState} />
            {pair.decisionState === 'needs_review' && (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-700">需人工复核</Badge>
            )}
            {pair.decisionState === 'skipped' && (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-700">待补充分析</Badge>
            )}
            {pair.reviewState === 'unreviewed' && (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-700">未进入深度分析</Badge>
            )}
            {(pair.decisionState === 'failed' || pair.reviewState === 'failed') && (
              <Badge variant="outline" className="text-[10px] border-red-300 text-red-700">分析失败</Badge>
            )}
            {pair.analysisMode === 'algorithm_only' && (
              <Badge variant="outline" className="text-[10px]">低风险快检</Badge>
            )}
            {pair.analysisMode?.startsWith('simplified') && (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-600">简化分析</Badge>
            )}
            {pair.analysisMode?.includes('degraded') && (
              <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-600">降级分析</Badge>
            )}
            {pair.analysisMode?.includes('recovered') && (
              <Badge variant="outline" className="text-[10px] border-blue-300 text-blue-600">补充分析</Badge>
            )}
          </div>
          <div className="text-sm font-medium text-gray-800">
            {pair.docAName} × {pair.docBName}
          </div>
          {pair.findings.length > 0 && (
            <div className="text-xs text-gray-500 mt-1">
              共 {pair.findings.length} 条证据 -- 点击中栏证据查看详情
            </div>
          )}
        </div>

        <Separator />

        <div>
          <div className="text-xs font-medium text-gray-500 mb-2">各维度得分</div>
          <div className="space-y-1.5">
            {allDimensions.map(({ key, label, score, certainty }) => {
              const analyzed = score !== undefined && score !== null
              const hasScore = analyzed && score > 0
              const isBoilerplate = hasScore && certainty !== null && certainty < 0.3
              // 条形：有得分用串标身份色；通用模板用浅底，与"(通用)"后缀语义对齐
              const barColor = isBoilerplate ? 'bg-[#E3D5F5]' : 'bg-[#9056D8]'
              // 文字：统一雅黑(text-gray-700)消除原有三档灰阶；
              // 仅"通用模板"得分文字弱化为 gray-400 以保留"该相似度可能来自模板而非串通"的视觉提示
              const labelClass = 'text-xs w-24 flex-shrink-0 text-gray-700'
              const scoreClass = `text-xs text-right ${
                hasScore && !isBoilerplate ? 'font-medium text-gray-700'
                  : isBoilerplate ? 'text-gray-400'
                  : 'text-gray-700'
              }`
              // "--"= 后端在分级LLM调用策略下未分析该维度（如低相似度 pair 仅算法跑 D1/D2/D8）
              // 与"0"(已分析为零)语义不同，不可合并；以 title 悬浮提示让用户区分
              const notAnalyzedTitle = '该维度未参与本次分析（系统判定无需深入分析）'
              const rowTitle = !analyzed ? notAnalyzedTitle : undefined
              return (
                <div key={key} className="flex items-center gap-2" title={rowTitle}>
                  <span className={labelClass}>{label}</span>
                  <div className="flex-1 bg-gray-200 rounded-full h-2">
                    {hasScore && (
                      <div
                        className={`h-2 rounded-full ${barColor} transition-all`}
                        style={{ width: `${Math.min(score! * 100, 100)}%` }}
                      />
                    )}
                  </div>
                  <span className={scoreClass}>
                    {hasScore ? `${Math.round(score! * 100)}${isBoilerplate ? '(通用)' : ''}` : analyzed ? '0' : '--'}
                  </span>
                </div>
              )
            })}
          </div>
          {overviewMessages.length > 0 && (
            <div className="mt-2 space-y-1">
              {overviewMessages.map((message, i) => {
                const className = message.tone === 'danger'
                  ? 'text-xs text-red-700 bg-red-50 px-2 py-1 rounded'
                  : message.tone === 'warning'
                    ? 'text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded'
                    : message.tone === 'info'
                      ? 'text-xs text-blue-700 bg-blue-50 px-2 py-1 rounded'
                      : 'text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded'
                return (
                  <p key={`overview-msg-${i}`} className={className}>{message.text}</p>
                )
              })}
            </div>
          )}
          {pair.decisionState === 'decidable' && (
            <>
              {cleanedQualityFlags.length > 0 && (
                <div className="mt-2 space-y-1">
                  {cleanedQualityFlags.map((flag, i) => (
                    <p key={`qf-${i}`} className="text-xs text-gray-600 bg-gray-50 px-2 py-1 rounded">{flag}</p>
                  ))}
                </div>
              )}
              {cleanedConsistencyFlags.length > 0 && (
                <div className="mt-2 space-y-1">
                  {cleanedConsistencyFlags.map((flag, i) => (
                    <p key={`cf-${i}`} className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded">{flag}</p>
                  ))}
                </div>
              )}
              {cleanedRiskExplanation && (
                <RichTextContent text={cleanedRiskExplanation} className="text-xs text-gray-500 mt-2 leading-relaxed" variant="compact" />
              )}
              {/* P2-3: 关注项（后端 attention_items）— 被门控降级但仍值得人工关注 */}
              {pair.attentionItems && pair.attentionItems.length > 0 && (
                <AttentionItemsPanel items={pair.attentionItems} />
              )}
            </>
          )}

        </div>

        {/* 重合片段：无论 decision_state 如何都展示（needs_review/无证据对子尤其需要），
            让"白名单命中/疑似模板"的结论有据可查 */}
        {pair.evidenceChunks && pair.evidenceChunks.length > 0 && (
          <>
            <Separator />
            <EvidenceChunksPanel
              chunks={pair.evidenceChunks}
              docAName={pair.docAName}
              docBName={pair.docBName}
              docAId={pair.docAId}
              docBId={pair.docBId}
              onOpenPage={openPage}
            />
          </>
        )}
      </div>
    </ScrollArea>
    {dialog}
    </>
  )
}

// ==================== 证据详情 ====================

interface FindingDetailProps {
  pair: CollusionPairResult
  finding: CollusionFinding
  replaceRefs: (text: string | undefined) => string
  projectId: string
}

const FindingDetail: React.FC<FindingDetailProps> = ({ pair, finding, replaceRefs, projectId }) => {
  const { openPage, dialog } = usePdfPagePreview(projectId)

  const handlePageClick = (docId: number, pageStr: string, excerpt?: string) => {
    openPage(docId, normalizeLocationLabel(pageStr).page, excerpt)
  }

  const levelInfo = getEvidenceLevelInfo(finding.level)
  const descText = replaceRefs(finding.description)
  const detailText = replaceRefs(finding.detail)
  const evidenceText = replaceRefs(finding.evidence)
  const showCertainty = finding.certainty !== undefined
    && finding.certainty !== null
    && finding.certainty > 0.05
  const hasEvidenceRefs = finding.evidences && finding.evidences.some(e => isValidQuote(e.quote) || e.location)
  const hasQuotes = isValidQuote(finding.quoteA) || isValidQuote(finding.quoteB)
  const hasPages = finding.pageA || finding.pageB

  const isCrossField = !!finding.riskPattern

  return (
    <>
    <ScrollArea className="h-full min-h-0">
      <div className="p-4 space-y-4">
        {/* 标签 */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`px-2 py-0.5 text-xs rounded-md font-medium ${levelInfo.color}`} title={levelInfo.tooltip}>
            {levelInfo.label}
          </span>
          {finding.dimension && finding.dimension !== 'unknown' && (
            <Badge variant="outline" className="text-[10px]">{getFindingDimensionLabel(finding.dimension)}</Badge>
          )}
          {isCrossField && (
            <Badge variant="outline" className="text-[10px] border-purple-400 bg-purple-50 text-purple-700" title="跨字段串联命中：标识符跨主体跨字段一致">
              跨字段串联
            </Badge>
          )}
          {finding.similarity !== undefined && finding.similarity !== null && (
            <span className="text-xs text-gray-400">
              相似度 {(finding.similarity * 100).toFixed(0)}%
            </span>
          )}
          {showCertainty && (
            <span className="text-xs text-gray-400">
              确定性 {Math.round(finding.certainty! * 100)}%
            </span>
          )}
        </div>

        {/* 跨字段命中详情卡 */}
        {isCrossField && (
          <div className="rounded-md border border-purple-300 bg-purple-50 p-3 space-y-1.5">
            <div className="flex items-center gap-2 text-xs font-medium text-purple-900">
              <span>跨字段命中规则</span>
              <Badge variant="outline" className="text-[10px] border-purple-300 bg-white text-purple-700">
                {getCrossFieldRiskPatternLabel(finding.riskPattern!)}
              </Badge>
            </div>
            {(finding.sourceFieldA || finding.sourceFieldB) && (
              <p className="text-[11px] text-purple-800 leading-relaxed">
                <span className="font-medium">字段对应：</span>
                {finding.sourceFieldA ? getSourceFieldLabel(finding.sourceFieldA) : '-'}
                <span className="mx-1.5 text-purple-400">↔</span>
                {finding.sourceFieldB ? getSourceFieldLabel(finding.sourceFieldB) : '-'}
              </p>
            )}
            {finding.legalReference && (
              <p className="text-[11px] text-purple-800 leading-relaxed">
                <span className="font-medium">法规对照：</span>
                {finding.legalReference}
              </p>
            )}
          </div>
        )}

        <Separator />

        {/* 描述 */}
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1.5">发现描述</div>
          <RichTextContent text={descText} className="text-gray-800 leading-relaxed" />
        </div>

        {/* 详细分析 */}
        {detailText && detailText !== descText && (
          <>
            <Separator />
            <div>
              <div className="text-xs font-medium text-gray-500 mb-1.5">详细分析</div>
              <div className="bg-gray-50 rounded-md p-3">
                <RichTextContent text={detailText} className="text-gray-700 leading-relaxed" />
              </div>
            </div>
          </>
        )}

        {/* N 文档引用块（evidences 优先，回退到 quoteA/B） */}
        {hasEvidenceRefs ? (
          <>
            <Separator />
            <div>
              <div className="text-xs font-medium text-gray-500 mb-2">
                原文引用 ({finding.evidences!.filter(e => isValidQuote(e.quote) || e.location).length} 份文档)
              </div>
              <div className="space-y-2">
                {finding.evidences!.filter(e => isValidQuote(e.quote) || e.location).map((ev, evIdx) => {
                  const color = EVIDENCE_COLORS[evIdx % EVIDENCE_COLORS.length]
                  const locInfo = normalizeLocationLabel(ev.location)
                  return (
                    <div key={`ev-${evIdx}`} className={`p-3 ${color.bg} rounded-lg border-l-2 ${color.border}`}>
                      <p className={`text-xs ${color.text} font-medium mb-1`}>
                        {ev.docName || `文档${ev.docId || ''}`}
                        {locInfo.label && (
                          <>
                            {' - '}
                            <SourcePageBadge
                              label={locInfo.label}
                              onClick={ev.docId && locInfo.page
                                ? () => handlePageClick(ev.docId!, ev.location!, ev.quote)
                                : undefined}
                              title={ev.docId && locInfo.page
                                ? `打开该文档第 ${locInfo.page} 页`
                                : '该证据未提供可跳转的页码'}
                            />
                          </>
                        )}
                      </p>
                      {isValidQuote(ev.quote) && (
                        <p className="text-xs text-gray-700 italic leading-relaxed">"{ev.quote}"</p>
                      )}
                      {!isValidQuote(ev.quote) && ev.location && (
                        <p className="text-[11px] text-gray-500 leading-relaxed">
                          {PAGE_ONLY_EVIDENCE_NOTICE}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </>
        ) : hasQuotes ? (
          <>
            <Separator />
            <div>
              <div className="text-xs font-medium text-gray-500 mb-2">原文对比</div>
              <div className="space-y-2">
                {(isValidQuote(finding.quoteA) || finding.pageA) && (() => {
                  const infoA = normalizeLocationLabel(finding.pageA)
                  return (
                    <div className="p-3 bg-blue-50 rounded-lg border-l-2 border-blue-300">
                      <p className="text-xs text-blue-600 font-medium mb-1">
                        {pair.docAName}
                        {infoA.label && (
                          <>
                            {' - '}
                            <SourcePageBadge
                              label={infoA.label}
                              onClick={infoA.page
                                ? () => handlePageClick(pair.docAId, finding.pageA!, finding.quoteA)
                                : undefined}
                              title={infoA.page
                                ? `打开 ${pair.docAName} 第 ${infoA.page} 页`
                                : '该证据未提供可跳转的页码'}
                            />
                          </>
                        )}
                      </p>
                      {isValidQuote(finding.quoteA) && (
                        <p className="text-xs text-gray-700 italic leading-relaxed">"{finding.quoteA}"</p>
                      )}
                    </div>
                  )
                })()}
                {(isValidQuote(finding.quoteB) || finding.pageB) && (() => {
                  const infoB = normalizeLocationLabel(finding.pageB)
                  return (
                    <div className="p-3 bg-amber-50 rounded-lg border-l-2 border-amber-300">
                      <p className="text-xs text-amber-600 font-medium mb-1">
                        {pair.docBName}
                        {infoB.label && (
                          <>
                            {' - '}
                            <SourcePageBadge
                              label={infoB.label}
                              onClick={infoB.page
                                ? () => handlePageClick(pair.docBId, finding.pageB!, finding.quoteB)
                                : undefined}
                              title={infoB.page
                                ? `打开 ${pair.docBName} 第 ${infoB.page} 页`
                                : '该证据未提供可跳转的页码'}
                            />
                          </>
                        )}
                      </p>
                      {isValidQuote(finding.quoteB) && (
                        <p className="text-xs text-gray-700 italic leading-relaxed">"{finding.quoteB}"</p>
                      )}
                    </div>
                  )
                })()}
              </div>
            </div>
          </>
        ) : hasPages ? (() => {
          const infoA = normalizeLocationLabel(finding.pageA)
          const infoB = normalizeLocationLabel(finding.pageB)
          return (
            <>
              <Separator />
              <div className="flex items-center gap-3 text-xs text-gray-500" data-guide="collusion-page-number">
                {infoA.label && (
                  <span className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
                    {pair.docAName}:
                    <SourcePageBadge
                      label={infoA.label}
                      onClick={infoA.page ? () => handlePageClick(pair.docAId, finding.pageA!, finding.quoteA) : undefined}
                      title={infoA.page ? `打开 ${pair.docAName} 第 ${infoA.page} 页` : '该证据未提供可跳转的页码'}
                    />
                  </span>
                )}
                {infoB.label && (
                  <span className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
                    {pair.docBName}:
                    <SourcePageBadge
                      label={infoB.label}
                      onClick={infoB.page ? () => handlePageClick(pair.docBId, finding.pageB!, finding.quoteB) : undefined}
                      title={infoB.page ? `打开 ${pair.docBName} 第 ${infoB.page} 页` : '该证据未提供可跳转的页码'}
                    />
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11px] text-gray-500 leading-relaxed">
                {PAGE_ONLY_EVIDENCE_NOTICE}
              </p>
            </>
          )
        })() : evidenceText ? null : (
          <>
            <Separator />
            <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
              <p className="text-xs text-gray-600 leading-relaxed">
                {UNVERIFIABLE_FINDING_NOTICE}
              </p>
            </div>
          </>
        )}

        {/* 旧格式兼容: evidence 字段 */}
        {!hasQuotes && evidenceText && (
          <>
            <Separator />
            <div>
              <div className="text-xs font-medium text-gray-500 mb-1.5">原文摘录</div>
              <div className="p-3 bg-gray-50 rounded-lg border-l-2 border-gray-300">
                <RichTextContent text={evidenceText} className="text-xs text-gray-700 italic leading-relaxed" variant="compact" />
              </div>
            </div>
          </>
        )}
      </div>
    </ScrollArea>
    {dialog}
    </>
  )
}

export default PairDetail
