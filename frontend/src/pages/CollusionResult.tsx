import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import api from '@/services/httpClient'
import {
  collusionService,
  type CollusionResults,
  type CollusionPairResult,
  type CollusionFinding,
} from '@/services/collusionApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { buildExportFilename, downloadBlob } from '@/lib/download'
import { useCachedProjectName } from '@/hooks/useCachedProjectName'
import { EmptyState, SkeletonCard, SkeletonStatCards, FeedbackPanel } from '@/components'
import ProjectTimelineButton from '@/components/ProjectTimelineButton'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react'
import { APP_HOME } from '@/config/routes'
import { isPaymentEnabled } from '@/config/features'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'

import {
  sortPairsByRisk, buildPairRefReplacer, getEvidenceLevelInfo,
  getRiskConfig, RISK_CONFIG, EVIDENCE_LEVEL_LABELS,
  groupFindingsByDimension, normalizeFindingDimension,
  type CollusionFilterState,
} from './collusionResult/types'
import {
  countFindingsByLevel,
  filterFindingsByLevel,
  countPairsByEvidenceLevel,
  type EvidenceLevelFilter,
} from './collusionResult/evidenceFiltering'
import { getFindingDimensionLabel } from '@/lib/constants'
import CollusionStats from './collusionResult/CollusionStats'
import PairNav from './collusionResult/PairNav'
import PairDetail from './collusionResult/PairDetail'
import GroupNav from './collusionResult/GroupNav'
import GroupDetail from './collusionResult/GroupDetail'
import GlobalFindings from './collusionResult/GlobalFindings'
import GuideTour from '@/components/GuideTour'
import { ResizableLeftPane } from './ResizableLeftPane'
import { ResizableMiddlePane } from './ResizableMiddlePane'
import {
  RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT,
  RESULT_LEFT_PANE_DEFAULT_PERCENT,
  RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT,
  RESULT_MIDDLE_PANE_DEFAULT_PERCENT,
} from './resultMiddlePaneWidth'
import {
  RESULT_PAGE_CONTENT_SHELL_CLASS,
  RESULT_PAGE_DETAIL_PANE_CLASS,
  RESULT_PAGE_ROOT_CLASS,
} from './resultPageLayout'
import RetryActionPanel from './writing/RetryActionPanel'
import KnowledgeUsagePanel from '@/components/KnowledgeUsagePanel'
import { buildFindingListSummary } from './collusionResult/evidenceSummary'

type FlatFinding = CollusionFinding & {
  sourcePairRisk: string
  sourceGroupIdx: number
  involvedDocNames: string[]
  sourcePairKey?: string
  sourcePairDocAId?: number
  sourcePairDocBId?: number
  sourcePairName?: string
}

const RISK_DOT: Record<string, string> = {
  high: 'bg-red-500', medium: 'bg-orange-500', low: 'bg-yellow-500',
  none: 'bg-green-500', inconclusive: 'bg-gray-400',
}
const EVIDENCE_DOT: Record<string, string> = {
  first: 'bg-red-500', second: 'bg-orange-500', third: 'bg-yellow-500',
}

const CollusionResultPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const projectName = useCachedProjectName(projectId)
  // 重试余额门禁迁移到共享 gate；阻断态落 sessionStorage，刷新后恢复
  const gate = useInsufficientBalanceGate({
    projectId: projectId ? parseInt(projectId) : NaN,
    nodeKey: 'collusion_retry',
    paymentEnabled: isPaymentEnabled,
  })

  const [results, setResults] = useState<CollusionResults | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const [showDownloadConfirm, setShowDownloadConfirm] = useState(false)
  const [showResultFeedback, setShowResultFeedback] = useState(false)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retryStateError, setRetryStateError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)

  const [selectedFindingIdx, setSelectedFindingIdx] = useState<number | null>(null)
  const [selectedGroupIdx, setSelectedGroupIdx] = useState<number | null>(null)

  const [filters, setFilters] = useState<CollusionFilterState>({
    risk: 'all',
    evidenceLevel: 'all',
  })

  // 维度二级分组的折叠状态（key=dimension, value=用户主动设置的展开/折叠） ----
  const [dimExpand, setDimExpand] = useState<Record<string, boolean>>({})

  // pair 模式专用状态(旧项目兼容) ----
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)

  const fetchResults = useCallback(async () => {
    if (!projectId) return
    try {
      setLoading(true)
      const data = await collusionService.getResults(parseInt(projectId))
      setResults(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载结果失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { fetchResults() }, [fetchResults])

  const refreshRetryState = useCallback(async () => {
    if (!projectId) return
    setRefreshingRetry(true)
    try {
      setRetryState(await collusionService.getRetryState(parseInt(projectId)))
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

  const handleRetryAnalysis = useCallback(async () => {
    if (!projectId) return
    setRetrying(true)
    try {
      const state = await collusionService.retryAnalysis(
        parseInt(projectId),
        retryState?.failedNode,
        retryState?.targetId,
      )
      setRetryState(state)
      gate.clear()
      toast.success('串标分析重试已启动')
      navigate(`/project/${projectId}/collusion/progress`)
    } catch (error) {
      // 余额 403 由共享 gate 接管（落状态+会话快照+单次 toast+充值弹窗），非余额错误保持原路径
      if (!gate.capture(error)) {
        toast.error(error instanceof Error ? error.message : '串标分析重试启动失败')
      }
      await refreshRetryState().catch(() => {})
    } finally {
      setRetrying(false)
    }
  }, [projectId, navigate, refreshRetryState, retryState?.failedNode, retryState?.targetId, gate.capture, gate.clear])

  const handleDownloadReport = async () => {
    if (!projectId) return
    setIsDownloading(true)
    try {
      const response = await api.get(`/projects/${projectId}/collusion/report/download`, {
        responseType: 'blob',
        timeout: 120000,
      })
      if (!(response instanceof Blob)) {
        throw new Error('服务端返回格式异常')
      }
      downloadBlob(response, buildExportFilename(projectName, '串标审查', 'pdf'))
      toast.success('下载完成')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '下载失败')
    } finally {
      setIsDownloading(false)
    }
  }

  const goBack = () => navigate(`/project/${projectId}/collusion/progress`)

  const sortedPairs = useMemo(() => {
    if (!results) return []
    return sortPairsByRisk(results.pairResults)
  }, [results])

  const replaceRefs = useMemo(
    () => buildPairRefReplacer(sortedPairs),
    [sortedPairs],
  )

  const hasGroups = results ? (results.groups && results.groups.length > 0) : false

  // 群组模式: 群组选择 + 证据扁平化 ----

  const handleSelectGroup = useCallback((idx: number) => {
    setSelectedGroupIdx(idx)
    setFilters({ risk: 'all', evidenceLevel: 'all' })
    setSelectedFindingIdx(null)
  }, [])

  useEffect(() => {
    if (hasGroups && selectedGroupIdx === null && results?.groups?.length) {
      handleSelectGroup(results.groups[0].groupIndex)
    }
  }, [hasGroups, selectedGroupIdx, results, handleSelectGroup])

  const filteredPairs = useMemo(() => {
    if (!hasGroups || selectedGroupIdx === null || !results) return sortedPairs
    const group = results.groups.find(g => g.groupIndex === selectedGroupIdx)
    if (!group) return sortedPairs
    const pairIdSet = new Set(group.relatedPairIds)
    return sortedPairs.filter(p => {
      const pid = `${Math.min(p.docAId, p.docBId)}:${Math.max(p.docAId, p.docBId)}`
      return pairIdSet.has(pid)
    })
  }, [hasGroups, selectedGroupIdx, results, sortedPairs])

  const selectedGroup = useMemo(() => {
    if (!hasGroups || selectedGroupIdx === null || !results) return null
    return results.groups.find(g => g.groupIndex === selectedGroupIdx) ?? null
  }, [hasGroups, selectedGroupIdx, results])

  const groupFindings: FlatFinding[] = useMemo(() => {
    if (hasGroups && selectedGroupIdx !== null && results) {
      const group = results.groups.find(g => g.groupIndex === selectedGroupIdx)
      if (group && group.findings && group.findings.length > 0) {
        return group.findings.map(f => ({
          ...f,
          sourcePairRisk: group.riskLevel.toLowerCase(),
          sourceGroupIdx: group.groupIndex,
          involvedDocNames: f.involvedDocNames || [],
        }))
      }
    }
    const pairs = hasGroups ? filteredPairs : sortedPairs
    const all: FlatFinding[] = []
    for (const pair of pairs) {
      for (const f of pair.findings) {
        all.push({
          ...f,
          sourcePairRisk: (pair.riskLevel || pair.overallRisk).toLowerCase(),
          sourceGroupIdx: selectedGroupIdx ?? -1,
          involvedDocNames: [pair.docAName, pair.docBName],
          sourcePairKey: `${pair.docAId}_${pair.docBId}`,
          sourcePairDocAId: pair.docAId,
          sourcePairDocBId: pair.docBId,
          sourcePairName: `${pair.docAName} × ${pair.docBName}`,
        })
      }
    }
    return all
  }, [hasGroups, selectedGroupIdx, results, filteredPairs, sortedPairs])

  const displayFindings = useMemo(() => {
    const riskFiltered = filters.risk === 'all'
      ? groupFindings
      : groupFindings.filter(f => f.sourcePairRisk === filters.risk)
    return filterFindingsByLevel(riskFiltered, filters.evidenceLevel as EvidenceLevelFilter)
  }, [groupFindings, filters])

  useEffect(() => {
    if (hasGroups && displayFindings.length > 0 && selectedFindingIdx === null) {
      setSelectedFindingIdx(0)
    }
  }, [hasGroups, displayFindings, selectedFindingIdx])

  // 维度折叠重置：群组/筛选切换时清空用户主动设定，回退到 defaultOpen ----
  useEffect(() => {
    setDimExpand({})
  }, [hasGroups, selectedGroupIdx, filters.risk, filters.evidenceLevel, selectedPairKey])

  // 选中证据时自动展开其所在维度（避免选中项被折叠隐藏） ----
  useEffect(() => {
    if (selectedFindingIdx === null) return
    const finding = displayFindings[selectedFindingIdx]
    if (!finding) return
    const dim = normalizeFindingDimension(finding.dimension)
    setDimExpand(prev => prev[dim] === true ? prev : { ...prev, [dim]: true })
  }, [selectedFindingIdx, displayFindings])

  const riskCounts = useMemo(() => {
    const counts: Record<string, number> = { all: groupFindings.length }
    for (const f of groupFindings) {
      counts[f.sourcePairRisk] = (counts[f.sourcePairRisk] || 0) + 1
    }
    return counts
  }, [groupFindings])

  const evidenceLevelCounts = useMemo(() => {
    const riskFiltered = filters.risk === 'all'
      ? groupFindings
      : groupFindings.filter(f => f.sourcePairRisk === filters.risk)
    return countFindingsByLevel(riskFiltered)
  }, [groupFindings, filters.risk])

  const riskKeys = useMemo(() => {
    const available = new Set(groupFindings.map(f => f.sourcePairRisk))
    return (['high', 'medium', 'low', 'none', 'inconclusive'] as const).filter(k => available.has(k))
  }, [groupFindings])

  const midTitle = useMemo(() => {
    const riskLabel = filters.risk === 'all' ? '全部风险' : getRiskConfig(filters.risk).label
    const levelLabel = filters.evidenceLevel === 'all' ? '' : ` ${EVIDENCE_LEVEL_LABELS[filters.evidenceLevel]?.label || ''}`
    return `${riskLabel}${levelLabel} ${displayFindings.length}条证据`
  }, [filters, displayFindings])

  const activeFinding = useMemo<FlatFinding | null>(() => {
    if (selectedFindingIdx === null) return null
    return displayFindings[selectedFindingIdx] ?? null
  }, [displayFindings, selectedFindingIdx])

  const activePair = useMemo<CollusionPairResult | null>(() => {
    if (!activeFinding) return null
    if (activeFinding.sourcePairDocAId && activeFinding.sourcePairDocBId) {
      return sortedPairs.find(p =>
        p.docAId === activeFinding.sourcePairDocAId &&
        p.docBId === activeFinding.sourcePairDocBId
      ) ?? null
    }
    const docIds = activeFinding.involvedDocIds || activeFinding.involvedDocNames?.length
      ? (activeFinding.involvedDocIds || [])
      : []
    if (docIds.length >= 2) {
      return sortedPairs.find(p =>
        docIds.includes(p.docAId) && docIds.includes(p.docBId)
      ) ?? null
    }
    return null
  }, [activeFinding, sortedPairs])

  // pair模式(旧项目兼容) ----

  const selectedPairLegacy = useMemo(
    () => sortedPairs.find(p => `${p.docAId}_${p.docBId}` === selectedPairKey) ?? null,
    [sortedPairs, selectedPairKey],
  )

  const [selectedFindingIdxLegacy, setSelectedFindingIdxLegacy] = useState<number | null>(null)

  const selectedFindingLegacy: CollusionFinding | null = useMemo(() => {
    if (!selectedPairLegacy || selectedFindingIdxLegacy === null) return null
    return selectedPairLegacy.findings[selectedFindingIdxLegacy] ?? null
  }, [selectedPairLegacy, selectedFindingIdxLegacy])

  // pair 模式：选中证据时自动展开其所在维度 ----
  useEffect(() => {
    if (selectedFindingIdxLegacy === null || !selectedPairLegacy) return
    const finding = selectedPairLegacy.findings[selectedFindingIdxLegacy]
    if (!finding) return
    const dim = normalizeFindingDimension(finding.dimension)
    setDimExpand(prev => prev[dim] === true ? prev : { ...prev, [dim]: true })
  }, [selectedFindingIdxLegacy, selectedPairLegacy])

  useEffect(() => {
    if (!hasGroups && sortedPairs.length > 0 && !selectedPairKey) {
      setSelectedPairKey(`${sortedPairs[0].docAId}_${sortedPairs[0].docBId}`)
    }
  }, [hasGroups, sortedPairs, selectedPairKey])

  const handleSelectPairLegacy = useCallback((pair: CollusionPairResult) => {
    setSelectedPairKey(`${pair.docAId}_${pair.docBId}`)
    setSelectedFindingIdxLegacy(null)
  }, [])

  const handleFilterChangeLegacy = useCallback((partial: Partial<CollusionFilterState>) => {
    setFilters(prev => ({ ...prev, ...partial }))
  }, [])

  const filterCountsLegacy = useMemo(() => {
    const byRisk: Record<string, number> = { all: sortedPairs.length }
    for (const pair of sortedPairs) {
      const normalizedRisk = (pair.riskLevel || pair.overallRisk).toLowerCase()
      byRisk[normalizedRisk] = (byRisk[normalizedRisk] || 0) + 1
    }
    const byEvidenceLevel = countPairsByEvidenceLevel(sortedPairs)
    return { byRisk, byEvidenceLevel }
  }, [sortedPairs])

  // 余额门禁卡片：空态分支与正常分支共用同一渲染（去重 的两处横幅）
  const balanceGateCard = gate.blocked ? (
    <div className="px-1">
      <BalanceGateCard
        layout="inline"
        semantic="retry"
        copy={getBalanceGateCopy('collusion_retry')}
        message={gate.blocked.message}
        paymentEnabled={isPaymentEnabled}
        continueBusy={retrying}
        onRecharge={gate.openRecharge}
        onContinue={() => { void handleRetryAnalysis() }}
      />
    </div>
  ) : null

  // 加载态 / 错误态 ----
  if (loading) {
    return (
      <div className={RESULT_PAGE_ROOT_CLASS}>
        <div className="flex justify-between items-center px-1 py-3 shrink-0">
          <div className="flex items-center">
            <Button variant="ghost" size="icon" onClick={goBack} className="mr-3">
              <ArrowLeft className="h-5 w-5" />
            </Button>
            <h1 className="text-xl font-bold text-gray-900">串标分析结果</h1>
          </div>
          <div className="h-9 w-28 bg-gray-200 rounded animate-pulse" />
        </div>
        <SkeletonStatCards count={3} className="mb-4 px-1" />
        <div className="bg-white rounded-lg shadow-sm mx-1 flex-1">
          {[1, 2, 3].map(i => (
            <SkeletonCard key={i} lines={3} hasTitle className="border-b last:border-b-0" />
          ))}
        </div>
      </div>
    )
  }

  if (error || !results) {
    return (
      <div className={RESULT_PAGE_ROOT_CLASS}>
        <div className="flex items-center px-1 py-3 shrink-0">
          <Button variant="ghost" size="icon" onClick={() => navigate(APP_HOME)} className="mr-3">
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <h1 className="text-xl font-bold text-gray-900">串标分析结果</h1>
        </div>
        {balanceGateCard}
        {retryState?.failedNode && (
          <div className="px-1">
            <RetryActionPanel
              state={retryState}
              onRetry={handleRetryAnalysis}
              onRefresh={refreshRetryState}
              retrying={retrying}
              refreshing={refreshingRetry}
            />
          </div>
        )}
        {!retryState?.failedNode && retryStateError && (
          <div className="mx-1 mb-3 p-3 bg-amber-50 rounded-lg border border-amber-200 shrink-0">
            <p className="text-sm text-amber-800">恢复状态加载失败: {retryStateError}</p>
          </div>
        )}
        <div className="flex-1 bg-white rounded-lg shadow-sm mx-1">
          <EmptyState
            variant="folder"
            title="暂无分析结果"
            description={error || '请先完成串标分析'}
            action={{ label: '返回项目列表', onClick: () => navigate(APP_HOME) }}
          />
        </div>
      </div>
    )
  }

  return (
    <div className={RESULT_PAGE_ROOT_CLASS}>
      {/* ---- 页头 ---- */}
      <div className="flex justify-between items-center px-1 py-3 shrink-0">
        <div className="flex items-center">
          <Button variant="ghost" size="icon" onClick={goBack} title="返回分析进度" className="mr-3">
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <h1 className="text-xl font-bold text-gray-900">串标分析结果</h1>
        </div>
        <div className="flex items-center gap-2">
          <ProjectTimelineButton projectId={projectId} />
          <Button variant="link" className="text-muted-foreground text-xs" onClick={() => setShowResultFeedback(true)}>
            提交反馈
          </Button>
          <Button
            size="sm"
            onClick={() => setShowDownloadConfirm(true)}
            disabled={isDownloading || sortedPairs.length === 0}
          >
            {isDownloading ? '生成中...' : '下载报告'}
          </Button>
        </div>
      </div>

      {balanceGateCard}
      {retryState?.failedNode && (
        <div className="px-1">
          <RetryActionPanel
            state={retryState}
            onRetry={handleRetryAnalysis}
            onRefresh={refreshRetryState}
            retrying={retrying}
            refreshing={refreshingRetry}
          />
        </div>
      )}
      {!retryState?.failedNode && retryStateError && (
        <div className="mx-1 mb-3 p-3 bg-amber-50 rounded-lg border border-amber-200 shrink-0">
          <p className="text-sm text-amber-800">恢复状态加载失败: {retryStateError}</p>
        </div>
      )}

      {sortedPairs.length === 0 ? (
        <div className="flex-1 bg-white rounded-lg shadow-sm mx-1">
          <EmptyState
            variant="chart"
            title="暂无对比对结果"
            description="请确保已完成串标分析"
            action={{ label: '重新加载', onClick: fetchResults }}
          />
        </div>
      ) : (
        <>
          {/* ---- 统计概览 ---- */}
          <div className="shrink-0 px-1">
            <CollusionStats
              results={results}
              sortedPairs={sortedPairs}
              replaceRefs={replaceRefs}
            />
          </div>

          {hasGroups ? (
            /* ========== 群组模式: 群组->风险->证据级别->证据列表 ========== */
            <div className={RESULT_PAGE_CONTENT_SHELL_CLASS}>
              {/* 左栏: 群组导航 + 风险等级 + 证据级别 + 全局发现（默认 22%，右缘可拖） */}
              <ResizableLeftPane defaultWidthPercent={RESULT_LEFT_PANE_COLLUSION_GROUP_PERCENT}>
                <ScrollArea className="flex-1 min-h-0 [&>div>div]:min-h-0! [&>div>div]:block!">
                  <div className="py-2">
                    {/* 群组导航 */}
                    <div className="px-2 mb-2">
                      <GroupNav
                        groups={results.groups}
                        selectedGroupIdx={selectedGroupIdx}
                        onSelectGroup={handleSelectGroup}
                      />
                    </div>
                    <div className="border-t mx-2" />

                    {/* 风险等级筛选 */}
                    <FilterSection title="风险等级" defaultOpen>
                      <FilterItem
                        label="全部"
                        count={riskCounts.all || 0}
                        active={filters.risk === 'all'}
                        onClick={() => { setFilters(prev => ({ ...prev, risk: 'all', evidenceLevel: 'all' })); setSelectedFindingIdx(null) }}
                      />
                      {riskKeys.map(key => (
                        <FilterItem
                          key={key}
                          label={RISK_CONFIG[key].label}
                          count={riskCounts[key] || 0}
                          active={filters.risk === key}
                          dotColor={RISK_DOT[key]}
                          onClick={() => { setFilters(prev => ({ ...prev, risk: key, evidenceLevel: 'all' })); setSelectedFindingIdx(null) }}
                        />
                      ))}
                    </FilterSection>

                    {/* 证据级别筛选(级联: 基于当前风险筛选) */}
                    <FilterSection title="证据级别" defaultOpen>
                      <FilterItem
                        label="全部"
                        count={evidenceLevelCounts.all || 0}
                        active={filters.evidenceLevel === 'all'}
                        onClick={() => { setFilters(prev => ({ ...prev, evidenceLevel: 'all' })); setSelectedFindingIdx(null) }}
                      />
                      {(['first', 'second', 'third'] as const).map(key => (
                        <FilterItem
                          key={key}
                          label={EVIDENCE_LEVEL_LABELS[key].label}
                          count={evidenceLevelCounts[key] || 0}
                          active={filters.evidenceLevel === key}
                          disabled={(evidenceLevelCounts[key] || 0) === 0}
                          dotColor={EVIDENCE_DOT[key]}
                          onClick={() => { setFilters(prev => ({ ...prev, evidenceLevel: key })); setSelectedFindingIdx(null) }}
                        />
                      ))}
                    </FilterSection>

                    {/* 全局特征发现 */}
                    {results.globalFindings && results.globalFindings.length > 0 && (
                      <>
                        <div className="border-t mx-2 my-1" />
                        <div className="px-2">
                          <GlobalFindings findings={results.globalFindings} />
                        </div>
                      </>
                    )}

                    {projectId && (
                      <>
                        <div className="border-t mx-2 my-1" />
                        <div className="px-2 pb-3">
                          <KnowledgeUsagePanel
                            projectId={parseInt(projectId)}
                            flow="collusion_check"
                            readOnly
                          />
                        </div>
                      </>
                    )}
                  </div>
                </ScrollArea>
              </ResizableLeftPane>

              {/* 中栏: 证据扁平列表（默认 28%，右缘可拖） */}
              <ResizableMiddlePane defaultWidthPercent={RESULT_MIDDLE_PANE_COLLUSION_GROUP_PERCENT}>
                <div className="px-4 py-3 bg-gray-50 border-b shrink-0">
                  <div className="text-sm font-medium text-gray-800">
                    {midTitle}
                  </div>
                </div>
                <ScrollArea className="flex-1 min-h-0">
                  {selectedGroup && (
                    <div className="border-b bg-gray-50/40">
                      <div className="px-4 pt-2 text-[11px] font-semibold text-gray-500 uppercase tracking-wider">
                        群组研判
                      </div>
                      <GroupDetail
                        group={selectedGroup}
                        pairResults={sortedPairs}
                        replaceRefs={replaceRefs}
                      />
                    </div>
                  )}
                  {displayFindings.length === 0 ? (
                    <div className="p-8 text-center text-gray-400 text-sm">
                      当前筛选条件下无证据
                    </div>
                  ) : (
                    <DimensionGroupedList
                      findings={displayFindings}
                      selectedIdx={selectedFindingIdx}
                      onSelect={setSelectedFindingIdx}
                      dimExpand={dimExpand}
                      onToggleDim={(dim, current) =>
                        setDimExpand(prev => ({ ...prev, [dim]: !current }))
                      }
                      replaceRefs={replaceRefs}
                      showInvolvedNames
                    />
                  )}
                </ScrollArea>
              </ResizableMiddlePane>

              {/* 右栏: 详情面板（吃掉剩余宽度） */}
              <div className={RESULT_PAGE_DETAIL_PANE_CLASS}>
                <PairDetail
                  pair={activePair}
                  finding={activeFinding}
                  replaceRefs={replaceRefs}
                  projectId={projectId || ''}
                />
              </div>
            </div>
          ) : (
            /* ========== 旧项目兼容: pair三栏模式 ========== */
            <div className={RESULT_PAGE_CONTENT_SHELL_CLASS}>
              <ResizableLeftPane defaultWidthPercent={RESULT_LEFT_PANE_DEFAULT_PERCENT}>
                <PairNav
                  sortedPairs={sortedPairs}
                  filters={filters}
                  filterCounts={filterCountsLegacy}
                  selectedPairKey={selectedPairKey}
                  onFilterChange={handleFilterChangeLegacy}
                  onSelectPair={handleSelectPairLegacy}
                />
              </ResizableLeftPane>
              <ResizableMiddlePane defaultWidthPercent={RESULT_MIDDLE_PANE_DEFAULT_PERCENT}>
                <ScrollArea className="flex-1 min-h-0">
                  {selectedPairLegacy ? (
                    <div>
                      <div className="px-4 py-3 bg-gray-50 border-b">
                        <div className="flex items-center justify-between">
                          <div className="text-sm font-medium text-gray-800 truncate">
                            {selectedPairLegacy.docAName} × {selectedPairLegacy.docBName}
                          </div>
                          <span className="text-xs text-gray-500 shrink-0 ml-2">
                            {selectedPairLegacy.findings.length} 条证据
                          </span>
                        </div>
                      </div>
                      <DimensionGroupedList
                        findings={selectedPairLegacy.findings}
                        selectedIdx={selectedFindingIdxLegacy}
                        onSelect={setSelectedFindingIdxLegacy}
                        dimExpand={dimExpand}
                        onToggleDim={(dim, current) =>
                          setDimExpand(prev => ({ ...prev, [dim]: !current }))
                        }
                        replaceRefs={replaceRefs}
                      />
                    </div>
                  ) : (
                    <div className="p-8 text-center text-gray-400 text-sm">
                      请在左侧选择一个对比对
                    </div>
                  )}
                </ScrollArea>
              </ResizableMiddlePane>
              <div className={RESULT_PAGE_DETAIL_PANE_CLASS}>
                <PairDetail
                  pair={selectedPairLegacy}
                  finding={selectedFindingLegacy}
                  replaceRefs={replaceRefs}
                  projectId={projectId || ''}
                />
              </div>
            </div>
          )}
          <GuideTour
            storageKey="bidmind_guide_collusion"
            steps={[
              {
                target: '[data-guide="collusion-collapse-chart"]',
                title: '收起图表',
                description: '点击此按钮可收起上方图表区域，增大分析内容的可视面积。',
                placement: 'bottom',
              },
              {
                target: '[data-guide="collusion-page-number"]',
                title: '查看原文',
                description: '点击页码数字可直接打开对应的 PDF 文档页面进行查阅。',
                placement: 'top',
              },
            ]}
          />
        </>
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
            <DialogTitle>下载串标审查报告</DialogTitle>
            <DialogDescription>
              系统将生成 PDF 格式的串标审查报告并下载到本地。
              报告包含风险评估、相似度矩阵、各对文件详细分析及调查建议。
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-destructive font-medium">
            内容为 AI 生成，实际业务请以人工审核为准。
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDownloadConfirm(false)}>取消</Button>
            <Button onClick={() => { setShowDownloadConfirm(false); handleDownloadReport() }}>确认下载</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// 左栏内联筛选组件 ----

const FilterSection: React.FC<{ title: string; defaultOpen?: boolean; children: React.ReactNode }> = ({
  title, defaultOpen = true, children,
}) => {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="mb-1">
      <button
        className="flex items-center w-full px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider hover:bg-gray-50"
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown className="h-3 w-3 mr-1" /> : <ChevronRight className="h-3 w-3 mr-1" />}
        {title}
      </button>
      {open && <div className="pb-1">{children}</div>}
    </div>
  )
}

const FilterItem: React.FC<{
  label: string
  count: number
  active: boolean
  disabled?: boolean
  dotColor?: string
  onClick: () => void
}> = ({ label, count, active, disabled, dotColor, onClick }) => (
  <button
    className={`flex items-center justify-between w-full px-3 py-1.5 text-sm transition-colors ${
      disabled
        ? 'text-gray-300 cursor-not-allowed'
        : active
          ? 'bg-primary/10 text-primary font-medium'
          : 'text-gray-600 hover:bg-gray-50'
    }`}
    disabled={disabled}
    onClick={() => !disabled && onClick()}
  >
    <span className="flex items-center gap-2 truncate">
      {dotColor && <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />}
      {label}
    </span>
    <Badge variant="secondary" className="ml-2 h-5 min-w-6 justify-center text-xs">
      {count}
    </Badge>
  </button>
)

// ==================== 中栏证据按维度二级分组渲染 ====================

interface CommonFinding {
  level: string
  dimension?: string | null
  description: string
  similarity?: number | null
  certainty?: number | null
  involvedDocNames?: string[]
  sourcePairName?: string
}

interface DimensionGroupedListProps<F extends CommonFinding> {
  findings: F[]
  selectedIdx: number | null
  onSelect: (idx: number) => void
  dimExpand: Record<string, boolean>
  onToggleDim: (dim: string, currentOpen: boolean) => void
  replaceRefs: (text: string | undefined) => string
  showInvolvedNames?: boolean
}

function DimensionGroupedList<F extends CommonFinding>({
  findings, selectedIdx, onSelect, dimExpand, onToggleDim, replaceRefs, showInvolvedNames,
}: DimensionGroupedListProps<F>) {
  const findingsWithIdx = useMemo(
    () => findings.map((f, i) => ({ ...f, _idx: i })),
    [findings],
  )
  const sections = useMemo(
    () => groupFindingsByDimension(findingsWithIdx),
    [findingsWithIdx],
  )

  return (
    <div>
      {sections.map(section => {
        const userSet = dimExpand[section.dimension]
        const isOpen = userSet !== undefined ? userSet : section.defaultOpen
        return (
          <div key={section.dimension} className="border-b last:border-b-0">
            <button
              type="button"
              onClick={() => onToggleDim(section.dimension, isOpen)}
              className="w-full flex items-center justify-between px-4 py-2 bg-gray-50/60 hover:bg-gray-100 text-xs font-semibold text-gray-600 transition-colors"
            >
              <span className="flex items-center gap-1">
                {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                {section.label}
              </span>
              <span className="text-gray-500">{section.findings.length} 条</span>
            </button>
            {isOpen && (
              <div className="divide-y">
                {section.findings.map(finding => {
                  const levelInfo = getEvidenceLevelInfo(finding.level)
                  const isSelected = selectedIdx === finding._idx
                  return (
                    <button
                      key={`${section.dimension}-${finding._idx}`}
                      className={`w-full text-left pl-7 pr-4 py-3 transition-colors hover:bg-gray-50 ${
                        isSelected ? 'bg-primary/5 border-l-2 border-l-primary' : ''
                      }`}
                      onClick={() => onSelect(finding._idx)}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <span className={`px-1.5 py-0 text-[10px] rounded-md font-medium ${levelInfo.color}`}>
                          {levelInfo.label}
                        </span>
                        {finding.dimension && finding.dimension !== 'unknown' && (
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                            {getFindingDimensionLabel(finding.dimension)}
                          </Badge>
                        )}
                        <div className="flex items-center gap-2 ml-auto shrink-0">
                          {finding.similarity != null ? (
                            <span className="text-[10px] text-gray-400">
                              {(finding.similarity * 100).toFixed(0)}%
                            </span>
                          ) : finding.certainty != null && finding.certainty > 0 ? (
                            <span className="text-[10px] text-gray-400">
                              {(finding.certainty * 100).toFixed(0)}%
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="text-sm text-gray-700 line-clamp-2 wrap-break-word wrap-anywhere">
                        {buildFindingListSummary({
                          ...finding,
                          description: replaceRefs(finding.description),
                        })}
                      </div>
                      {showInvolvedNames && (
                        <div className="text-[10px] text-gray-400 mt-0.5 truncate">
                          {finding.involvedDocNames?.length
                            ? finding.involvedDocNames.join(' / ')
                            : finding.sourcePairName || ''}
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default CollusionResultPage
