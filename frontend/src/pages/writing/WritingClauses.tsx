import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  ArrowLeft, ArrowRight, Ban, Check, FileText, Pencil, Loader2, MoreHorizontal, Plus, Trash2, AlertTriangle,
} from 'lucide-react'
import { useWritingStore } from '@/stores'
import { useMetricProbe } from '@/hooks/useMetricProbe'
import { isPaymentEnabled } from '@/config/features'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { documentService } from '@/services/documentApi'
import { writingService } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import {
  getClauseJumpPage,
  getClausePageLabel,
  getClausePageText,
  isMultiPageClause,
} from '@/services/writingClauseTransforms'
import { useWritingGuard } from './useWritingGuard'
import { isAtLeast } from './writingConstants'
import WritingStepBar from './WritingStepBar'
import PdfEvidencePreviewHost from '../result/pdfPreview/PdfEvidencePreviewHost'
import { usePdfEvidencePreview } from '../result/pdfPreview/usePdfEvidencePreview'
import SourcePageBadge from '@/components/SourcePageBadge'
import RichTextContent from '@/components/RichTextContent'
import {
  getClauseCreateTarget,
  getClauseLayerDisplayLabel,
  getClauseTabDisplayLabel,
  getVisibleClauseCount,
  getVisibleClausesForTab,
  isVisibleWritingClause,
  LAYER_TABS,
  SUB_TYPE_LABELS,
  SUB_TYPE_OPTIONS_BY_LAYER,
} from './writingClauseTabs'
import {
  buildClauseDisplayGroups,
  collectGroupUnconfirmedIds,
  countGroupConfirmed,
  isCheckOnlyWritingClause,
  isReferenceWritingClause,
  SCORING_ASSESSMENT_GROUP_TITLE,
  type ClauseDisplayGroup,
} from './writingClauseGrouping'
import WritingClauseGroupSection from './WritingClauseGroupSection'
import WritingScoringTotalsBanner from './WritingScoringTotalsBanner'
import { getScoringRefLabel } from './writingScoringLink'
import {
  transformScoringTotalsReport,
  type ScoringTotalsReport,
} from '../requirementReviewTransforms'
import {
  hasRichClauseMarkup,
  shouldApplyClauseHeadingNumbers,
  toUserFacingClauseText,
} from './writingClauseDisplay'

interface ClauseFormState {
  layer: string
  subType: string
  content: string
  scoreWeight: string
  insertAfterId: number | null
}

function buildClauseFormState(layer: string, subType?: string, insertAfterId: number | null = null): ClauseFormState {
  const fallbackSubType = SUB_TYPE_OPTIONS_BY_LAYER[layer]?.[0] || 'content_required'
  return {
    layer,
    subType: subType || fallbackSubType,
    content: '',
    scoreWeight: '',
    insertAfterId,
  }
}

function getClausePreviewUrl(projectId: number, clause: WritingClause | null): string {
  if (!clause?.documentId) return ''
  return documentService.getPreviewUrl(projectId, clause.documentId)
}

