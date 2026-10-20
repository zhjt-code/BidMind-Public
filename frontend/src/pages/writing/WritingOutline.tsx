import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { ArrowLeft, ArrowRight, RefreshCw, Loader2, Plus, AlertTriangle } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { useFirstVisitDialog } from '@/hooks/useFirstVisitDialog'
import ProgressPanel from '@/components/ProgressPanel'
import { useProgressAnimation, type ProgressStep } from '@/hooks/useProgressAnimation'
import { useWritingStore } from '@/stores'
import { isPaymentEnabled } from '@/config/features'
import BalanceGateCard from '@/components/BalanceGateCard'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { writingService, type OutlineNode } from '@/services/writingApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import { useWritingGuard } from './useWritingGuard'
import WritingStepBar from './WritingStepBar'
import { mapApiProgressSteps, mapStageToProgressSteps, OUTLINE_STEPS } from './useWritingProgress'
import { isAtLeast } from './writingConstants'
import {
  WRITING_LONG_POLL_MAX_ATTEMPTS,
  WRITING_PROGRESS_POLL_INTERVAL_MS,
} from './progressPolling'
import RetryActionPanel from './RetryActionPanel'
import { TreeNodeItem, WORD_COUNT_LIMITS, type TreeNode } from './WritingOutlineTreeNode'

type AddDialogInsertPosition = 'first' | 'last' | 'after'

