/**
 * 写标·素材关联页（主编排层）。
 *
 * 拆分自原 WritingBinding.tsx（1113 行 → 目标 ≤ 400 行）：
 * - 5 个领域 hook 承载状态机与副作用（match polling / binding tree / filter / actions / batch confirm）
 * - 5 个子组件承载 UI 渲染（filter cards / tree view / batch confirm dialog / tree node / binding row）
 * - 2 个纯函数模块承载常量与派生（bindingDerivations / bindingLabel）
 *
 * 本文件仅做：guard + 初始加载 + 5 个 hook destructure + Header + 下一步编排 + 组件装配。
 * 禁止在本文件内写业务规则或闭包状态机，新增逻辑应放入对应 hook / 组件。
 *
 * 路由 lazy 入口：`../WritingBinding.tsx`（11 行 shim， 不删 lazy 物理文件）。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useParams, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ArrowLeft, ArrowRight, Zap, Check, Loader2 } from 'lucide-react'
import ProgressPanel from '@/components/ProgressPanel'
import BalanceGateCard from '@/components/BalanceGateCard'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { isPaymentEnabled } from '@/config/features'
import { useWritingStore } from '@/stores'
import { writingService } from '@/services/writingApi'
import { useWritingGuard } from '../useWritingGuard'
import WritingStepBar from '../WritingStepBar'
import { canProceedWithoutBindings, composeNavigationState, getEmptyBindingNotice, shouldShowBindingPageLoading } from '../writingBindingState'
import { toast } from 'sonner'
import { WRITING_PAGE_MIN_STATUS } from '../writingConstants'
import RetryActionPanel from '../RetryActionPanel'
import { BindingFilterCards } from './components/BindingFilterCards'
import { BindingTreeView } from './components/BindingTreeView'
import { BatchConfirmDialog } from './components/BatchConfirmDialog'
import { RematchConfirmDialog } from './components/RematchConfirmDialog'
import { useBindingTree } from './hooks/useBindingTree'
import { useBindingActions } from './hooks/useBindingActions'
import { useBatchConfirmFlow } from './hooks/useBatchConfirmFlow'
import { useBindingFilter } from './hooks/useBindingFilter'
import { useMatchPolling } from './hooks/useMatchPolling'

const WritingBindingPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const pid = Number(projectId)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(pid, WRITING_PAGE_MIN_STATUS.binding)
  const { bindings, outlineNodes, clauses, materials, loading, loadBindings, loadOutline, loadClauses, loadMaterials, triggerMatch } = useWritingStore()
  const [initialLoaded, setInitialLoaded] = useState(false)
  const [rematchDialogOpen, setRematchDialogOpen] = useState(false)
  const [ignoreOrphansDialogOpen, setIgnoreOrphansDialogOpen] = useState(false)
  const [ignoringOrphans, setIgnoringOrphans] = useState(false)

  const {
    pendingBindingAction,
    setPendingBindingAction,
    hasLocalAction,
    handleConfirmOne,
    handleReject,
    handleRestore,
    handleAssignToChapter,
  } = useBindingActions({ pid })
  const {
    batchConfirmOpen,
    setBatchConfirmOpen,
    batchConfirming,
    handleConfirmAll,
  } = useBatchConfirmFlow({ pid, bindings, loadBindings, setPendingBindingAction })

  // pid 切换时重置主入口 state；hook 内的守卫 ref 各自负责（useBindingFilter / useMatchPolling）
  useEffect(() => {
    setInitialLoaded(false)
  }, [pid])

  // 初始加载（4 个 store 并行 fetch）
  const initialLoadMountedRef = useRef(true)
  useEffect(() => {
    initialLoadMountedRef.current = true
    return () => { initialLoadMountedRef.current = false }
  }, [])
  useEffect(() => {
    if (pid && ready) {
      Promise.all([
        loadBindings(pid).catch(() => {}),
        loadOutline(pid).catch(() => {}),
        loadClauses(pid).catch(() => {}),
        loadMaterials(pid).catch(() => {}),
      ]).then(() => {
        if (initialLoadMountedRef.current) setInitialLoaded(true)
      })
    }
  }, [pid, ready, loadBindings, loadOutline, loadClauses, loadMaterials])

  const {
    treeRoots,
    unlinkedBindings,
    globalBindings,
    orphanBindings,
    unappliedMaterialBindings,
    clauseMap,
    materialMap,
    leafNodeOptions,
    nodeParentMap,
    highConf,
    lowConf,
    globalCount,
    suggested,
    toConfirmBindings,
    toConfirmHighConf,
    toConfirmLowConf,
    hasEffectiveBindings,
    conflictCount,
    conflictMaterialIds,
  } = useBindingTree({ bindings, outlineNodes, clauses, materials })

  const {
    collapsedTypes,
    confidenceFilter,
    toggleTypeCollapse,
    handleFilterClick,
    clearFilter,
    expandAll,
    collapseAll,
    focusSection,
  } = useBindingFilter({ pid, initialLoaded, outlineNodes, bindings, conflictMaterialIds, nodeParentMap })

  useEffect(() => {
    if (!initialLoaded) return
    const state = location.state as { focus?: 'pendingAssignment' | 'unmatched' | 'unappliedMaterial' } | null
    if (!state?.focus) return
    const focusMap = {
      pendingAssignment: 'orphan',
      unmatched: 'unmatched',
      unappliedMaterial: 'unappliedMaterial',
    } as const
    focusSection(focusMap[state.focus])
    navigate(location.pathname, { replace: true, state: null })
  }, [initialLoaded, location.pathname, location.state, focusSection, navigate])

  // 余额门禁：匹配启动 403 落阻断态并截断自动触发
  const balanceGate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_match',
    paymentEnabled: isPaymentEnabled,
  })

  const {
    matching,
    matchStageText,
    matchProgressSteps,
    matchDisplayProgress,
    retryState,
    retrying,
    refreshingRetry,
    handleMatch,
    handleRetryMatch,
    refreshRetryState,
  } = useMatchPolling({
    pid,
    ready,
    projectStatus,
    initialLoaded,
    bindings,
    triggerMatch,
    loadBindings,
    location,
    navigate,
    balanceBlocked: balanceGate.isBlocked,
    onBalanceBlocked: balanceGate.capture,
    onBalanceCleared: balanceGate.clear,
  })

  // retryState 指向本节点失败时按 retry 语义展示门禁卡，否则视为上一节点已完成
  const matchGateRetrySemantic = retryState?.failedNode === 'writing_match'

  const showPageLoading = shouldShowBindingPageLoading({
    initialLoaded,
    loading,
    hasBindings: bindings.length > 0,
    hasLocalAction,
  })
  const canProceedToCompose = canProceedWithoutBindings({
    matching,
    loading: loading || hasLocalAction || batchConfirming,
  })
  const activeOrphanBindings = orphanBindings.filter((b) => b.status !== 'rejected')
  const unmatchedClauses = useMemo(() => {
    const boundClauseIds = new Set(
      bindings
        .filter((b) => b.bindingType === 'clause_to_node' && b.clauseId != null)
        .map((b) => b.clauseId as number),
    )
    return clauses.filter((clause) => clause.confirmed === 1 && !boundClauseIds.has(clause.id))
  }, [bindings, clauses])

  const goCompose = (refreshCoverage = false) => navigate(
    `/project/${projectId}/writing/compose`,
    { state: composeNavigationState(refreshCoverage) },
  )

  const confirmIgnoreOrphans = async () => {
    setIgnoringOrphans(true)
    try {
      for (const binding of activeOrphanBindings) {
        await writingService.updateBinding(pid, binding.id, {
          status: 'rejected',
          reason: '用户确认不消费',
        })
      }
      await loadBindings(pid)
      setIgnoreOrphansDialogOpen(false)
      toast.success('已确认不消费未分配约束')
      goCompose(true)
    } catch {
      toast.error('确认不消费失败')
    } finally {
      setIgnoringOrphans(false)
    }
  }

  if (guardLoading || !ready) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={2} projectStatus={projectStatus} />
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/project/${projectId}/writing/materials`)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <h1 className="text-xl font-semibold">素材关联</h1>
          <Badge variant="secondary">{bindings.length} 条绑定</Badge>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => {
              if (bindings.length > 0) {
                setRematchDialogOpen(true)
              } else {
                handleMatch(false)
              }
            }}
            disabled={loading || matching || hasLocalAction}
          >
            {matching ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Zap className="h-4 w-4 mr-1" />}
            {matching ? '匹配中...' : bindings.length > 0 ? '重新匹配' : '开始匹配'}
          </Button>
          {suggested > 0 && (
            <Button variant="outline" onClick={() => setBatchConfirmOpen(true)} disabled={batchConfirming || hasLocalAction}>
              {batchConfirming ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Check className="h-4 w-4 mr-1" />}
              {batchConfirming ? '确认中...' : `全部确认 (${suggested})`}
            </Button>
          )}
          <Button
            onClick={() => {
              if (!canProceedToCompose) { toast.warning('当前仍在处理中，请稍后再试'); return }
              if (!hasEffectiveBindings) {
                toast.info(getEmptyBindingNotice())
                return
              }
              if (suggested > 0) {
                toast.warning(`还有 ${suggested} 条待确认绑定`)
                return
              }
              if (activeOrphanBindings.length > 0) {
                setIgnoreOrphansDialogOpen(true)
                return
              }
              goCompose()
            }}
            disabled={matching || loading || hasLocalAction || batchConfirming || suggested > 0}
            title={suggested > 0 ? `还有 ${suggested} 条待确认绑定` : ''}
          >
            下一步
            <ArrowRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      </div>

      <div className="max-w-5xl mx-auto">
      {balanceGate.isBlocked && (
        <BalanceGateCard
          layout="inline"
          semantic={matchGateRetrySemantic ? 'retry' : 'completed'}
          copy={getBalanceGateCopy(matchGateRetrySemantic ? 'writing_retry' : 'writing_match')}
          message={balanceGate.blocked?.message ?? ''}
          paymentEnabled={isPaymentEnabled}
          continueBusy={matching || retrying}
          onRecharge={balanceGate.openRecharge}
          onContinue={() => {
            balanceGate.clear()
            if (matchGateRetrySemantic) {
              void handleRetryMatch()
            } else {
              void handleMatch()
            }
          }}
        />
      )}
      {retryState?.failedNode === 'writing_match' && !balanceGate.isBlocked && (
        <RetryActionPanel
          state={retryState}
          onRetry={handleRetryMatch}
          onRefresh={refreshRetryState}
          retrying={retrying}
          refreshing={refreshingRetry}
        />
      )}
      {bindings.length > 0 && (
        <BindingFilterCards
          highConf={highConf}
          lowConf={lowConf}
          conflictCount={conflictCount}
          globalCount={globalCount}
          confidenceFilter={confidenceFilter}
          onFilterClick={handleFilterClick}
        />
      )}

      <div className="bg-white rounded-xl border">
        {matching ? (
          <div className="p-4">
            <ProgressPanel
              steps={matchProgressSteps}
              displayProgress={matchDisplayProgress}
              stageText={matchStageText}
              isRunning
              isCompleted={matchProgressSteps.every((s) => s.status === 'completed')}
              isInitialLoading={false}
              error={null}
              accentColor="teal"
              compact
            />
          </div>
        ) : showPageLoading ? (
          <div className="text-center py-12 text-muted-foreground">
            <Loader2 className="h-8 w-8 animate-spin mx-auto mb-2 text-primary" />
            加载中...
          </div>
        ) : bindings.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <p className="mb-2">当前暂无可用绑定</p>
            <p className="mb-4 text-sm">你可以重新执行匹配，或直接进入内容编辑，后续再补充素材后重新匹配。</p>
            <div className="flex items-center justify-center gap-2">
              <Button variant="outline" onClick={() => handleMatch(false)}>
                <Zap className="h-4 w-4 mr-1" />
                重新匹配
              </Button>
              <Button onClick={() => navigate(`/project/${projectId}/writing/compose`)}>
                <ArrowRight className="h-4 w-4 mr-1" />
                直接进入内容编辑
              </Button>
            </div>
          </div>
        ) : (
          <BindingTreeView
            treeRoots={treeRoots}
            globalBindings={globalBindings}
            orphanBindings={orphanBindings}
            unappliedMaterialBindings={unappliedMaterialBindings}
            unlinkedBindings={unlinkedBindings}
            unmatchedClauses={unmatchedClauses}
            bindingsCount={bindings.length}
            clauseMap={clauseMap}
            materialMap={materialMap}
            leafNodeOptions={leafNodeOptions}
            collapsedTypes={collapsedTypes}
            confidenceFilter={confidenceFilter}
            conflictMaterialIds={conflictMaterialIds}
            pendingBindingAction={pendingBindingAction}
            toggleTypeCollapse={toggleTypeCollapse}
            clearFilter={clearFilter}
            expandAll={expandAll}
            collapseAll={collapseAll}
            onConfirm={handleConfirmOne}
            onReject={handleReject}
            onRestore={handleRestore}
            onAssignToChapter={handleAssignToChapter}
          />
        )}
      </div>
      </div>
      <BatchConfirmDialog
        open={batchConfirmOpen}
        onOpenChange={setBatchConfirmOpen}
        batchConfirming={batchConfirming}
        toConfirmCount={toConfirmBindings.length}
        toConfirmHighConf={toConfirmHighConf}
        toConfirmLowConf={toConfirmLowConf}
        conflictCount={conflictCount}
        onConfirmAll={handleConfirmAll}
      />
      <RematchConfirmDialog
        open={rematchDialogOpen}
        onOpenChange={setRematchDialogOpen}
        matching={matching}
        onRematch={(resetReviewedBindings) => {
          setRematchDialogOpen(false)
          handleMatch(resetReviewedBindings)
        }}
      />
      <Dialog open={ignoreOrphansDialogOpen} onOpenChange={(next) => {
        if (!ignoringOrphans) setIgnoreOrphansDialogOpen(next)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认不消费未分配约束</DialogTitle>
            <DialogDescription>
              仍有 {activeOrphanBindings.length} 条约束未分配章节。若确认不消费，后续内容编辑和预览导出不再将它们作为未消费提醒；返回本页仍可重新匹配或人工分配。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIgnoreOrphansDialogOpen(false)} disabled={ignoringOrphans}>
              返回分配
            </Button>
            <Button onClick={confirmIgnoreOrphans} disabled={ignoringOrphans}>
              {ignoringOrphans ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
              确认不消费并继续
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default WritingBindingPage
