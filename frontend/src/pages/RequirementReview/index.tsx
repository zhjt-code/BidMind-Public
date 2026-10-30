import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { requirementService } from '@/services/requirementApi'
import { reviewService } from '@/services/reviewApi'
import { projectService } from '@/services/projectApi'
import { EmptyState } from '@/components'
import KnowledgeUsagePanel from '@/components/KnowledgeUsagePanel'
import { useMetricProbe } from '@/hooks/useMetricProbe'
import { useProgressAnimation } from '@/hooks/useProgressAnimation'
import { useFirstVisitDialog } from '@/hooks/useFirstVisitDialog'
import { useAuthStore } from '@/stores'
import { isPaymentEnabled } from '@/config/features'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getCategoryLabel } from '@/lib/constants'
import PdfPreviewDialog from '../result/PdfPreviewDialog'
import {
  formatExtractionWarning,
  type Requirement,
  type ScoringLedgerReport,
  type ScoringTotalsReport,
  transformRequirement,
  transformScoringLedgerReport,
  transformScoringTotalsReport,
} from '../requirementReviewTransforms'
import { EXTRACTION_STEPS } from './constants'
import { transformScoringVerdict, type ScoringVerdict } from './scoringVerdict'
import {
  buildRiskNoticeSummary,
  buildScoringFocusSummary,
  countReferenceByCategory,
  getScoringFocusGroupLabels,
  isScoringFocusItem,
  sortRequirements,
  splitFoldedRequirements,
} from './helpers'
import { useDocumentReadiness } from './hooks/useDocumentReadiness'
import { useRequirementExtractionPolling } from './hooks/useRequirementExtractionPolling'
import { shouldHoldExtractionProgressView } from './hooks/extractionLoadGuard'
import { useRequirementList } from './hooks/useRequirementList'
import { useRequirementPdfPreview } from './hooks/useRequirementPdfPreview'
import { AllSourcePagesDialog } from './components/AllSourcePagesDialog'
import { ConfirmStartReviewDialog } from './components/ConfirmStartReviewDialog'
import { DeleteConfirmDialog } from './components/DeleteConfirmDialog'
import { DocumentNotReadyBanner } from './components/DocumentNotReadyBanner'
import { ExtractionProgressPanel } from './components/ExtractionProgressPanel'
import { ExtractionWarningBanner } from './components/ExtractionWarningBanner'
import { ScoringTotalsBanner } from './components/ScoringTotalsBanner'
import { FirstVisitGuideDialog } from './components/FirstVisitGuideDialog'
import { RequirementAddDialog } from './components/RequirementAddDialog'
import { ReextractConfirmDialog } from './components/ReextractConfirmDialog'
import { RequirementFilterBar } from './components/RequirementFilterBar'
import { RequirementHeader } from './components/RequirementHeader'
import { RequirementTable } from './components/RequirementTable'
import { FoldedRequirementsSection } from './components/FoldedRequirementsSection'
import { RiskNoticeSection } from './components/RiskNoticeSection'
import { getRequirementReviewPrimaryAction } from './reviewFlowState'

const splitMultilineInput = (value: string): string[] =>
  value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)

const parseSourcePagesInput = (value: string): number[] =>
  Array.from(
    new Set(
      value
        .split(/[,，\s]+/)
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isFinite(item) && item >= 1)
        .map((item) => Math.floor(item)),
    ),
  ).sort((a, b) => a - b)