function buildTree(nodes: OutlineNode[]): TreeNode[] {
  const map = new Map<number, TreeNode>()
  const roots: TreeNode[] = []
  for (const n of nodes) map.set(n.id, { ...n, children: [] })
  for (const n of nodes) {
    const node = map.get(n.id)!
    if (n.parentId && map.has(n.parentId)) {
      map.get(n.parentId)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
}

const WritingOutlinePage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const pid = Number(projectId)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(pid, 'extracted')
  // 余额门禁: 与 WritingClauses 共享 writing_outline 节点快照（同一计费节点，有意设计）
  const gate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_outline',
    paymentEnabled: isPaymentEnabled,
  })
  const { outlineNodes, loading, loadOutline, triggerOutlineGenerate, createNode, deleteNode, loadChecklist } = useWritingStore()
  const [generating, setGenerating] = useState(false)
  const [stageText, setStageText] = useState('')
  const [riskDialogOpen, setRiskDialogOpen] = useState(false)
  const [riskActionLabel, setRiskActionLabel] = useState('')
  const [riskSubmitting, setRiskSubmitting] = useState(false)
  const [unlockedNodeIds, setUnlockedNodeIds] = useState<Set<number>>(() => new Set())
  const [dirtyEditingNodeIds, setDirtyEditingNodeIds] = useState<Set<number>>(() => new Set())
  const [pendingNextDialogOpen, setPendingNextDialogOpen] = useState(false)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addDialogParentId, setAddDialogParentId] = useState<number | null>(null)
  const [addDialogInsertAfterId, setAddDialogInsertAfterId] = useState<number | null>(null)
  const [addDialogInsertPosition, setAddDialogInsertPosition] = useState<AddDialogInsertPosition>('last')
  const [addTitle, setAddTitle] = useState('')
  const [addType, setAddType] = useState('free_writing')
  const [addTargetWordCount, setAddTargetWordCount] = useState('800')
  const [creatingNode, setCreatingNode] = useState(false)
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetry, setRefreshingRetry] = useState(false)
  const riskActionRef = useRef<null | (() => Promise<void>)>(null)
  const dirtySaveActionsRef = useRef(new Map<number, () => Promise<boolean>>())

  const outlineInitialSteps = useMemo<ProgressStep[]>(
    () => OUTLINE_STEPS.steps.map((s) => ({
      id: s.id, name: s.name, status: 'pending' as const, progress: 0, targetProgress: 0,
    })),
    [],
  )
  const {
    steps: progressSteps,
    displayProgress,
    updateTargets,
  } = useProgressAnimation(outlineInitialSteps)

  const mountedRef = useRef(true)
  const lastOutlineOverallRef = useRef(0)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    if (pid && ready) {
      loadOutline(pid).catch(() => {})
    }
  }, [pid, ready, loadOutline])

  useEffect(() => {
    if (ready && projectStatus === 'outlining') {
      setGenerating(true)
      pollUntilDone()
    }
  }, [ready, projectStatus])

  const pollUntilDone = useCallback(async () => {
    for (let i = 0; i < WRITING_LONG_POLL_MAX_ATTEMPTS; i++) {
      if (!mountedRef.current) return
      await new Promise(r => setTimeout(r, WRITING_PROGRESS_POLL_INTERVAL_MS))
      try {
        const raw = await writingService.getStatus(pid)
        const status = raw.status
        const overallStatus = raw.overallStatus
        const currentStage = raw.currentStage || ''
        const pct = raw.overallProgress || 0

        const mapped = mapApiProgressSteps(raw.steps, pct, raw.message || '')
          || mapStageToProgressSteps(OUTLINE_STEPS, currentStage, pct)

        const safeOverall = Math.max(lastOutlineOverallRef.current, mapped.overallTarget)
        lastOutlineOverallRef.current = safeOverall
        updateTargets(mapped.steps, safeOverall)
        setStageText(mapped.stageText)

        const isTaskCompleted = overallStatus === 'completed' || currentStage === 'completed'
        if (status === 'failed') {
          toast.error(raw.errorMessage || '目录生成失败')
          writingService.getRetryState(pid, 'writing_outline')
            .then(setRetryState)
            .catch(() => {})
          setGenerating(false)
          return
        }
        if (status === 'outlined' || isTaskCompleted) {
          updateTargets(mapped.steps, mapped.overallTarget)
          setStageText('处理完成')
          toast.success('目录生成完成')
          await loadOutline(pid).catch(() => {})
          loadChecklist(pid)
          setGenerating(false)
          return
        }
      } catch {
        if (i > 5) {
          setGenerating(false)
          return
        }
      }
    }
    setGenerating(false)
  }, [pid, loadOutline, loadChecklist, updateTargets])

  const tree = buildTree(outlineNodes)
  const shouldWarnDownstreamStale = isAtLeast(projectStatus, 'matched')

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

  const handleToggleUnlock = useCallback((nodeId: number) => {
    setUnlockedNodeIds((prev) => {
      const next = new Set(prev)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }, [])

  const handleEditingChange = useCallback((nodeId: number, editing: boolean) => {
    setDirtyEditingNodeIds((prev) => {
      const next = new Set(prev)
      if (editing) next.add(nodeId)
      else next.delete(nodeId)
      return next
    })
  }, [])

  const handleRegisterDirtySave = useCallback((
    nodeId: number,
    saveAction: (() => Promise<boolean>) | null,
  ) => {
    if (saveAction) dirtySaveActionsRef.current.set(nodeId, saveAction)
    else dirtySaveActionsRef.current.delete(nodeId)
  }, [])

  const openAddDialog = useCallback((
    parentId: number | null,
    options?: { insertAfterId?: number | null; insertPosition?: AddDialogInsertPosition },
  ) => {
    setAddDialogParentId(parentId)
    setAddDialogInsertAfterId(options?.insertAfterId ?? null)
    setAddDialogInsertPosition(options?.insertPosition ?? 'last')
    setAddTitle('')
    setAddType('free_writing')
    setAddTargetWordCount('800')
    setAddDialogOpen(true)
  }, [])

  const openAddChildDialog = useCallback((parentId: number) => {
    openAddDialog(parentId, { insertPosition: 'first' })
  }, [openAddDialog])

  const openAddSiblingDialog = useCallback((parentId: number | null, insertAfterId: number) => {
    openAddDialog(parentId, { insertAfterId, insertPosition: 'after' })
  }, [openAddDialog])

  const handleCreateNode = useCallback(async () => {
    const normalizedTitle = addTitle.trim()
    if (!normalizedTitle) {
      toast.error('目录标题不能为空')
      return
    }
    const payload: Record<string, unknown> = {
      parent_id: addDialogParentId,
      title: normalizedTitle,
      node_type: addType,
    }
    if (addDialogInsertAfterId != null) {
      payload.insert_after_id = addDialogInsertAfterId
    }
    if (addDialogInsertPosition !== 'last') {
      payload.insert_position = addDialogInsertPosition
    }
    if (addType !== 'fixed_template') {
      const trimmedWordCount = addTargetWordCount.trim()
      if (trimmedWordCount) {
        const parsedWordCount = Number(trimmedWordCount)
        const limits = WORD_COUNT_LIMITS[addType] || { min: 100, max: 5000 }
        if (!Number.isInteger(parsedWordCount)) {
          toast.error('建议字数必须为整数')
          return
        }
        if (parsedWordCount < limits.min || parsedWordCount > limits.max) {
          toast.error(`建议字数需在 ${limits.min} 到 ${limits.max} 之间`)
          return
        }
        payload.target_word_count = parsedWordCount
      }
    }
    setCreatingNode(true)
    try {
      await executeWithRiskPrompt('新增目录节点', async () => {
        await createNode(pid, payload)
        toast.success('目录节点已新增')
        setAddDialogOpen(false)
      })
    } catch {
      toast.error('新增目录失败')
    } finally {
      setCreatingNode(false)
    }
  }, [
    addDialogParentId,
    addDialogInsertAfterId,
    addDialogInsertPosition,
    addTargetWordCount,
    addTitle,
    addType,
    createNode,
    executeWithRiskPrompt,
    pid,
  ])

  const navigateToMaterials = useCallback(() => {
    navigate(`/project/${projectId}/writing/materials`)
  }, [navigate, projectId])

  const handleNext = useCallback(() => {
    if (outlineNodes.length === 0) { toast.warning('请先生成目录'); return }
    if (dirtyEditingNodeIds.size > 0) {
      setPendingNextDialogOpen(true)
      return
    }
    navigateToMaterials()
  }, [dirtyEditingNodeIds.size, navigateToMaterials, outlineNodes.length])

  const handleSaveDirtyAndNext = useCallback(async () => {
    const actions = Array.from(dirtySaveActionsRef.current.values())
    for (const saveAction of actions) {
      const saved = await saveAction()
      if (!saved) return
    }
    setPendingNextDialogOpen(false)
    navigateToMaterials()
  }, [navigateToMaterials])

  const handleDiscardDirtyAndNext = useCallback(() => {
    setPendingNextDialogOpen(false)
    navigateToMaterials()
  }, [navigateToMaterials])

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
    } catch {
      // 操作内部已给出提示，这里仅防止未处理的 Promise 继续冒泡。
    } finally {
      setRiskSubmitting(false)
    }
  }, [])

  const handleGenerate = useCallback(async () => {
    if (outlineNodes.length > 0) {
      const ok = window.confirm(
        '重新生成将清空当前目录及已关联的绑定和草稿数据，是否继续？',
      )
      if (!ok) return
    }
    setGenerating(true)
    setRetryState(null)
    try {
      await triggerOutlineGenerate(pid, true)
      // 目录生成成功启动，清除余额阻断态与会话快照
      gate.clear()
      pollUntilDone()
    } catch (err) {
      // 余额不足: 不置失败态，保留现有目录树等待充值后继续 
      if (gate.capture(err)) {
        setGenerating(false)
        return
      }
      toast.error('触发目录生成失败')
      setGenerating(false)
    }
  }, [gate.capture, gate.clear, pid, outlineNodes.length, triggerOutlineGenerate, pollUntilDone])

  const refreshRetryState = useCallback(async () => {
    setRefreshingRetry(true)
    try {
      setRetryState(await writingService.getRetryState(pid, 'writing_outline'))
    } finally {
      setRefreshingRetry(false)
    }
  }, [pid])

  useEffect(() => {
    if (ready && projectStatus === 'failed') {
      refreshRetryState().catch(() => {})
    }
  }, [ready, projectStatus, refreshRetryState])

  const handleRetryOutline = useCallback(async () => {
    setRetrying(true)
    setGenerating(true)
    try {
      await writingService.retryWritingNode(pid, 'writing_outline')
      // 重试成功启动，清除余额阻断态与会话快照
      gate.clear()
      setRetryState(null)
      pollUntilDone()
    } catch (error) {
      // 余额不足: 保留重试入口与既有成果，不走通用失败 toast 
      if (gate.capture(error)) {
        setGenerating(false)
      } else {
        toast.error(error instanceof Error ? error.message : '目录生成重试失败')
        setGenerating(false)
        await refreshRetryState().catch(() => {})
      }
    } finally {
      setRetrying(false)
    }
  }, [gate.capture, gate.clear, pid, pollUntilDone, refreshRetryState])

  const autoTriggered = useRef(false)

  useEffect(() => {
    if (
      ready &&
      !loading &&
      !generating &&
      !autoTriggered.current &&
      // 余额阻断中（含刷新恢复）停止自动推进，等待用户充值后手动继续 
      !gate.isBlocked &&
      projectStatus === 'extracted' &&
      outlineNodes.length === 0
    ) {
      autoTriggered.current = true
      handleGenerate()
    }
  }, [ready, loading, generating, gate.isBlocked, projectStatus, outlineNodes.length, handleGenerate])

  const handleDelete = async (nodeId: number) => {
    try {
      await executeWithRiskPrompt('删除目录节点', async () => {
        await deleteNode(pid, nodeId)
        toast.success('节点已删除')
      })
    } catch {
      toast.error('删除失败')
    }
  }

  const handleReorderSiblings = useCallback(async (
    parentId: number | null,
    orderedIds: number[],
  ) => {
    if (dirtyEditingNodeIds.size > 0) {
      toast.warning('存在未保存的目录编辑，请先保存后再调整顺序')
      return
    }
    try {
      await executeWithRiskPrompt('调整目录顺序', async () => {
        try {
          await writingService.reorderOutline(pid, orderedIds, parentId ?? undefined)
          toast.success('目录顺序已更新')
          await loadOutline(pid)
        } catch {
          await loadOutline(pid).catch(() => {})
          toast.error('调整目录顺序失败')
          throw new Error('调整目录顺序失败')
        }
      })
    } catch {
      // 操作内部已提示并刷新，这里避免拖拽回调继续冒泡。
    }
  }, [dirtyEditingNodeIds.size, executeWithRiskPrompt, loadOutline, pid])

  const handleRefreshTree = useCallback(() => {
    loadOutline(pid).catch(() => {})
  }, [pid, loadOutline])

  if (guardLoading || !ready) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>
  }

  return (
    <div>
      <WritingStepBar projectId={pid} currentStep={2} projectStatus={projectStatus} />
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/project/${projectId}/writing/clauses`)}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <h1 className="text-xl font-semibold">目录编辑</h1>
          <Badge variant="secondary">{outlineNodes.length} 节点</Badge>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => openAddDialog(null)} disabled={loading || generating}>
            <Plus className="h-4 w-4 mr-1" />
            新增目录
          </Button>
          <Button variant="outline" onClick={handleGenerate} disabled={loading || generating}>
            {generating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            {generating ? '生成中...' : outlineNodes.length > 0 ? '重新生成' : '生成目录'}
          </Button>
          <Button
            onClick={handleNext}
            disabled={loading || generating || outlineNodes.length === 0}
          >
            下一步
            <ArrowRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      </div>

      <div className="max-w-4xl mx-auto">
      {gate.isBlocked && (
        <BalanceGateCard
          layout="inline"
          semantic={retryState ? 'retry' : 'completed'}
          copy={getBalanceGateCopy(retryState ? 'writing_retry' : 'writing_outline')}
          message={gate.blocked?.message ?? ''}
          paymentEnabled={isPaymentEnabled}
          continueBusy={generating || retrying}
          onRecharge={gate.openRecharge}
          onContinue={retryState ? () => void handleRetryOutline() : () => void handleGenerate()}
        />
      )}
      {retryState?.failedNode === 'writing_outline' && !gate.isBlocked && (
        <RetryActionPanel
          state={retryState}
          onRetry={handleRetryOutline}
          onRefresh={refreshRetryState}
          retrying={retrying}
          refreshing={refreshingRetry}
        />
      )}
      <div className="bg-white rounded-xl border">
        {generating ? (
          <div className="p-4">
            <ProgressPanel
              steps={progressSteps}
              displayProgress={displayProgress}
              stageText={stageText}
              isRunning
              isCompleted={progressSteps.every((s) => s.status === 'completed')}
              isInitialLoading={false}
              error={null}
              accentColor="teal"
              compact
            />
          </div>
        ) : loading ? (
          <div className="text-center py-12 text-muted-foreground">加载中...</div>
        ) : tree.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <p className="mb-4">暂无目录，请先生成</p>
            <Button onClick={handleGenerate}>
              <RefreshCw className="h-4 w-4 mr-1" />
              生成目录
            </Button>
          </div>
        ) : (
          <div className="p-4">
            {tree
              .sort((a, b) => a.sortOrder - b.sortOrder)
              .map((node) => (
                <TreeNodeItem
                  key={node.id}
                  node={node}
                  projectId={pid}
                  onDelete={handleDelete}
                  onUpdate={handleRefreshTree}
                  onAddChild={openAddChildDialog}
                  onAddSibling={openAddSiblingDialog}
                  onReorderSiblings={handleReorderSiblings}
                  onEditingChange={handleEditingChange}
                  onRegisterDirtySave={handleRegisterDirtySave}
                  onToggleUnlock={handleToggleUnlock}
                  unlockedNodeIds={unlockedNodeIds}
                  executeWithRiskPrompt={executeWithRiskPrompt}
                />
              ))}
          </div>
        )}
      </div>

      {!generating && !loading && tree.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground list-disc pl-5">
          <li>带锁目录来自招标文件固定结构，默认仅查看；点击锁图标解除锁定后，可编辑、删除或新增子目录。</li>
          <li>解锁父节点后会同步放开其子节点操作；行尾操作按钮默认弱显示，鼠标移入后高亮。</li>
        </ul>
      )}
      </div>

      <Dialog open={riskDialogOpen} onOpenChange={(open) => { if (!open) { setRiskDialogOpen(false); riskActionRef.current = null } }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              后续结果可能已过期
            </DialogTitle>
            <DialogDescription className="leading-relaxed">
              当前项目已进入绑定、写作或预览等后续阶段。继续执行“{riskActionLabel}”后，系统不会自动刷新绑定、草稿和预览结果。
              如需保持前后数据一致，请在本次修改后手动重新生成相关章节或全文。
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

      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新增目录节点</DialogTitle>
            <DialogDescription>
              新节点将在点击确认后立即保存，后续素材匹配与内容生成将直接消费该目录。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="outline-add-title">目录标题</label>
              <Input
                id="outline-add-title"
                value={addTitle}
                onChange={(e) => setAddTitle(e.target.value)}
                placeholder="请输入目录标题"
                autoFocus
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium">章节类型</label>
              <Select value={addType} onValueChange={setAddType}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="fixed_template">固定模板</SelectItem>
                  <SelectItem value="data_fill">资料填充</SelectItem>
                  <SelectItem value="free_writing">自由论述</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {addType !== 'fixed_template' ? (
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="outline-add-word-count">建议字数</label>
                <Input
                  id="outline-add-word-count"
                  type="number"
                  min={String((WORD_COUNT_LIMITS[addType] || { min: 100 }).min)}
                  max={String((WORD_COUNT_LIMITS[addType] || { max: 5000 }).max)}
                  step="100"
                  value={addTargetWordCount}
                  onChange={(e) => setAddTargetWordCount(e.target.value)}
                />
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">固定模板节点不设置建议字数。</p>
            )}
            {addDialogParentId != null && (
              <p className="text-xs text-muted-foreground">
                {addDialogInsertPosition === 'after'
                  ? '将紧邻当前目录下方新增同级节点。'
                  : addDialogInsertPosition === 'first'
                    ? '将作为当前目录的第一个子节点新增。'
                    : '将作为当前目录的子节点新增。'}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddDialogOpen(false)}>取消</Button>
            <Button onClick={handleCreateNode} disabled={creatingNode}>
              {creatingNode ? '保存中...' : '确认新增'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={pendingNextDialogOpen} onOpenChange={setPendingNextDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>存在未保存的目录编辑</DialogTitle>
            <DialogDescription>
              当前仍有目录行处于编辑状态。后续节点只消费已保存到后端的目录，请先保存或放弃本次修改。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingNextDialogOpen(false)}>取消</Button>
            <Button variant="outline" onClick={handleDiscardDirtyAndNext}>放弃修改并继续</Button>
            <Button onClick={handleSaveDirtyAndNext}>保存并继续</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <FirstVisitOutlineDialog />
    </div>
  )
}

function FirstVisitOutlineDialog() {
  const { open, dismiss } = useFirstVisitDialog('bidmind_writing_guide_outline')
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) dismiss() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>目录结构确认</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            目录是标书的骨架，直接决定最终文档的结构和质量。AI 后续将严格按照此目录生成内容。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-3 p-3 bg-muted rounded-lg space-y-1.5 text-sm text-foreground/80">
          <p className="text-sm font-medium text-foreground mb-1">操作提示</p>
          <p>&middot; 仔细审查各章节的层级、顺序和字数分配</p>
          <p>&middot; 带锁章节来自招标文件固定结构，默认锁定以避免误改</p>
          <p>&middot; 如提取目录不准确，可点击锁图标解除锁定后编辑、删除或新增子目录</p>
          <p>&middot; 点击"重新生成"可让 AI 重新规划目录</p>
        </div>
        <DialogFooter>
          <Button onClick={dismiss}>知道了</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default WritingOutlinePage
