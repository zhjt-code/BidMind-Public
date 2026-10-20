import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import api from '@/services/httpClient'
import { reviewService } from '@/services/reviewApi'
import { documentService } from '@/services/documentApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { buildExportFilename, downloadBlob } from '@/lib/download'
import { useCachedProjectName } from '@/hooks/useCachedProjectName'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { isPaymentEnabled } from '@/config/features'
import BalanceGateCard from '@/components/BalanceGateCard'
import { EmptyState, SkeletonStatCards, SkeletonCard, FeedbackPanel } from '@/components'
import ProjectTimelineButton from '@/components/ProjectTimelineButton'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { ArrowLeft } from 'lucide-react'

import type { ReviewResult, ReviewSummary, FilterState, FilterCounts, FactGroup } from './result/types'
import {
  transformResult, transformSummary, getCategoryLabel,
  COMPLIANCE_ORDER, CATEGORY_ORDER,
  groupResultsByFact,
} from './result/types'
import {
  complianceCountKey,
  complianceSortRank,
  matchesComplianceFilter,
} from './result/pendingJudgment'
import {
  filterScoringResults, isScoringUniverseMember,
  buildScoringVolumeNavItems, buildScoringVolumeOrder, createScoringVolumeKeyResolver,
  sortScoringResultsByVolume,
} from './result/scoringViewTransforms'
import { ResultGroupBlock } from './result/ReviewResultList'
import { RESULT_VIEW_PARAM, parseResultView, serializeResultView, type ResultView } from './result/resultViewParam'
import ResultViewSwitch from './result/ResultViewSwitch'
import { documentQualityService } from '@/services/documentQualityApi'
import { pendingFindingCount } from './result/documentQuality/documentQualityTransforms'
import type { DocumentQualityData } from './result/documentQuality/types'

// 文档质检视图懒加载: 默认响应审查视图不加载质检组件代码
const DocumentQualityView = React.lazy(
  () => import('./result/documentQuality/DocumentQualityView'),
)
import ReviewStats from './result/ReviewStats'
import KnowledgeUsagePanel from '@/components/KnowledgeUsagePanel'
import CategoryNav from './result/CategoryNav'
import ResultDetail from './result/ResultDetail'
import GuideTour from '@/components/GuideTour'
import { ResizableLeftPane } from './ResizableLeftPane'
import { ResizableMiddlePane } from './ResizableMiddlePane'
import {
  RESULT_LEFT_PANE_DEFAULT_PERCENT,
  RESULT_MIDDLE_PANE_DEFAULT_PERCENT,
} from './resultMiddlePaneWidth'
import {
  RESULT_PAGE_CONTENT_SHELL_CLASS,
  RESULT_PAGE_DETAIL_PANE_CLASS,
  RESULT_PAGE_ROOT_CLASS,
} from './resultPageLayout'
import RetryActionPanel from './writing/RetryActionPanel'

const ResultPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const projectName = useCachedProjectName(projectId)
  const debugView = new URLSearchParams(window.location.search).get('debug') === '1'

  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [results, setResults] = useState<ReviewResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [showDownloadConfirm, setShowDownloadConfirm] = useState(false)
  const [showResultFeedback, setShowResultFeedback] = useState(false)
  const [extractionWarnings, setExtractionWarnings] = useState<string[]>([])
  const [selectedResultId, setSelectedResultId] = useState<number | null>(null)
  const [bidDocumentId, setBidDocumentId] = useState<number | null>(null)
  const [tenderDocumentId, setTenderDocumentId] = useState<number | null>(null)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retryStateError, setRetryStateError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)

  // 视图切换: 响应审查(默认) | 文档质检(?view=quality) ----
  const [searchParams, setSearchParams] = useSearchParams()
  const view = parseResultView(searchParams.get(RESULT_VIEW_PARAM))
  const handleViewChange = useCallback((next: ResultView) => {
    const serialized = serializeResultView(next)
    setSearchParams(prev => {
      const params = new URLSearchParams(prev)
      if (serialized) params.set(RESULT_VIEW_PARAM, serialized)
      else params.delete(RESULT_VIEW_PARAM)
      return params
    }, { replace: true })
  }, [setSearchParams])

  // 文档质检数据(独立结果域, 与响应审查结果互不阻塞) ----
  const [qualityData, setQualityData] = useState<DocumentQualityData | null>(null)
  const [qualityLoading, setQualityLoading] = useState(false)
  const [qualityError, setQualityError] = useState<string | null>(null)
  const loadQuality = useCallback(async () => {
    if (!projectId) return
    setQualityLoading(true)
    setQualityError(null)
    try {
      setQualityData(await documentQualityService.getDocumentQuality(parseInt(projectId)))
    } catch (err) {
      setQualityError(err instanceof Error ? err.message : '未知错误')
    } finally {
      setQualityLoading(false)
    }
  }, [projectId])
  useEffect(() => { loadQuality() }, [loadQuality])
  const qualityPendingCount = useMemo(() => pendingFindingCount(qualityData), [qualityData])

  // 重试动作的余额门禁：与进度页共用 review_retry 节点快照
  const retryGate = useInsufficientBalanceGate({
    projectId: projectId ? parseInt(projectId) : 0,
    nodeKey: 'review_retry',
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '重试未启动',
  })

  const [filters, setFilters] = useState<FilterState>({
    compliance: 'all',
    risk: 'all',
    category: 'all',
    scoringOnly: false,
    scoringVolume: null,
  })
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({})

  const handleFilterChange = useCallback((partial: Partial<FilterState>) => {
    setFilters(prev => ({ ...prev, ...partial }))
  }, [])

  const loadResults = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      const response = await reviewService.getReviewResults(parseInt(projectId), debugView)
      const data = response as {
        summary?: Record<string, unknown>
        results?: Record<string, unknown>[]
        extraction_warnings?: string[]
      }
      if (data.summary) setSummary(transformSummary(data.summary))
      if (data.results) setResults(data.results.map(transformResult))
      setExtractionWarnings(data.extraction_warnings || [])
    } catch (err) {
      setError(`加载审查结果失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setLoading(false)
    }
  }, [projectId, debugView])

  useEffect(() => { loadResults() }, [loadResults])

  const refreshRetryState = useCallback(async () => {
    if (!projectId) return
    setRefreshingRetry(true)
    try {
      setRetryState(await reviewService.getRetryState(parseInt(projectId)))
      setRetryStateError(null)
    } catch (error) {
      setRetryStateError(error instanceof Error ? error.message : '恢复状态加载失败')
      throw error
    } finally {
      setRefreshingRetry(false)
    }
  }, [projectId])

  useEffect(() => {
    refreshRetryState().catch(() => {})
  }, [refreshRetryState])

  const handleRetryReview = useCallback(async () => {
    if (!projectId) return
    setRetrying(true)
    try {
      const state = await reviewService.retryReview(
        parseInt(projectId),
        retryState?.failedNode,
        retryState?.targetId,
      )
      setRetryState(state)
      retryGate.clear()
      toast.success('审查重试已启动')
      navigate(`/project/${projectId}/progress`)
    } catch (error) {
      // 余额不足走门禁卡（已完成成果保留可见），其余保持原 toast
      if (!retryGate.capture(error)) {
        toast.error(error instanceof Error ? error.message : '审查重试启动失败')
      }
      await refreshRetryState().catch(() => {})
    } finally {
      setRetrying(false)
    }
  }, [projectId, navigate, refreshRetryState, retryState?.failedNode, retryState?.targetId, retryGate])

  useEffect(() => {
    if (!projectId) return
    documentService.getDocuments(parseInt(projectId)).then((res: unknown) => {
      const payload = res as { data?: { bid?: { id?: number }; tender?: { id?: number } } }
      const tender = payload?.data?.tender
      const bid = payload?.data?.bid
      if (tender?.id) setTenderDocumentId(tender.id)
      if (bid?.id) setBidDocumentId(bid.id)
    }).catch(() => {})
  }, [projectId])

  const handleDownloadPdf = async () => {
    if (!projectId) return
    setDownloading(true)
    try {
      const response = await api.get(`/projects/${projectId}/report/download`, {
        responseType: 'blob',
        timeout: 120000,
      })
      if (!(response instanceof Blob)) {
        throw new Error('服务端返回格式异常')
      }
      downloadBlob(response, buildExportFilename(projectName, '投标审查', 'pdf'))
      toast.success('下载完成')
    } catch (err) {
      toast.error(`下载PDF失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setDownloading(false)
    }
  }

  // 评分卷桶解析（原文组标签轴）----
  // 解析器全局唯一: 左栏导航、中栏筛选、计数联动共用同一实例, 保证桶键与后端权威
  // 卷桶键三方对齐（: 桶键与过滤键错位会让点击筛选后列表为空）。
  const multiLotDoc = Object.keys(summary?.estimatedScoreByLot ?? {}).length > 0
  const resolveVolumeKey = useMemo(
    () => createScoringVolumeKeyResolver(filterScoringResults(results), multiLotDoc),
    [results, multiLotDoc],
  )
  // 卷序单一来源: 左栏导航与中栏列表共用, 保证三栏同序。
  const volumeOrder = useMemo(
    () =>
      buildScoringVolumeOrder(
        filterScoringResults(results),
        Object.keys(summary?.estimatedScoreByVolume ?? {}),
        resolveVolumeKey,
      ),
    [results, summary, resolveVolumeKey],
  )

  // 排序 + 筛选 ----
  const { sortedResults, filteredResults } = useMemo(() => {
    const getCategoryOrder = (category: string): number =>
      CATEGORY_ORDER[category] || CATEGORY_ORDER[category.toUpperCase()] || 99

    const sorted = [...results].sort((a, b) => {
      const cd = complianceSortRank(a, COMPLIANCE_ORDER) - complianceSortRank(b, COMPLIANCE_ORDER)
      if (cd !== 0) return cd
      return getCategoryOrder(a.category) - getCategoryOrder(b.category)
    })

    // 评分项视图先收敛到评分项全集并按评分卷序重排（与左栏卷导航同源），再叠加
    // 卷桶/符合度/风险；普通视图沿用符合度+分类排序。
    const scoped = filters.scoringOnly
      ? sortScoringResultsByVolume(filterScoringResults(sorted), resolveVolumeKey, volumeOrder)
      : sorted
    const filtered = scoped.filter(r => {
      if (!matchesComplianceFilter(r, filters.compliance)) return false
      if (filters.risk !== 'all' && r.riskLevel !== filters.risk) return false
      if (filters.scoringOnly) {
        if (filters.scoringVolume !== null && resolveVolumeKey(r) !== filters.scoringVolume) {
          return false
        }
      } else if (filters.category !== 'all' && getCategoryLabel(r.category) !== filters.category) {
        return false
      }
      return true
    })

    return { sortedResults: sorted, filteredResults: filtered }
  }, [results, filters, resolveVolumeKey, volumeOrder])

  // 事实组聚合（同 factKey/canonicalKey 的多条 review_result 折叠为一组）----
  // 列表用：基于筛选后结果分组
  const factGroups = useMemo<FactGroup[]>(
    () => groupResultsByFact(filteredResults),
    [filteredResults],
  )
  // 详情页 siblings 用：基于全量 results，避免筛选时丢失同源条目
  const allFactGroups = useMemo<FactGroup[]>(
    () => groupResultsByFact(results),
    [results],
  )

  // 联动计数 ----
  const filterCounts: FilterCounts = useMemo(() => {
    const byCompliance: Record<string, number> = { all: 0 }
    const byRisk: Record<string, number> = { all: 0 }
    const byCategory: Record<string, number> = { all: 0 }
    const byVolume: Record<string, number> = { all: 0 }

    // 评分项视图下计数仅在评分项全集内联动，分组维度为评分卷；普通视图为分类。
    const scoped = filters.scoringOnly ? sortedResults.filter(isScoringUniverseMember) : sortedResults

    for (const r of scoped) {
      const matchRisk = filters.risk === 'all' || r.riskLevel === filters.risk
      const matchComp = matchesComplianceFilter(r, filters.compliance)
      // 分组维度二选一: 评分项视图看卷, 普通视图看分类, 两者互不叠加。
      const matchGroup = filters.scoringOnly
        ? (filters.scoringVolume === null || resolveVolumeKey(r) === filters.scoringVolume)
        : (filters.category === 'all' || getCategoryLabel(r.category) === filters.category)

      if (matchRisk && matchGroup) {
        const complianceKey = complianceCountKey(r)
        byCompliance[complianceKey] = (byCompliance[complianceKey] || 0) + 1
        byCompliance.all++
      }
      if (matchComp && matchGroup) {
        byRisk[r.riskLevel] = (byRisk[r.riskLevel] || 0) + 1
        byRisk.all++
      }
      if (matchComp && matchRisk) {
        if (filters.scoringOnly) {
          const volumeKey = resolveVolumeKey(r)
          byVolume[volumeKey] = (byVolume[volumeKey] || 0) + 1
          byVolume.all++
        } else {
          const key = getCategoryLabel(r.category)
          byCategory[key] = (byCategory[key] || 0) + 1
          byCategory.all++
        }
      }
    }
    return { byCompliance, byRisk, byCategory, byVolume }
  }, [sortedResults, filters, resolveVolumeKey])

  // 评分项视图：按评分卷导航 + 预估总分（总分数字复用后端权威聚合，不前端重算）----
  const scoringResults = useMemo(() => filterScoringResults(sortedResults), [sortedResults])
  const scoringTotal = useMemo(
    () =>
      summary
        ? {
            estimated: summary.estimatedItemCount > 0 ? summary.estimatedTotalScore : null,
            max: summary.maxTotalScore,
            reliable: summary.scoringTotalReliable,
          }
        : null,
    [summary],
  )
  const scoringVolumeNav = useMemo(
    () =>
      summary
        ? buildScoringVolumeNavItems(
            scoringResults,
            summary.estimatedScoreByVolume,
            resolveVolumeKey,
            { hasEstimate: summary.estimatedItemCount > 0 },
          )
        : [],
    [scoringResults, summary, resolveVolumeKey],
  )

  // 评分卷桶筛选项失效/归零时回退全部 ----
  useEffect(() => {
    if (
      filters.scoringOnly &&
      filters.scoringVolume !== null &&
      !(filterCounts.byVolume[filters.scoringVolume] > 0)
    ) {
      setFilters(f => ({ ...f, scoringVolume: null }))
    }
  }, [filterCounts.byVolume, filters.scoringOnly, filters.scoringVolume])

  // 筛选导致选项归零时自动重置 ----
  useEffect(() => {
    if (filters.compliance !== 'all' && !(filterCounts.byCompliance[filters.compliance] > 0)) {
      setFilters(f => ({ ...f, compliance: 'all' }))
    }
  }, [filterCounts.byCompliance, filters.compliance])

  useEffect(() => {
    if (filters.risk !== 'all' && !(filterCounts.byRisk[filters.risk] > 0)) {
      setFilters(f => ({ ...f, risk: 'all' }))
    }
  }, [filterCounts.byRisk, filters.risk])

  useEffect(() => {
    if (filters.category !== 'all' && !(filterCounts.byCategory[filters.category] > 0)) {
      setFilters(f => ({ ...f, category: 'all' }))
    }
  }, [filterCounts.byCategory, filters.category])

  // 自动选中第一条(数据首次加载 / 当前选中项被筛选掉) ----
  useEffect(() => {
    if (filteredResults.length === 0) return
    const currentStillVisible = selectedResultId !== null && filteredResults.some(r => r.id === selectedResultId)
    if (!currentStillVisible) {
      setSelectedResultId(filteredResults[0].id)
    }
  }, [filteredResults, selectedResultId])

  // 选中结果 ----
  const selectedResult = useMemo(
    () => results.find(r => r.id === selectedResultId) ?? null,
    [results, selectedResultId],
  )

  // 同源事实组的其他成员（基于全量 results 查找，不受筛选影响） ----
  const siblingResults = useMemo<ReviewResult[]>(() => {
    if (!selectedResult) return []
    const group = allFactGroups.find(g => g.members.some(m => m.id === selectedResult.id))
    if (!group) return []
    return group.members.filter(m => m.id !== selectedResult.id)
  }, [allFactGroups, selectedResult])

  // 加载态 ----
  if (loading) {
    return (
      <div>
        <div className="flex justify-between items-center gap-2 mb-6 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="h-8 w-24 bg-gray-200 rounded animate-pulse" />
            <div className="h-8 w-40 bg-gray-200 rounded animate-pulse" />
          </div>
          <div className="h-9 w-28 bg-gray-200 rounded animate-pulse" />
        </div>
        <SkeletonStatCards count={4} className="mb-6" />
        <div className="bg-white rounded-lg shadow">
          <div className="px-4 py-3 border-b bg-gray-50">
            <div className="h-5 w-24 bg-gray-200 rounded animate-pulse" />
          </div>
          {[1, 2, 3].map(i => (
            <SkeletonCard key={i} lines={3} hasTitle className="border-b last:border-b-0" />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className={RESULT_PAGE_ROOT_CLASS}>
      {/* ---- 页头 ---- */}
      <div className="flex justify-between items-center gap-2 px-1 py-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-3 min-w-0 flex-wrap">
          <Button
            variant="ghost" size="icon"
            onClick={() => navigate(`/project/${projectId}/progress`)}
            title="返回审查进度"
            className="shrink-0"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div className="flex items-center min-w-0">
            <h1 className="text-xl font-bold text-gray-900 shrink-0">投标审查结果</h1>
            <div className="mx-1 h-5 w-px shrink-0 bg-gray-200" aria-hidden />
            <ResultViewSwitch
              value={view}
              pendingCount={qualityPendingCount}
              onChange={handleViewChange}
            />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ProjectTimelineButton projectId={projectId} />
          <Button variant="link" className="text-muted-foreground text-xs" onClick={() => setShowResultFeedback(true)}>
            提交反馈
          </Button>
          <Button
            size="sm"
            onClick={() => setShowDownloadConfirm(true)}
            disabled={downloading || results.length === 0}
          >
            {downloading ? '下载中...' : '下载PDF报告'}
          </Button>
        </div>
      </div>

      {view === 'review' && (
        <>
      {error && (
        <div className="mx-1 mb-3 p-3 bg-red-50 rounded-lg border border-red-200 flex-shrink-0">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {retryGate.blocked && (
        <div className="px-1">
          <BalanceGateCard
            layout="inline"
            semantic="retry"
            copy={getBalanceGateCopy('review_retry')}
            message={retryGate.blocked.message}
            paymentEnabled={isPaymentEnabled}
            continueBusy={retrying}
            onRecharge={retryGate.openRecharge}
            onContinue={() => {
              retryGate.clear()
              void handleRetryReview()
            }}
          />
        </div>
      )}
      {retryState?.failedNode && (
        <div className="px-1">
          <RetryActionPanel
            state={retryState}
            onRetry={handleRetryReview}
            onRefresh={refreshRetryState}
            retrying={retrying}
            refreshing={refreshingRetry}
          />
        </div>
      )}
      {!retryState?.failedNode && retryStateError && (
        <div className="mx-1 mb-3 p-3 bg-amber-50 rounded-lg border border-amber-200 flex-shrink-0">
          <p className="text-sm text-amber-800">恢复状态加载失败: {retryStateError}</p>
        </div>
      )}

      {results.length === 0 ? (
        <div className="flex-1 bg-white rounded-lg shadow">
          <EmptyState
            variant="chart"
            title="暂无审查结果"
            description="请确保已完成招标要求确认并启动审查"
            action={{ label: '重新加载', onClick: loadResults }}
          />
        </div>
      ) : (
        <>
          {/* ---- 统计图表（可折叠） ---- */}
          {summary && (
            <div className="flex-shrink-0 px-1">
              <ReviewStats results={results} summary={summary} extractionWarnings={extractionWarnings} />
            </div>
          )}

          {/* ---- 三栏主体 (2:3:5) ---- */}
          <div className={RESULT_PAGE_CONTENT_SHELL_CLASS}>
            {/* 左栏: 分类导航（默认 20%，右缘可拖）— 底部嵌入知识库回显，与串标结果页对齐 */}
            <ResizableLeftPane defaultWidthPercent={RESULT_LEFT_PANE_DEFAULT_PERCENT}>
              <CategoryNav
                results={sortedResults}
                filters={filters}
                filterCounts={filterCounts}
                onFilterChange={handleFilterChange}
                shownCount={filteredResults.length}
                scoringItemCount={summary?.scoringItemCount ?? 0}
                scoringVolumeNav={scoringVolumeNav}
                scoringTotal={scoringTotal}
                footerSlot={projectId ? (
                  <KnowledgeUsagePanel
                    projectId={parseInt(projectId)}
                    flow="bid_review"
                    readOnly
                  />
                ) : null}
              />
            </ResizableLeftPane>

            {/* 中栏: 精简结果列表（默认 30%，右缘可拖）— 评分项视图与普通视图共用卡片列表，
             评分项视图下列表已按权威卷序排列，分值以卡片徽标呈现 */}
            <ResizableMiddlePane defaultWidthPercent={RESULT_MIDDLE_PANE_DEFAULT_PERCENT}>
              <ScrollArea className="flex-1 min-h-0">
                <div className="divide-y">
                  {filteredResults.length === 0 ? (
                    <div className="p-8 text-center text-gray-400 text-sm">
                      当前筛选条件下无匹配结果
                    </div>
                  ) : (
                    factGroups.map(group => {
                      const isMulti = group.members.length >= 2
                      const collapsed = !!collapsedGroups[group.groupKey]
                      const childMembers = isMulti
                        ? group.members.filter(m => m.id !== group.primary.id)
                        : []
                      return (
                        <ResultGroupBlock
                          key={group.groupKey}
                          group={group}
                          isMulti={isMulti}
                          collapsed={collapsed}
                          childMembers={childMembers}
                          selectedResultId={selectedResultId}
                          onSelect={setSelectedResultId}
                          onToggleCollapse={() =>
                            setCollapsedGroups(prev => ({ ...prev, [group.groupKey]: !collapsed }))
                          }
                        />
                      )
                    })
                  )}
                </div>
              </ScrollArea>
            </ResizableMiddlePane>

            {/* 右栏: 详情面板（吃掉剩余宽度） */}
            <div className={RESULT_PAGE_DETAIL_PANE_CLASS}>
              <ResultDetail
                result={selectedResult}
                debugView={debugView}
                projectId={projectId || ''}
                bidDocumentId={bidDocumentId}
                tenderDocumentId={tenderDocumentId}
                siblingResults={siblingResults}
                onSelectSibling={setSelectedResultId}
              />
            </div>
          </div>
          <GuideTour
            storageKey="bidmind_guide_result"
            steps={[
              {
                target: '[data-guide="result-collapse-chart"]',
                title: '收起图表',
                description: '点击此按钮可收起上方图表区域，增大审查内容的可视面积。',
                placement: 'bottom',
              },
              {
                target: '[data-guide="result-page-number"]',
                title: '查看原文',
                description: '点击页码数字可直接打开对应的 PDF 文档页面进行查阅。',
                placement: 'top',
              },
            ]}
          />
        </>
      )}
        </>
      )}

      {view === 'quality' && (
        <React.Suspense
          fallback={<div className="flex-1 px-1"><SkeletonCard lines={4} hasTitle /></div>}
        >
          <DocumentQualityView
            projectId={projectId || ''}
            data={qualityData}
            loading={qualityLoading}
            error={qualityError}
            onReload={loadQuality}
          />
        </React.Suspense>
      )}

      {/* ---- 弹窗 ---- */}
      {showResultFeedback && (
        <FeedbackPanel
          visible={showResultFeedback}
          onClose={() => setShowResultFeedback(false)}
          defaultType="review_question"
          projectId={projectId ? Number(projectId) : undefined}
        />
      )}

      <Dialog open={showDownloadConfirm} onOpenChange={setShowDownloadConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>下载审查报告</DialogTitle>
            <DialogDescription>
              系统将生成 PDF 格式的审查报告并下载到本地。
              报告包含审查汇总、高风险项、全部详细审查结果与文档质检章节。
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-destructive font-medium">
            内容为 AI 生成，实际业务请以人工审核为准。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDownloadConfirm(false)}>取消</Button>
            <Button onClick={() => { setShowDownloadConfirm(false); handleDownloadPdf() }}>确认下载</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default ResultPage