const WritingClausesPage: React.FC = () => {
  useMetricProbe({ page: 'WritingClauses' })
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const pid = Number(projectId)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(pid, 'extracted')
  // 余额门禁: 目录生成节点被 403 阻断时停留本页保留约束清单 
  const gate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_outline',
    paymentEnabled: isPaymentEnabled,
  })
  const {
    clauses, loading, loadClauses, createClause, confirmClauses, deleteClause, triggerOutlineGenerate,
  } = useWritingStore()
  const [activeTab, setActiveTab] = useState('hard_constraint')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editContent, setEditContent] = useState('')
  const [navigating, setNavigating] = useState(false)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [collapsedOverrides, setCollapsedOverrides] = useState<Record<string, boolean>>({})
  const [confirmingGroupKey, setConfirmingGroupKey] = useState<string | null>(null)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addSubmitting, setAddSubmitting] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<WritingClause | null>(null)
  const [deleteSubmitting, setDeleteSubmitting] = useState(false)
  const [addForm, setAddForm] = useState<ClauseFormState>(() => buildClauseFormState('hard_constraint'))
  const preview = usePdfEvidencePreview()
  const { openPreview } = preview
  const [riskDialogOpen, setRiskDialogOpen] = useState(false)
  const [riskActionLabel, setRiskActionLabel] = useState('')
  const [riskSubmitting, setRiskSubmitting] = useState(false)
  const [scoringReport, setScoringReport] = useState<ScoringTotalsReport | null>(null)
  const handlingRef = useRef(false)
  const confirmAllRef = useRef(false)
  const riskActionRef = useRef<null | (() => Promise<void>)>(null)

  useEffect(() => {
    if (pid) loadClauses(pid)
  }, [pid, loadClauses])

  // 评分项按卷满分核计报告：随约束变化刷新（提取完成/编辑分值后同步核计口径）
  useEffect(() => {
    if (!pid) return
    let cancelled = false
    writingService
      .getScoringReport(pid)
      .then((raw) => {
        if (!cancelled) setScoringReport(transformScoringTotalsReport(raw))
      })
      .catch(() => {
        if (!cancelled) setScoringReport(null)
      })
    return () => {
      cancelled = true
    }
  }, [pid, clauses])

  // 标记/取消"非正文响应"(价格分等由报价/资格文件决定, 不在正文写作范围)。
  // 末端一致性检查据此把该评分项从"未响应失分(high)"降级为"事实告知(info)"。
  const toggleNonBodyResponse = useCallback(async (clause: WritingClause) => {
    const current = Boolean(clause.metadataJson?.non_body_response)
    const merged = { ...(clause.metadataJson || {}), non_body_response: !current }
    try {
      await writingService.updateClause(pid, clause.id, { metadata_json: merged })
      await loadClauses(pid)
      toast.success(current ? '已取消非正文响应标记' : '已标记为非正文响应')
    } catch {
      toast.error('操作失败，请重试')
    }
  }, [pid, loadClauses])

  const filteredClauses = getVisibleClausesForTab(clauses, activeTab)
  const unconfirmed = clauses.filter((c) => c.confirmed === 0 && isVisibleWritingClause(c))
  const shouldWarnDownstreamStale = isAtLeast(projectStatus, 'outlined')

  const executeWithRiskPrompt = useCallback(async (
    actionLabel: string,
    operation: () => Promise<void>,
  ) => {
    if (!shouldWarnDownstreamStale) {
      await operation()
      return
    }
    riskActionRef.current = operation
    setRiskActionLabel(actionLabel)
    setRiskDialogOpen(true)
  }, [shouldWarnDownstreamStale])

  const restoreBodyConsumption = useCallback(async (clause: WritingClause) => {
    const merged = {
      ...(clause.metadataJson || {}),
      writing_relevance: 'direct',
      writing_relevance_source: 'manual',
      writing_relevance_reason: '人工恢复为正文约束',
    }
    await executeWithRiskPrompt('恢复正文约束', async () => {
      try {
        await writingService.updateClause(pid, clause.id, { metadata_json: merged })
        await loadClauses(pid)
        toast.success('已恢复为正文约束，后续匹配将重新消费')
      } catch {
        toast.error('操作失败，请重试')
      }
    })
  }, [executeWithRiskPrompt, loadClauses, pid])

  const handleOpenPdfPreview = useCallback((clause: WritingClause) => {
    const jumpPage = getClauseJumpPage(clause)
    const url = getClausePreviewUrl(pid, clause)
    if (!url || jumpPage == null) return
    const excerpt = (clause.rawText || '').trim()
    openPreview({
      pdfUrl: url,
      page: jumpPage,
      excerpts: excerpt ? [excerpt] : [],
    })
  }, [pid, openPreview])

  const handleConfirmAll = async () => {
    if (confirmAllRef.current) return
    const ids = unconfirmed.map((c) => c.id)
    if (ids.length === 0) {
      toast.info('所有约束已确认')
      return
    }
    await executeWithRiskPrompt('确认全部约束', async () => {
      confirmAllRef.current = true
      setConfirmingAll(true)
      try {
        await confirmClauses(pid, ids)
        toast.success(`已确认 ${ids.length} 条约束，系统正在后台准备匹配数据`)
      } catch {
        toast.error('确认失败，请稍后重试')
      } finally {
        confirmAllRef.current = false
        setConfirmingAll(false)
      }
    })
  }

  const handleConfirmOne = useCallback(async (clauseId: number) => {
    await executeWithRiskPrompt('确认约束', async () => {
      try {
        await confirmClauses(pid, [clauseId])
        toast.success('约束已确认')
      } catch {
        toast.error('确认失败，请稍后重试')
      }
    })
  }, [confirmClauses, executeWithRiskPrompt, pid])

  const toggleGroupCollapsed = useCallback((key: string, defaultCollapsed: boolean) => {
    setCollapsedOverrides((prev) => ({
      ...prev,
      [key]: !(prev[key] ?? defaultCollapsed),
    }))
  }, [])

  const handleConfirmGroup = useCallback(async (group: ClauseDisplayGroup) => {
    const ids = collectGroupUnconfirmedIds(group)
    if (ids.length === 0) {
      toast.info('本组已全部确认')
      return
    }
    await executeWithRiskPrompt('确认本组约束', async () => {
      setConfirmingGroupKey(group.key)
      try {
        await confirmClauses(pid, ids)
        toast.success(`已确认本组 ${ids.length} 条约束`)
      } catch {
        toast.error('确认失败，请稍后重试')
      } finally {
        setConfirmingGroupKey(null)
      }
    })
  }, [confirmClauses, executeWithRiskPrompt, pid])

  const handleNext = async () => {
    if (handlingRef.current) return
    if (unconfirmed.length > 0) {
      toast.warning(`还有 ${unconfirmed.length} 条未确认约束`)
      return
    }
    handlingRef.current = true
    setNavigating(true)
    try {
      const raw = await writingService.getStatus(pid)
      const freshStatus = raw.status || ''

      if (freshStatus === 'outlining' || isAtLeast(freshStatus, 'outlined')) {
        // 节点已越过（其他入口已启动/完成目录生成），同步清除阻断快照
        gate.clear()
        navigate(`/project/${projectId}/writing/outline`)
        return
      }

      await triggerOutlineGenerate(pid)
      gate.clear()
      navigate(`/project/${projectId}/writing/outline`)
    } catch (err) {
      // 余额不足: 停留本页保留约束清单，由门禁卡片引导充值后继续 
      if (gate.capture(err)) return
      toast.error('启动目录生成失败')
    } finally {
      handlingRef.current = false
      setNavigating(false)
    }
  }

  const startEdit = useCallback((clause: WritingClause) => {
    setEditingId(clause.id)
    setEditContent(clause.content)
  }, [])

  const openAddDialog = useCallback((layer: string, subType?: string, insertAfterId: number | null = null) => {
    const target = getClauseCreateTarget(layer, subType)
    setAddForm(buildClauseFormState(target.layer, target.subType, insertAfterId))
    setAddDialogOpen(true)
  }, [])

  const cancelEdit = useCallback(() => {
    setEditingId(null)
    setEditContent('')
  }, [])

  const saveEdit = useCallback(async () => {
    if (!editingId || !editContent.trim()) return
    const payload: Record<string, unknown> = { content: editContent.trim() }
    const operation = async () => {
      try {
        await writingService.updateClause(pid, editingId, payload)
        await loadClauses(pid)
        toast.success('约束已更新')
        cancelEdit()
      } catch {
        toast.error('更新失败')
      }
    }
    await executeWithRiskPrompt('编辑约束', operation)
  }, [pid, editingId, editContent, loadClauses, cancelEdit, executeWithRiskPrompt])

  const handleCreateClause = useCallback(async () => {
    if (!addForm.content.trim()) return
    const operation = async () => {
      setAddSubmitting(true)
      try {
        await createClause(pid, {
          layer: addForm.layer,
          sub_type: addForm.subType,
          content: addForm.content.trim(),
          score_weight: addForm.scoreWeight ? Number(addForm.scoreWeight) : undefined,
          insert_after_id: addForm.insertAfterId ?? undefined,
        })
        toast.success('约束已添加，请确认后再进入下一步')
        setAddDialogOpen(false)
      } catch {
        toast.error('添加失败')
      } finally {
        setAddSubmitting(false)
      }
    }
    await executeWithRiskPrompt('新增约束', operation)
  }, [addForm, createClause, executeWithRiskPrompt, pid])

  const handleDeleteClause = useCallback(async () => {
    if (!deleteTarget) return
    const target = deleteTarget
    const operation = async () => {
      setDeleteSubmitting(true)
      try {
        await deleteClause(pid, target.id)
        toast.success('约束已删除')
        if (editingId === target.id) cancelEdit()
        setDeleteTarget(null)
      } catch {
        toast.error('删除失败')
      } finally {
        setDeleteSubmitting(false)
      }
    }
    await executeWithRiskPrompt('删除约束', operation)
  }, [deleteTarget, deleteClause, pid, editingId, cancelEdit, executeWithRiskPrompt])

  const handleConfirmRisk = useCallback(async () => {
    const action = riskActionRef.current
    if (!action) {
      setRiskDialogOpen(false)
      return
    }
    setRiskSubmitting(true)
    try {
      await action()
      setRiskDialogOpen(false)
      riskActionRef.current = null
    } finally {
      setRiskSubmitting(false)
    }
  }, [])

  if (guardLoading || !ready) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={1} projectStatus={projectStatus} />
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <Button variant="ghost" size="sm" onClick={() => navigate(`/project/${projectId}/writing/upload?from=clauses`)}>
                <ArrowLeft className="h-4 w-4 mr-1" />
                返回
              </Button>
              <h1 className="text-xl font-semibold">约束确认</h1>
              <Badge variant="secondary">{getVisibleClauseCount(clauses)} 条</Badge>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => openAddDialog(activeTab)}
              >
                <Plus className="h-4 w-4 mr-1" />
                添加约束
              </Button>
              <Button variant="outline" onClick={handleConfirmAll} disabled={confirmingAll || unconfirmed.length === 0}>
                {confirmingAll ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Check className="h-4 w-4 mr-1" />}
                {confirmingAll
                  ? '确认中...'
                  : unconfirmed.length > 0
                    ? `全部确认 (${unconfirmed.length}条待确认)`
                    : '已全部确认'}
              </Button>
              <Button
                onClick={handleNext}
                disabled={navigating || unconfirmed.length > 0}
                title={unconfirmed.length > 0 ? `还有 ${unconfirmed.length} 条约束未确认` : ''}
              >
                {navigating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : null}
                {navigating ? '启动中...' : '下一步'}
                {!navigating && <ArrowRight className="h-4 w-4 ml-1" />}
              </Button>
            </div>
          </div>

      <div className="max-w-4xl mx-auto">
          {gate.isBlocked && (
            <BalanceGateCard
              layout="inline"
              semantic="completed"
              copy={getBalanceGateCopy('writing_outline')}
              message={gate.blocked?.message ?? ''}
              paymentEnabled={isPaymentEnabled}
              continueBusy={navigating}
              onRecharge={gate.openRecharge}
              onContinue={() => void handleNext()}
            />
          )}
          {shouldWarnDownstreamStale && (
            <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              当前项目已进入后续阶段。此处继续新增、删除或编辑约束时，不会自动刷新目录、绑定、草稿和预览结果；如需保持一致，请在修改后手动重走后续流程。
            </div>
          )}

          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList>
              {LAYER_TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  {getClauseTabDisplayLabel(clauses, tab)}
                </TabsTrigger>
              ))}
            </TabsList>

            {LAYER_TABS.map((tab) => (
              <TabsContent key={tab.value} value={tab.value} className="mt-4 pb-10">
                {tab.value === 'generation_guide' && (
                  <WritingScoringTotalsBanner
                    report={scoringReport}
                    onAddScoringItem={() => openAddDialog('generation_guide', 'scoring_criterion')}
                  />
                )}
                {loading ? (
                  <div className="text-center py-12 text-muted-foreground">加载中...</div>
                ) : filteredClauses.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    暂无{tab.label}
                  </div>
                ) : (
                  <div className="space-y-5">
                    {buildClauseDisplayGroups(filteredClauses).map((group) => (
                      <WritingClauseGroupSection
                        key={group.key}
                        title={group.titleOverride || SUB_TYPE_LABELS[group.subType] || group.subType}
                        total={group.clauses.length}
                        confirmedCount={countGroupConfirmed(group)}
                        collapsed={collapsedOverrides[group.key] ?? group.collapsedByDefault}
                        onToggleCollapsed={() => toggleGroupCollapsed(group.key, group.collapsedByDefault)}
                        onConfirmGroup={() => handleConfirmGroup(group)}
                        confirming={confirmingGroupKey === group.key}
                        substantive={group.isSubstantive}
                        hideHeader={group.subType === 'industry_profile'}
                      >
                        {group.clauses.map((clause) => {
                          const clauseDisplayText = toUserFacingClauseText(clause.content)
                          return (
                            <div
                              key={clause.id}
                              className={`bg-white rounded-lg border p-4 transition-colors hover:border-primary/40 ${
                                clause.confirmed ? 'border-green-200 bg-green-50/30' : ''
                              }`}
                            >
                              <div className="space-y-3">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                                    <Badge variant="outline" className="text-xs">
                                      {SUB_TYPE_LABELS[clause.subType] || clause.subType}
                                    </Badge>
                                    {clause.isManual === 1 && (
                                      <Badge variant="secondary" className="text-xs">
                                        人工新增
                                      </Badge>
                                    )}
                                    {clause.scoreWeight != null && (
                                      <Badge
                                        variant="secondary"
                                        className="border border-amber-300 bg-amber-100 text-xs font-bold text-amber-900 shadow-xs ring-1 ring-amber-200/70 hover:bg-amber-100"
                                      >
                                        {clause.scoreWeight}分
                                      </Badge>
                                    )}
                                    {clause.metadataJson?.suspected_assessment_form === true && (
                                      <Badge
                                        variant="secondary"
                                        className="text-xs bg-orange-50 text-orange-800 border border-orange-200"
                                      >
                                        {SCORING_ASSESSMENT_GROUP_TITLE}
                                      </Badge>
                                    )}
                                    {(() => {
                                      const linkLabel = getScoringRefLabel(clause)
                                      if (!linkLabel) return null
                                      return (
                                        <Badge
                                          variant="secondary"
                                          className="text-xs bg-teal-50 text-teal-700 border border-teal-200"
                                        >
                                          {linkLabel}
                                        </Badge>
                                      )
                                    })()}
                                    {clause.subType === 'scoring_criterion'
                                      && Boolean(clause.metadataJson?.non_body_response) && (
                                      <Badge
                                        variant="secondary"
                                        className="text-xs bg-slate-100 text-slate-600 border border-slate-200"
                                      >
                                        非正文响应
                                      </Badge>
                                    )}
                                    {isReferenceWritingClause(clause) && (
                                      <Badge
                                        variant="secondary"
                                        className="text-xs bg-blue-50 text-blue-700 border border-blue-200"
                                      >
                                        参考内容
                                      </Badge>
                                    )}
                                    {isCheckOnlyWritingClause(clause) && (
                                      <Badge
                                        variant="secondary"
                                        className="text-xs bg-amber-50 text-amber-700 border border-amber-200"
                                      >
                                        检查型红线
                                      </Badge>
                                    )}
                                    {(() => {
                                      const pageLabel = getClausePageLabel(clause)
                                      if (!pageLabel) return null
                                      const pageText = getClausePageText(clause)
                                      const multi = isMultiPageClause(clause)
                                      const tip = multi
                                        ? `该条款定位到${pageText}范围，点击从起始页打开并在该范围内核对原文`
                                        : `查看${pageText}原文`
                                      return (
                                        <SourcePageBadge
                                          label={pageLabel}
                                          onClick={clause.documentId
                                            ? (e) => { e.stopPropagation(); handleOpenPdfPreview(clause) }
                                            : undefined}
                                          title={clause.documentId ? tip : '原始文档未就绪，无法跳转预览'}
                                        />
                                      )
                                    })()}
                                    {clause.confirmed === 1 && (
                                      <Badge className="bg-green-100 text-green-800 text-xs">
                                        已确认
                                      </Badge>
                                    )}
                                  </div>

                                  <div className="flex items-center justify-end gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                                    {editingId !== clause.id && (
                                      <>
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          className="h-8 px-2.5 text-xs"
                                          onClick={() => startEdit(clause)}
                                          title="编辑"
                                        >
                                          <Pencil className="h-3.5 w-3.5" />
                                          编辑
                                        </Button>
                                        {clause.confirmed === 0 && (
                                          <Button
                                            size="sm"
                                            variant="outline"
                                            className="h-8 px-2.5 text-xs"
                                            onClick={() => handleConfirmOne(clause.id)}
                                            title="确认"
                                          >
                                            <Check className="h-3.5 w-3.5" />
                                            确认
                                          </Button>
                                        )}
                                        <DropdownMenu>
                                          <DropdownMenuTrigger asChild>
                                            <Button
                                              size="sm"
                                              variant="ghost"
                                              className="h-8 px-2"
                                              aria-label={`更多操作：${clauseDisplayText.slice(0, 24)}`}
                                            >
                                              <MoreHorizontal className="h-3.5 w-3.5" />
                                              <span className="sr-only">更多操作</span>
                                            </Button>
                                          </DropdownMenuTrigger>
                                          <DropdownMenuContent align="end" className="w-48">
                                            <DropdownMenuItem
                                              className="cursor-pointer"
                                              onClick={() => openAddDialog(clause.layer, clause.subType, clause.id)}
                                            >
                                              <Plus className="h-4 w-4" />
                                              在此后新增
                                            </DropdownMenuItem>
                                            {clause.subType === 'scoring_criterion' && (
                                              <DropdownMenuItem
                                                className="cursor-pointer"
                                                onClick={() => toggleNonBodyResponse(clause)}
                                              >
                                                <Ban className="h-4 w-4" />
                                                {clause.metadataJson?.non_body_response
                                                  ? '取消非正文响应'
                                                  : '标记为非正文响应'}
                                              </DropdownMenuItem>
                                            )}
                                            {(isReferenceWritingClause(clause) || isCheckOnlyWritingClause(clause)) && (
                                              <DropdownMenuItem
                                                className="cursor-pointer"
                                                onClick={() => restoreBodyConsumption(clause)}
                                              >
                                                <ArrowRight className="h-4 w-4" />
                                                恢复为正文约束
                                              </DropdownMenuItem>
                                            )}
                                            <DropdownMenuSeparator />
                                            <DropdownMenuItem
                                              className="cursor-pointer text-destructive focus:text-destructive"
                                              onClick={() => setDeleteTarget(clause)}
                                            >
                                              <Trash2 className="h-4 w-4" />
                                              删除
                                            </DropdownMenuItem>
                                          </DropdownMenuContent>
                                        </DropdownMenu>
                                      </>
                                    )}
                                  </div>
                                </div>

                                <div className="min-w-0">
                                  {editingId === clause.id ? (
                                    <div className="space-y-2">
                                      <Input
                                        value={editContent}
                                        onChange={(e) => setEditContent(e.target.value)}
                                        onKeyDown={(e) => {
                                          if (e.key === 'Enter') saveEdit()
                                          if (e.key === 'Escape') cancelEdit()
                                        }}
                                        autoFocus
                                      />
                                      <div className="flex gap-2">
                                        <Button size="sm" onClick={saveEdit}>保存</Button>
                                        <Button size="sm" variant="ghost" onClick={cancelEdit}>取消</Button>
                                      </div>
                                    </div>
                                  ) : hasRichClauseMarkup(clauseDisplayText) ? (
                                    <RichTextContent
                                      text={clauseDisplayText}
                                      numbering={
                                        group.subType === 'industry_profile'
                                          ? (shouldApplyClauseHeadingNumbers(clauseDisplayText) ? 'cn' : undefined)
                                          : 'cn'
                                      }
                                    />
                                  ) : (
                                    <p className="text-sm leading-6">{clauseDisplayText}</p>
                                  )}
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </WritingClauseGroupSection>
                    ))}
                  </div>
                )}
              </TabsContent>
            ))}
          </Tabs>
      </div>

      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>添加约束</DialogTitle>
            <DialogDescription>
              手动补充 AI 未提取到的约束条目。新增条目默认为待确认状态，确认后才会进入后续流程。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <p className="text-sm font-medium mb-2">所属层级</p>
              <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
                {getClauseLayerDisplayLabel(addForm.layer, addForm.subType)}
              </div>
            </div>
            <div>
              <p className="text-sm font-medium mb-2">约束分类</p>
              <Select
                value={addForm.subType}
                onValueChange={(value) => setAddForm((prev) => ({
                  ...prev,
                  subType: value,
                  scoreWeight: value === 'scoring_criterion' ? prev.scoreWeight : '',
                }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="请选择分类" />
                </SelectTrigger>
                <SelectContent>
                  {(SUB_TYPE_OPTIONS_BY_LAYER[addForm.layer] || []).map((value) => (
                    <SelectItem key={value} value={value}>
                      {SUB_TYPE_LABELS[value] || value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <p className="text-sm font-medium mb-2">结构化内容</p>
              <textarea
                value={addForm.content}
                onChange={(e) => setAddForm((prev) => ({ ...prev, content: e.target.value }))}
                rows={4}
                placeholder="请输入约束内容"
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs focus:outline-hidden focus:ring-1 focus:ring-ring resize-y"
              />
            </div>
            {addForm.subType === 'scoring_criterion' && (
              <div>
                <p className="text-sm font-medium mb-2">评分权重</p>
                <Input
                  type="number"
                  min="0"
                  step="0.1"
                  value={addForm.scoreWeight}
                  onChange={(e) => setAddForm((prev) => ({ ...prev, scoreWeight: e.target.value }))}
                  placeholder="例如 5"
                />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddDialogOpen(false)}>取消</Button>
            <Button onClick={handleCreateClause} disabled={addSubmitting || !addForm.content.trim()}>
              {addSubmitting ? '添加中...' : '确认添加'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <PdfEvidencePreviewHost {...preview.hostProps} />
      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除这条约束吗？删除后会立即从当前约束列表中移除。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-lg border bg-muted/30 p-3 text-sm">
            {toUserFacingClauseText(deleteTarget?.content)}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDeleteClause} disabled={deleteSubmitting}>
              {deleteSubmitting ? '删除中...' : '确认删除'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={riskDialogOpen} onOpenChange={(open) => { if (!open) { setRiskDialogOpen(false); riskActionRef.current = null } }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              后续结果可能已过期
            </DialogTitle>
            <DialogDescription className="leading-relaxed">
              当前项目已进入目录、绑定或写作等后续阶段。继续执行“{riskActionLabel}”后，系统不会自动刷新目录、绑定、草稿和预览结果。
              如需保持前后数据一致，请在本次修改后手动重走后续流程。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setRiskDialogOpen(false); riskActionRef.current = null }}>取消</Button>
            <Button onClick={handleConfirmRisk} disabled={riskSubmitting}>
              {riskSubmitting ? '处理中...' : '继续操作'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default WritingClausesPage