const RequirementReviewPage: React.FC = () => {
  useMetricProbe({ page: 'RequirementReview' })
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const numericProjectId = projectId ? parseInt(projectId) : 0
  // 余额门禁按计费节点隔离：首次提取 / 重新提取 / 确认并开始审查
  const extractGate = useInsufficientBalanceGate({
    projectId: numericProjectId,
    nodeKey: 'requirement_extract',
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '招标要求提取失败',
  })
  const reextractGate = useInsufficientBalanceGate({
    projectId: numericProjectId,
    nodeKey: 'requirement_reextract',
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '重新提取失败',
  })
  const startGate = useInsufficientBalanceGate({
    projectId: numericProjectId,
    nodeKey: 'review_start',
    paymentEnabled: isPaymentEnabled,
    toastPrefix: '确认失败',
  })
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [reextracting, setReextracting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [showReextractDialog, setShowReextractDialog] = useState(false)
  const [projectStatus, setProjectStatus] = useState<string>('')
  // 评分项按卷满分校验报告(第3层): 卷满分缺口/权重异常时提示人工核对
  const [scoringTotalsReport, setScoringTotalsReport] = useState<ScoringTotalsReport | null>(null)
  // 评分账本对账报告(P5-1): 冻结窗口内与 legacy 报告并存展示,review_required 时提示账本对账需复核
  const [scoringLedgerReport, setScoringLedgerReport] =
    useState<ScoringLedgerReport | null>(null)
  // 评分三态权威结论(verdict 单源): 后端 VLM 救援触发与横幅三态消费同一结论
  const [scoringVerdict, setScoringVerdict] = useState<ScoringVerdict | null>(null)

  // 招标要求列表（CRUD + 乐观更新由 hook 内部承担）
  const {
    requirements,
    setRequirements,
    toggleMandatory,
    updateDescription,
    deleteRequirement,
    createRequirement,
    createScoringItem,
    promoteToActionable,
    demoteToReference,
    updateScoringWeight,
  } = useRequirementList({ projectId })

  // 提取进度动画（复用 ProgressPanel 体系）
  const {
    steps: extractionSteps,
    displayProgress: extractionDisplayProgress,
    updateTargets: updateExtractionTargets,
  } = useProgressAnimation(EXTRACTION_STEPS)

  // 提取任务自启动 + 轮询（含 extracting / stageText / initial / warnings 状态）
  const {
    extracting,
    extractionStageText,
    extractionInitial,
    extractionWarnings,
    startIfNeeded: startExtractionIfNeeded,
    startForRun: startExtractionForRun,
  } = useRequirementExtractionPolling({
    projectId,
    updateExtractionTargets,
    onRequirementsLoaded: (items) => setRequirements(items),
    onScoringTotalsReportLoaded: (report) =>
      setScoringTotalsReport(transformScoringTotalsReport(report)),
    onScoringLedgerReportLoaded: (report) =>
      setScoringLedgerReport(transformScoringLedgerReport(report)),
    onScoringVerdictLoaded: (raw) => setScoringVerdict(transformScoringVerdict(raw)),
    onError: (message) => setError(message),
    // 闭包每轮渲染重建并由 hook 内部 ref 保活，capture/isBlocked 始终读取最新门禁态
    onBalanceBlocked: (blockedError) => { extractGate.capture(blockedError) },
    shouldBlockAutoStart: () => extractGate.isBlocked,
  })

  // 筛选状态
  const [activeTab, setActiveTab] = useState<string>('all')
  const [showMandatoryOnly, setShowMandatoryOnly] = useState(false)
  // 仅显示评分项/重点项（与分类、否决项正交；由筛选栏 chip 或核对提示条"聚焦评分项"驱动）
  const [showScoringFocusOnly, setShowScoringFocusOnly] = useState(false)
  // 已展开的分组 key（分类标签文字）；空集合 = 全部分类默认折叠
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  // 风险告知 / 参考条款布局交互（本会话，不入库）
  const [riskNoticeViewed, setRiskNoticeViewed] = useState(false)
  const [showStartReviewConfirm, setShowStartReviewConfirm] = useState(false)
  const [riskExpandRequest, setRiskExpandRequest] = useState(false)
  const [jumpToReferenceCategory, setJumpToReferenceCategory] = useState<string | null>(null)

  const { open: showGuideTip, dismiss: dismissGuideTip } = useFirstVisitDialog('bidmind_guide_requirement_review')

  // 添加招标要求
  const [showAddModal, setShowAddModal] = useState(false)
  const [addMode, setAddMode] = useState<'requirement' | 'scoring_item'>('requirement')
  const [newCategory, setNewCategory] = useState('TECH')
  const [newDescription, setNewDescription] = useState('')
  const [newIsMandatory, setNewIsMandatory] = useState(false)
  const [newScoreItemName, setNewScoreItemName] = useState('')
  const [newScoreWeight, setNewScoreWeight] = useState('')
  const [newScoreRules, setNewScoreRules] = useState('')
  const [newDeductionRules, setNewDeductionRules] = useState('')
  const [newVolume, setNewVolume] = useState('')
  const [newVolumeLabel, setNewVolumeLabel] = useState('')
  const [newLot, setNewLot] = useState('')
  const [newSourcePages, setNewSourcePages] = useState('')
  const [adding, setAdding] = useState(false)

  // 招标 / 投标文档就绪状态（含 tenderDocId 用于 PDF 预览兜底）
  const {
    tenderDocReady,
    tenderDocMessage,
    tenderDocId,
    bidDocReady,
    bidDocMessage,
    refresh: refreshDocumentReadiness,
  } = useDocumentReadiness({ projectId })

  // 来源页码 PDF 预览（含全部页码弹窗）
  const {
    pdfOpen,
    setPdfOpen,
    pdfPage,
    pdfUrl,
    excerpts: pdfExcerpts,
    handleOpenPdfPreview,
    showAllPagesReq,
    setShowAllPagesReq,
  } = useRequirementPdfPreview({ projectId, tenderDocId })

  const accessToken = useAuthStore((s) => s.accessToken)
  const extractionWarningDisplays = useMemo(
    () => extractionWarnings.map(formatExtractionWarning),
    [extractionWarnings],
  )
  const primaryAction = useMemo(
    () => getRequirementReviewPrimaryAction(projectId || '', projectStatus),
    [projectId, projectStatus],
  )

  // 评分核对范围聚合：只统计进入主审查列表的 actionable 项，与筛选栏计数口径一致
  // （参考条款/风险告知折叠区不在主列表内，计入会导致 chip 计数与筛选结果不符）。
  const actionableRequirements = useMemo(
    () => requirements.filter((r) => r.reviewRelevance === 'actionable'),
    [requirements],
  )
  const scoringFocusSummary = useMemo(
    () => buildScoringFocusSummary(actionableRequirements),
    [actionableRequirements],
  )

  /** 展开含评分核对项的分类分组（「全部」默认折叠时保证筛选结果可见）。 */
  const expandScoringFocusGroups = useCallback(() => {
    setExpandedGroups(new Set(getScoringFocusGroupLabels(actionableRequirements)))
  }, [actionableRequirements])

/**
 * 筛选栏「评分项/重点」toggle：开启时同步展开目标分组，
 * 关闭时只关筛选；不改动分类/否决项正交叠加。
 */
  const toggleScoringFocusOnly = useCallback(() => {
    if (!showScoringFocusOnly) {
      setShowScoringFocusOnly(true)
      expandScoringFocusGroups()
      return
    }
    setShowScoringFocusOnly(false)
  }, [showScoringFocusOnly, expandScoringFocusGroups])

/**
 * 一键聚焦评分项：复用既有筛选/折叠状态而非直接操作 DOM。
 * 清空分类与否决项筛选避免与评分项筛选叠加后出现空列表，
 * 展开含评分项的分类分组，最后滚动到主列表卡片。
 */
  const focusScoringItems = useCallback(() => {
    setActiveTab('all')
    setShowMandatoryOnly(false)
    setShowScoringFocusOnly(true)
    expandScoringFocusGroups()
    window.requestAnimationFrame(() => {
      document.getElementById('requirement-review-table-card')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    })
  }, [expandScoringFocusGroups])

  const refreshProjectStatus = useCallback(async () => {
    if (!projectId) return ''
    const project = await projectService.getProject(parseInt(projectId))
    const status = (project as { status?: string }).status || ''
    setProjectStatus(status)
    return status
  }, [projectId])

  // 检查项目状态
  useEffect(() => {
    const checkProjectStatus = async () => {
      if (!projectId) return
      try {
        await refreshProjectStatus()
      } catch (err) {
        console.error('获取项目状态失败:', err)
      }
    }
    checkProjectStatus()
  }, [projectId, refreshProjectStatus])

  const refreshScoringTotalsReport = useCallback(async () => {
    if (!projectId) return
    const response = await requirementService.getRequirements(parseInt(projectId))
    setScoringTotalsReport(
      transformScoringTotalsReport(
        (response as { scoring_totals_report?: unknown }).scoring_totals_report,
      ),
    )
    setScoringLedgerReport(
      transformScoringLedgerReport(
        (response as { scoring_ledger_report?: unknown }).scoring_ledger_report,
      ),
    )
    setScoringVerdict(
      transformScoringVerdict((response as { scoring_verdict?: unknown }).scoring_verdict),
    )
  }, [projectId])

  const loadRequirements = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)

    const readiness = await refreshDocumentReadiness()

    try {
      const [response, extractStatus, project] = await Promise.all([
        requirementService.getRequirements(parseInt(projectId)),
        requirementService
          .getExtractStatus(parseInt(projectId))
          .catch(() => null),
        projectService.getProject(parseInt(projectId)).catch(() => null),
      ])
      const items = (response as { items?: Record<string, unknown>[] }).items || []
      const loadedProjectStatus = (project as { status?: string } | null)?.status || ''
      if (loadedProjectStatus) setProjectStatus(loadedProjectStatus)

      if (shouldHoldExtractionProgressView(extractStatus?.status, loadedProjectStatus)) {
        await startExtractionIfNeeded()
        return
      }

      setScoringTotalsReport(
        transformScoringTotalsReport(
          (response as { scoring_totals_report?: unknown }).scoring_totals_report,
        ),
      )
      setScoringLedgerReport(
        transformScoringLedgerReport(
          (response as { scoring_ledger_report?: unknown }).scoring_ledger_report,
        ),
      )
      setScoringVerdict(
        transformScoringVerdict((response as { scoring_verdict?: unknown }).scoring_verdict),
      )

      if (items.length === 0) {
        // 招标或投标文档未就绪时，不触发提取
        if (readiness.tenderDocReady === false || readiness.bidDocReady === false) {
          setLoading(false)
          return
        }

        // 委派给 useRequirementExtractionPolling 处理自启动 + 轮询 + 完成回填
        await startExtractionIfNeeded()
      } else {
        setRequirements(items.map(transformRequirement))
      }
    } catch (err) {
      setError(`加载招标要求失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setLoading(false)
    }
  }, [projectId, refreshDocumentReadiness, startExtractionIfNeeded])

  useEffect(() => {
    loadRequirements()
  }, [loadRequirements])

  // 删除要求：仅打开确认弹窗，实际删除由 confirmDelete 触发 hook.deleteRequirement
  const handleDeleteRequirement = (id: number) => {
    setDeletingId(id)
  }

  // 确认删除：保留主页面 deletingId 控制态 + 委派 hook
  const confirmDelete = async () => {
    if (!deletingId) return
    const targetId = deletingId
    setDeletingId(null)
    await deleteRequirement(targetId)
  }

  // 取消删除
  const cancelDelete = () => {
    setDeletingId(null)
  }

  const handleReextractRequirements = async () => {
    if (!projectId) return

    setReextracting(true)
    setError(null)

    try {
      const started = await requirementService.reextractRequirements(parseInt(projectId))
      // 重新提取成功启动即解除阻断（用户也可能绕过门禁卡从对话框重新发起）
      reextractGate.clear()
      setShowReextractDialog(false)
      setRequirements([])
      setScoringTotalsReport(null)
      setScoringLedgerReport(null)
      setScoringVerdict(null)
      setProjectStatus('extracting')
      toast.success('已清空旧确认清单，重新提取任务已启动')
      const identity =
        typeof started?.run_id === 'string' && started.run_id
          ? {
              runId: started.run_id,
              revision: Number.isFinite(Number(started.revision))
                ? Number(started.revision)
                : 0,
            }
          : null
      await startExtractionForRun(identity)
      await refreshProjectStatus().catch(() => setProjectStatus('extracted'))
    } catch (err) {
      // 余额不足走门禁卡（当前清单保留可见），并关闭确认弹窗避免与充值弹窗叠层
      if (reextractGate.capture(err)) {
        setShowReextractDialog(false)
        return
      }
      toast.error(`重新提取失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setReextracting(false)
    }
  }

  const resetAddDialogState = () => {
    setAddMode('requirement')
    setNewCategory('TECH')
    setNewDescription('')
    setNewIsMandatory(false)
    setNewScoreItemName('')
    setNewScoreWeight('')
    setNewScoreRules('')
    setNewDeductionRules('')
    setNewVolume('')
    setNewVolumeLabel('')
    setNewLot('')
    setNewSourcePages('')
  }

  const openAddDialog = (mode: 'requirement' | 'scoring_item' = 'requirement') => {
    setAddMode(mode)
    setShowAddModal(true)
  }

  // 手动添加招标要求 / 评分项 wrapper
  const handleCreateRequirement = async () => {
    if (!newDescription.trim()) return
    setAdding(true)
    const created = addMode === 'scoring_item'
      ? await createScoringItem({
          category: newCategory,
          score_item_name: newScoreItemName.trim(),
          description: newDescription.trim(),
          score_weight: Number(newScoreWeight),
          score_rules: splitMultilineInput(newScoreRules),
          deduction_rules: splitMultilineInput(newDeductionRules),
          volume: newVolume.trim() || null,
          volume_label: newVolumeLabel.trim() || null,
          lot: newLot.trim() || null,
          source_pages: parseSourcePagesInput(newSourcePages),
        })
      : await createRequirement({
          category: newCategory,
          description: newDescription.trim(),
          is_mandatory: newIsMandatory,
        })
    if (created) {
      setShowAddModal(false)
      resetAddDialogState()
      if (addMode === 'scoring_item') {
        await refreshScoringTotalsReport()
      }
    }
    setAdding(false)
  }

  // 确认所有要求并开始审查
  const handleConfirmAll = async () => {
    if (!projectId) return
    
    setConfirming(true)
    setError(null)
    
    try {
      // 1. 确认所有招标要求
      await requirementService.confirmAllRequirements(parseInt(projectId))
      
      // 2. 启动AI审查（成功启动后先解除余额门禁再跳转，避免返回本页残留阻断卡）
      const startResponse = await reviewService.startReview(parseInt(projectId))
      startGate.clear()
      const startStatus = startResponse.data?.status
      if (startStatus === 'completed') {
        setProjectStatus('completed')
        navigate(`/project/${projectId}/result`)
        return
      }
      setProjectStatus('reviewing')
      
      // 3. 跳转到进度页面
      navigate(`/project/${projectId}/progress`)
    } catch (err) {
      // 余额不足由 startGate 接管（阻断卡+去重 toast+充值弹窗），其余保持原 toast
      if (startGate.capture(err)) return
      toast.error(`确认失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setConfirming(false)
    }
  }

  /** 有风险告知且本会话未查看时弹窗；否则直接开始审查 */
  const openStartReviewConfirm = () => {
    const folded = requirements.filter((r) => r.reviewRelevance !== 'actionable')
    const { riskItems } = splitFoldedRequirements(folded)
    const summary = buildRiskNoticeSummary(riskItems)
    if (summary.hasRisk && !riskNoticeViewed) {
      setShowStartReviewConfirm(true)
      return
    }
    void handleConfirmAll()
  }

  if (loading || extracting) {
    return (
      <ExtractionProgressPanel
        extracting={extracting}
        steps={extractionSteps}
        displayProgress={extractionDisplayProgress}
        stageText={extractionStageText}
        isInitialLoading={extractionInitial}
        error={error}
      />
    )
  }

  // 首次提取被余额阻断且尚无清单：整页替换为完成语义门禁卡，不渲染红色错误面板
  if (extractGate.blocked && requirements.length === 0) {
    return (
      <div className="max-w-2xl mx-auto bg-white rounded-lg shadow-sm p-8">
        <BalanceGateCard
          layout="center"
          semantic="completed"
          copy={getBalanceGateCopy('requirement_extract')}
          message={extractGate.blocked.message}
          paymentEnabled={isPaymentEnabled}
          continueBusy={extracting}
          onRecharge={extractGate.openRecharge}
          onContinue={() => {
            extractGate.clear()
            void startExtractionIfNeeded()
          }}
        />
      </div>
    )
  }

  return (
    <div>
      <DeleteConfirmDialog
        open={!!deletingId}
        onCancel={cancelDelete}
        onConfirm={confirmDelete}
      />

      <FirstVisitGuideDialog open={showGuideTip} onDismiss={dismissGuideTip} />

      <ReextractConfirmDialog
        open={showReextractDialog}
        reextracting={reextracting}
        onCancel={() => setShowReextractDialog(false)}
        onConfirm={handleReextractRequirements}
      />

      <ConfirmStartReviewDialog
        open={showStartReviewConfirm}
        confirming={confirming}
        riskCount={
          buildRiskNoticeSummary(
            splitFoldedRequirements(
              requirements.filter((r) => r.reviewRelevance !== 'actionable'),
            ).riskItems,
          ).total
        }
        onReturnToView={() => {
          setShowStartReviewConfirm(false)
          setRiskExpandRequest(true)
        }}
        onContinue={() => {
          setShowStartReviewConfirm(false)
          void handleConfirmAll()
        }}
        onDismiss={() => setShowStartReviewConfirm(false)}
      />

      <RequirementAddDialog
        open={showAddModal}
        onOpenChange={setShowAddModal}
        mode={addMode}
        onModeChange={setAddMode}
        category={newCategory}
        description={newDescription}
        isMandatory={newIsMandatory}
        scoreItemName={newScoreItemName}
        scoreWeight={newScoreWeight}
        scoreRules={newScoreRules}
        deductionRules={newDeductionRules}
        volume={newVolume}
        volumeLabel={newVolumeLabel}
        lot={newLot}
        sourcePages={newSourcePages}
        adding={adding}
        onCategoryChange={setNewCategory}
        onDescriptionChange={setNewDescription}
        onMandatoryToggle={() => setNewIsMandatory((v) => !v)}
        onScoreItemNameChange={setNewScoreItemName}
        onScoreWeightChange={setNewScoreWeight}
        onScoreRulesChange={setNewScoreRules}
        onDeductionRulesChange={setNewDeductionRules}
        onVolumeChange={setNewVolume}
        onVolumeLabelChange={setNewVolumeLabel}
        onLotChange={setNewLot}
        onSourcePagesChange={setNewSourcePages}
        onSubmit={handleCreateRequirement}
      />

      <RequirementHeader
        primaryAction={primaryAction}
        requirementsCount={requirements.length}
        confirming={confirming}
        reextracting={reextracting}
        tenderDocReady={tenderDocReady}
        bidDocReady={bidDocReady}
        tenderDocMessage={tenderDocMessage}
        bidDocMessage={bidDocMessage}
        onBack={() => navigate(`/project/${projectId}/upload`)}
        onOpenAddDialog={() => openAddDialog('requirement')}
        onOpenReextractDialog={() => setShowReextractDialog(true)}
        onGoToPrimaryAction={() => {
          if (primaryAction.targetPath) navigate(primaryAction.targetPath)
        }}
        onConfirmAll={openStartReviewConfirm}
        knowledgeSlot={
          projectId ? (
            <KnowledgeUsagePanel
              projectId={parseInt(projectId)}
              flow="bid_review"
              readOnly={projectStatus === 'completed'}
            />
          ) : null
        }
      />

      <DocumentNotReadyBanner
        visible={tenderDocReady === false}
        message={tenderDocMessage}
        onNavigateUpload={() => navigate(`/project/${projectId}/upload`)}
      />

      <DocumentNotReadyBanner
        visible={bidDocReady === false}
        message={bidDocMessage}
        onNavigateUpload={() => navigate(`/project/${projectId}/upload`)}
      />

      {error && (
        <div className="mb-4 p-4 bg-red-50 rounded-lg border border-red-200">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}
      {startGate.blocked && (
        <BalanceGateCard
          layout="inline"
          semantic="completed"
          copy={getBalanceGateCopy('review_start')}
          message={startGate.blocked.message}
          paymentEnabled={isPaymentEnabled}
          continueBusy={confirming}
          onRecharge={startGate.openRecharge}
          onContinue={() => { void handleConfirmAll() }}
        />
      )}

      <ExtractionWarningBanner displays={extractionWarningDisplays} />

      <ScoringTotalsBanner
        report={scoringTotalsReport}
        ledgerReport={scoringLedgerReport}
        verdict={scoringVerdict}
        focusSummary={scoringFocusSummary}
        onAddScoringItem={() => openAddDialog('scoring_item')}
        onFocusScoringItems={focusScoringItems}
      />

      {reextractGate.blocked && (
        <BalanceGateCard
          layout="inline"
          semantic="retry"
          copy={getBalanceGateCopy('requirement_reextract')}
          message={reextractGate.blocked.message}
          paymentEnabled={isPaymentEnabled}
          continueBusy={reextracting}
          onRecharge={reextractGate.openRecharge}
          onContinue={() => {
            reextractGate.clear()
            void handleReextractRequirements()
          }}
        />
      )}

      {(() => {
        if (requirements.length === 0) {
          return (
            <div className="bg-white rounded-lg shadow-sm">
              <EmptyState
                variant="document"
                title={
                  tenderDocReady === false || bidDocReady === false
                    ? '无法提取招标要求'
                    : '未提取到招标要求'
                }
                description={
                  tenderDocReady === false
                    ? tenderDocMessage
                    : bidDocReady === false
                      ? bidDocMessage
                      : '请确保招标文件已上传并处理完成'
                }
                action={
                  tenderDocReady === false || bidDocReady === false
                    ? {
                        label: '返回上传',
                        onClick: () => navigate(`/project/${projectId}/upload`),
                      }
                    : { label: '重新加载', onClick: loadRequirements }
                }
              />
            </div>
          )
        }

        // 派生数据：actionable 进入主审查列表（复用组件级 memo，避免与评分聚合口径分叉），
        // informational/procedural 折叠为参考条款（P1）
        const foldedRequirements = requirements.filter(
          (r) => r.reviewRelevance !== 'actionable',
        )
        const { riskItems, referenceItems } = splitFoldedRequirements(foldedRequirements)
        const referenceCountsByCategory = countReferenceByCategory(referenceItems)
        const sortedAll = sortRequirements(actionableRequirements)
        const mandatoryCount = actionableRequirements.filter((r) => r.isMandatory).length
        const uniqueCategoryLabels = Array.from(
          new Set(sortedAll.map((r) => getCategoryLabel(r.category))),
        )
        const categoryCounts: Record<string, number> = {}
        for (const label of uniqueCategoryLabels) {
          categoryCounts[label] = actionableRequirements.filter(
            (r) => getCategoryLabel(r.category) === label,
          ).length
        }
        const filteredList = sortedAll.filter((r) => {
          if (showMandatoryOnly && !r.isMandatory) return false
          if (showScoringFocusOnly && !isScoringFocusItem(r)) return false
          if (activeTab !== 'all' && getCategoryLabel(r.category) !== activeTab) return false
          return true
        })
        const groupedMap = new Map<string, Requirement[]>()
        if (activeTab === 'all') {
          filteredList.forEach((r) => {
            const label = getCategoryLabel(r.category)
            if (!groupedMap.has(label)) groupedMap.set(label, [])
            groupedMap.get(label)!.push(r)
          })
        }
        const onToggleGroup = (label: string) => {
          setExpandedGroups((prev) => {
            const next = new Set(prev)
            if (next.has(label)) next.delete(label)
            else next.add(label)
            return next
          })
        }
        /** 半展开亦走收起：一键回到默认全折叠，且不改动任何筛选态。 */
        const toggleExpandCollapseGroups = () => {
          if (expandedGroups.size > 0) {
            setExpandedGroups(new Set())
            return
          }
          setExpandedGroups(new Set(groupedMap.keys()))
        }

        return (
          <>
            <div id="requirement-review-table-card" className="bg-white rounded-lg shadow-sm scroll-mt-4">
              <RequirementFilterBar
                totalCount={actionableRequirements.length}
                mandatoryCount={mandatoryCount}
                scoringFocusCount={scoringFocusSummary.totalCount}
                uniqueCategoryLabels={uniqueCategoryLabels}
                categoryCounts={categoryCounts}
                activeTab={activeTab}
                showMandatoryOnly={showMandatoryOnly}
                showScoringFocusOnly={showScoringFocusOnly}
                onTabChange={setActiveTab}
                onToggleMandatoryOnly={() => setShowMandatoryOnly((v) => !v)}
                onToggleScoringFocusOnly={toggleScoringFocusOnly}
                anyGroupExpanded={expandedGroups.size > 0}
                expandCollapseVisible={activeTab === 'all' && groupedMap.size >= 2}
                onToggleExpandCollapse={toggleExpandCollapseGroups}
              />
              <RequirementTable
                filteredList={filteredList}
                groupedMap={groupedMap}
                activeTab={activeTab}
                expandedGroups={expandedGroups}
                onToggleGroup={onToggleGroup}
                tenderDocId={tenderDocId}
                onOpenPdfPreview={handleOpenPdfPreview}
                onShowAllPages={setShowAllPagesReq}
                onToggleMandatory={toggleMandatory}
                onUpdateDescription={updateDescription}
                onDelete={handleDeleteRequirement}
                onDemote={demoteToReference}
                onUpdateScoringWeight={updateScoringWeight}
                referenceCountsByCategory={referenceCountsByCategory}
                onJumpToReference={setJumpToReferenceCategory}
              />
            </div>
            <RiskNoticeSection
              items={riskItems}
              onViewed={() => setRiskNoticeViewed(true)}
              onPromote={promoteToActionable}
              onDelete={handleDeleteRequirement}
              tenderDocId={tenderDocId}
              onOpenPdfPreview={handleOpenPdfPreview}
              onShowAllPages={setShowAllPagesReq}
              expandRequest={riskExpandRequest}
              onExpandRequestConsumed={() => setRiskExpandRequest(false)}
            />
            <FoldedRequirementsSection
              items={referenceItems}
              onPromote={promoteToActionable}
              onDelete={handleDeleteRequirement}
              tenderDocId={tenderDocId}
              onOpenPdfPreview={handleOpenPdfPreview}
              onShowAllPages={setShowAllPagesReq}
              jumpToCategory={jumpToReferenceCategory}
              onJumpConsumed={() => setJumpToReferenceCategory(null)}
            />
          </>
        )
      })()}

      <AllSourcePagesDialog
        requirement={showAllPagesReq}
        onClose={() => setShowAllPagesReq(null)}
        tenderDocId={tenderDocId}
        onOpenPage={handleOpenPdfPreview}
      />

      <PdfPreviewDialog
        open={pdfOpen}
        onOpenChange={setPdfOpen}
        pdfUrl={pdfUrl}
        initialPage={pdfPage}
        accessToken={accessToken}
        excerpts={pdfExcerpts}
      />
    </div>
  )
}

export default RequirementReviewPage
