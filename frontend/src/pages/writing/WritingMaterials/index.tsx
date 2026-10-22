/**
 * 写标素材页（主编排层）。
 *
 * 拆分自原 WritingMaterials.tsx（1261 行 → 目标 ≤ 500 行）：
 * - 5 个领域 hook 承载状态机与副作用（轮询 / 重试 / 知识库 / 预览 / 标题编辑）
 * - 1 个 memo hook 承载素材分组
 * - 7 个子组件承载 UI 渲染
 * - 4 个纯函数模块承载常量与分组工具
 *
 * 本文件仅做：useEffect 联动 + 顶部 Header + 下一步编排 + 组件装配。
 * 禁止在本文件内写业务规则或闭包状态机，新增逻辑应放入对应 hook / 组件。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, Loader2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import BalanceGateCard from '@/components/BalanceGateCard'
import ProgressPanel from '@/components/ProgressPanel'
import { isPaymentEnabled } from '@/config/features'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { useProgressAnimation } from '@/hooks/useProgressAnimation'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { useAuthStore, useWritingStore } from '@/stores'
import { writingService } from '@/services/writingApi'
import type { MaterialBatchStatus } from '@/services/writingApi'
import PdfPreviewDialog from '../../result/PdfPreviewDialog'
import MaterialUploadDialog from '../MaterialUploadDialog'
import RetryActionPanel from '../RetryActionPanel'
import WritingStepBar from '../WritingStepBar'
import { useWritingGuard } from '../useWritingGuard'
import { WRITING_PAGE_MIN_STATUS } from '../writingConstants'
import { formatFailureMessage } from '@/lib/formatFailureMessage'
import { BATCH_STEPS } from './constants'
import { useKnowledgeBaseProjection } from './hooks/useKnowledgeBaseProjection'
import { useMaterialGroups } from './hooks/useMaterialGroups'
import { useMaterialTitleEditing } from './hooks/useMaterialTitleEditing'
import { usePdfPreviewState } from './hooks/usePdfPreviewState'
import { useWritingMaterialsPolling } from './hooks/useWritingMaterialsPolling'
import { useWritingMaterialsRetry } from './hooks/useWritingMaterialsRetry'
import { FirstVisitMaterialsDialog } from './components/FirstVisitMaterialsDialog'
import { KnowledgeBaseDialog } from './components/KnowledgeBaseDialog'
import { MaterialChecklistSections } from './components/MaterialChecklistSections'
import { MaterialListPanel } from './components/MaterialListPanel'

const WritingMaterialsPage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const pid = Number(projectId)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(
    pid,
    WRITING_PAGE_MIN_STATUS.materials,
  )
  const { materials, checklist, loading, loadMaterials, loadChecklist } = useWritingStore()
  const accessToken = useAuthStore((s) => s.accessToken)

  const [showMaterials, setShowMaterials] = useState(true)
  const [showNotices, setShowNotices] = useState(true)
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [batchStatus, setBatchStatus] = useState<MaterialBatchStatus | null>(null)

  const initialSteps = useMemo(() => BATCH_STEPS, [])
  const { steps, displayProgress, updateTargets } = useProgressAnimation(initialSteps)

  // ============ 标题内联编辑 ============
  const titleEditing = useMaterialTitleEditing({ materials })

  // ============ PDF 预览 ============
  const preview = usePdfPreviewState({ pid })

  // ============ 余额门禁============
  const gate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_material_process',
    paymentEnabled: isPaymentEnabled,
  })

  // ============ 重试状态 ============
  const retry = useWritingMaterialsRetry({
    pid,
    onBatchStatusSync: setBatchStatus,
    onMaterialsReload: useCallback(async () => {
      await loadMaterials(pid)
      await loadChecklist(pid)
    }, [pid, loadMaterials, loadChecklist]),
    onRetryStart: useCallback(() => {
      // onRetryStart 仅在重试成功启动后触发，此处解除余额阻断
      gate.clear()
      setShowMaterials(false)
      setShowNotices(false)
      polling.start()
    }, [gate.clear]), // eslint-disable-line react-hooks/exhaustive-deps -- polling 沿用页面既有空依赖模式
    onBalanceBlocked: gate.capture,
  })

  // ============ 轮询 ============
  const polling = useWritingMaterialsPolling({
    pid,
    updateTargets,
    onStatusTick: useCallback((status: MaterialBatchStatus) => {
      setBatchStatus(status)
      retry.setRetryState(status)
    }, []), // eslint-disable-line react-hooks/exhaustive-deps
    onSettled: useCallback(
      async (terminal: 'done' | 'failed' | 'idle') => {
        setShowMaterials(false)
        setShowNotices(false)
        await loadMaterials(pid)
        await loadChecklist(pid)
        if (terminal === 'failed') {
          await retry.refreshRetryState()
        }
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [pid, loadMaterials, loadChecklist],
    ),
    onBalanceBlocked: gate.capture,
  })

  // ============ 知识库投影 ============
  const kb = useKnowledgeBaseProjection({
    pid,
    onMaterialsChanged: useCallback(() => loadMaterials(pid), [pid, loadMaterials]),
  })

  // ============ 素材分组 ============
  const { rootMaterials, childMap, groupedChildMap } = useMaterialGroups(materials)

  // ============ 派生数据 ============
  const missingMaterials = useMemo(
    () => checklist.filter((c) => c.category === 'material' && c.status === 'missing'),
    [checklist],
  )
  const notices = useMemo(
    () => checklist.filter((c) => c.category === 'notice'),
    [checklist],
  )

  const activeRetryState = retry.retryState ?? batchStatus
  const showRetryPanel = Boolean(
    activeRetryState &&
      (activeRetryState.retryable ||
        activeRetryState.retryRunning ||
        activeRetryState.failedNode),
  )

  const hasPendingMaterials = materials.some(
    (m) => m.processingStatus === 'saved' || m.processingStatus === 'processing',
  )

  // ============ 首屏加载 ============
  useEffect(() => {
    if (pid) {
      loadMaterials(pid)
      loadChecklist(pid)
      retry.refreshRetryState().catch(() => {
        // retry-state 只增强失败态展示，首屏加载失败不阻塞素材页
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, loadMaterials, loadChecklist])

  useEffect(() => {
    if (projectStatus === 'failed') {
      retry.refreshRetryState().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectStatus])

  // 切项目时清理标题编辑全部状态（防 类遗漏）
  useEffect(() => {
    titleEditing.resetAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid])

  // 自启动轮询：当 store 中存在 saved/processing 素材但目前没有活跃轮询时。
  // 余额阻断中截断自动恢复，等待用户充值后从门禁卡片手动继续。
  useEffect(() => {
    if (!gate.isBlocked && !polling.isProcessing && hasPendingMaterials && !polling.hasActivePoll()) {
      polling.start()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingMaterials, polling.isProcessing, gate.isBlocked])

  // ============ 动作回调 ============
  const handleUploadComplete = useCallback(async () => {
    toast.success(gate.isBlocked ? '素材文件已保存' : '素材文件已保存，开始后台处理')
    setShowMaterials(false)
    setShowNotices(false)
    await loadMaterials(pid)
    await loadChecklist(pid)
    retry.setRetryState(null)
    // 余额阻断中截断自动推进：素材已保存，由门禁卡片等待用户充值后手动继续
    if (gate.isBlocked) return
    polling.startBatchProcess()
  }, [pid, loadMaterials, loadChecklist, polling, retry, gate.isBlocked])

  const handleDelete = useCallback(
    async (materialId: number) => {
      try {
        await writingService.deleteMaterial(pid, materialId)
        await loadMaterials(pid)
        toast.success('素材已删除')
      } catch {
        toast.error('删除失败')
      }
    },
    [pid, loadMaterials],
  )

  const handleMaterialAction = useCallback(
    async (materialId: number, action: string, successMessage: string) => {
      try {
        await writingService.updateMaterial(pid, materialId, { action })
        await loadMaterials(pid)
        toast.success(successMessage)
      } catch {
        toast.error('操作失败')
      }
    },
    [pid, loadMaterials],
  )

  const handleNextStep = useCallback(async () => {
    if (polling.isProcessing) {
      toast.warning('素材正在处理中，请等待全部处理完成后再进入下一步')
      return
    }

    if (
      !window.confirm(
        '请确认是否已对照招标要求上传了全部所需素材。\n如有遗漏，点击"取消"可返回补充上传。',
      )
    ) {
      return
    }

    if (titleEditing.editingMaterialId != null) {
      const committed = titleEditing.commitEdit()
      if (!committed) return
    }

    if (titleEditing.pendingTitleCount === 0) {
      navigate(`/project/${projectId}/writing/binding`)
      return
    }

    titleEditing.setSavingPendingTitles(true)
    try {
      const result = await writingService.batchUpdateMaterialTitles(
        pid,
        Object.entries(titleEditing.pendingTitleEdits).map(([materialId, displayTitle]) => ({
          materialId: Number(materialId),
          displayTitle,
        })),
      )
      await loadMaterials(pid)
      titleEditing.clearPendingTitles()
      toast.success(`已保存 ${result.updatedCount} 项标题`)
      navigate(`/project/${projectId}/writing/binding`, {
        state: {
          forceRematch: result.requiresRematch,
          fromTitleBatchSave: true,
          forceRematchNonce: Date.now(),
        },
      })
    } catch {
      toast.error('批量保存标题失败')
    } finally {
      titleEditing.setSavingPendingTitles(false)
    }
  }, [
    polling.isProcessing,
    titleEditing,
    loadMaterials,
    navigate,
    pid,
    projectId,
  ])

  // ============ 渲染 ============
  if (guardLoading || !ready) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={2} projectStatus={projectStatus} />
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/project/${projectId}/writing/outline`)}
          >
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <h1 className="text-xl font-semibold">素材管理</h1>
          {materials.length > 0 && (
            <Badge className="bg-green-500 text-white hover:bg-green-600">
              {materials.length} 份素材
            </Badge>
          )}
          {titleEditing.pendingTitleCount > 0 && (
            <Badge variant="outline" className="border-amber-300 text-amber-700">
              待保存 {titleEditing.pendingTitleCount} 项
            </Badge>
          )}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={kb.openKbDialog} disabled={polling.isProcessing}>
            <BookOpen className="h-4 w-4 mr-1" />
            从知识库引用
          </Button>
          <Button
            variant="outline"
            onClick={() => setUploadDialogOpen(true)}
            disabled={polling.isProcessing}
          >
            <Upload className="h-4 w-4 mr-1" />
            上传素材
          </Button>
          <Button
            onClick={handleNextStep}
            disabled={titleEditing.savingPendingTitles || polling.isProcessing}
          >
            {polling.isProcessing
              ? '处理中...'
              : titleEditing.savingPendingTitles
                ? '保存中...'
                : titleEditing.pendingTitleCount > 0
                  ? '保存并下一步'
                  : '下一步'}
            <ArrowRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
        {polling.isProcessing && (
          <div className="bg-white rounded-xl border p-4 mb-4">
            <ProgressPanel
              steps={steps}
              displayProgress={displayProgress}
              stageText={polling.stageText}
              isRunning={polling.isProcessing}
              isCompleted={steps.every((s) => s.status === 'completed')}
              isInitialLoading={!batchStatus}
              error={
                batchStatus?.phase === 'failed'
                  ? (batchStatus.lastError
                    ? formatFailureMessage(batchStatus.lastError)
                    : '处理失败')
                  : null
              }
              accentColor="teal"
              compact
            />
          </div>
        )}

        {!polling.isProcessing && batchStatus?.phase === 'failed' && batchStatus.lastError && !showRetryPanel && (
          <div className="mb-4 p-3 rounded-lg border border-red-200 bg-red-50 text-sm text-red-700">
            原因: {formatFailureMessage(batchStatus.lastError)}
          </div>
        )}

        {showRetryPanel && activeRetryState && !gate.isBlocked && (
          <RetryActionPanel
            state={activeRetryState}
            onRetry={retry.handleRetryMaterialProcess}
            onRefresh={retry.handleRefreshRetryState}
            retrying={retry.retrying}
            refreshing={retry.refreshingRetryState}
          />
        )}

        {gate.isBlocked && (
          <BalanceGateCard
            layout="inline"
            semantic={retry.retryState?.failedNode ? 'retry' : 'completed'}
            copy={getBalanceGateCopy(
              retry.retryState?.failedNode ? 'writing_retry' : 'writing_material_process',
            )}
            message={gate.blocked?.message ?? ''}
            paymentEnabled={isPaymentEnabled}
            continueBusy={retry.retryState?.failedNode ? retry.retrying : polling.isProcessing}
            onRecharge={gate.openRecharge}
            onContinue={() => {
              // 固定契约：先 clear 再启动；启动再次命中余额 403 会重新 capture
              gate.clear()
              if (retry.retryState?.failedNode) {
                void retry.handleRetryMaterialProcess()
              } else {
                void polling.startBatchProcess()
              }
            }}
          />
        )}

        <MaterialUploadDialog
          projectId={pid}
          open={uploadDialogOpen}
          onOpenChange={setUploadDialogOpen}
          onUploadComplete={handleUploadComplete}
        />

        <KnowledgeBaseDialog
          open={kb.kbDialogOpen}
          onOpenChange={kb.setKbDialogOpen}
          loading={kb.kbLoading}
          projecting={kb.kbProjecting}
          docs={kb.kbDocs}
          atomMap={kb.kbAtomMap}
          selectedDocIds={kb.selectedKbIds}
          selectedAtomIds={kb.selectedKbAtomIds}
          onToggleDoc={kb.toggleKbSelection}
          onToggleAtom={kb.toggleKbAtomSelection}
          onSubmit={kb.handleProjectKbMaterials}
        />

        <PdfPreviewDialog
          open={preview.pdfOpen}
          onOpenChange={preview.setPdfOpen}
          pdfUrl={preview.pdfUrl}
          initialPage={preview.pdfPage}
          accessToken={accessToken}
          excerpts={preview.excerpts}
        />

        <MaterialListPanel
          loading={loading}
          isProcessing={polling.isProcessing}
          materials={materials}
          rootMaterials={rootMaterials}
          childMap={childMap}
          groupedChildMap={groupedChildMap}
          savingPendingTitles={titleEditing.savingPendingTitles}
          editingMaterialId={titleEditing.editingMaterialId}
          editingTitleValue={titleEditing.editingTitleValue}
          pendingTitleEdits={titleEditing.pendingTitleEdits}
          getDisplayedTitle={titleEditing.getDisplayedTitle}
          onChangeTitleValue={titleEditing.setEditingTitleValue}
          onCommitTitleEdit={titleEditing.commitEdit}
          onCancelTitleEdit={titleEditing.cancelEdit}
          onStartEditTitle={titleEditing.startEdit}
          onOpenPdfPreview={preview.openPreview}
          onMaterialAction={handleMaterialAction}
          onDelete={handleDelete}
        />

        <MaterialChecklistSections
          missingMaterials={missingMaterials}
          notices={notices}
          showMaterials={showMaterials}
          showNotices={showNotices}
          onToggleMaterials={() => setShowMaterials((v) => !v)}
          onToggleNotices={() => setShowNotices((v) => !v)}
        />
      </div>

      <FirstVisitMaterialsDialog />
    </div>
  )
}

export default WritingMaterialsPage
