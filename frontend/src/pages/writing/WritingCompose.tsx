import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { useLocation, useParams, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowRight, Play, FileText, Lock, Unlock, RefreshCw, Save, Loader2 } from 'lucide-react'
import InlineProgressBar from '@/components/InlineProgressBar'
import FirstVisitComposeDialog from './FirstVisitComposeDialog'
import { useWritingStore } from '@/stores'
import { writingService } from '@/services/writingApi'
import { useWritingGuard } from './useWritingGuard'
import { isAtLeast } from './writingConstants'
import WritingStepBar from './WritingStepBar'
import RichTextEditor, { MaterialImageInfo } from '@/components/RichTextEditor'
import { toast } from 'sonner'
import BalanceGateCard from '@/components/BalanceGateCard'
import { useInsufficientBalanceGate } from '@/hooks/useInsufficientBalanceGate'
import { getBalanceGateCopy } from '@/lib/balanceGateCopy'
import { isPaymentEnabled } from '@/config/features'
import { summarizeGateWarnings } from './writingComposeGateFlags'
import { buildComposeTree } from './writingComposeTree'
import { WritingGateReviewSummary } from './components/WritingGateReviewSummary'
import WritingClauseCoveragePanel from './WritingClauseCoveragePanel'
import WritingClauseCoverageOverview from './WritingClauseCoverageOverview'
import type { ClauseCoverage, GateFlagEntry } from '@/services/writingApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'
import RetryActionPanel from './RetryActionPanel'
import { safeRichHtml } from '@/lib/sanitizeHtml'
import type { CoverageBindingFocus } from './writingClauseCoverageAction'
import { shouldAutosaveDraftChange } from './writingDraftAutosave'
import { isFirstComposeInteractionLocked } from './writingComposeLock'
import { WritingComposeSidebar, type WritingComposeTreeNode } from './WritingComposeSidebar'
import {
  WRITING_LONG_POLL_TIMEOUT_MS,
  WRITING_PROGRESS_POLL_INTERVAL_MS,
  WRITING_SINGLE_POLL_TIMEOUT_MS,
  isWritingPollAbortError,
  pollWritingTask,
} from './progressPolling'

function isSuggestedWordCountOutOfRange(actualWordCount: number, suggestedWordCount: number | null | undefined) {
  if (suggestedWordCount == null || suggestedWordCount <= 0) return false
  const ratio = actualWordCount / suggestedWordCount
  const upperRatio = suggestedWordCount <= 1500 ? 2.0 : suggestedWordCount <= 3000 ? 1.8 : 1.6
  return ratio < 0.6 || ratio > upperRatio
}

const WritingComposePage: React.FC = () => {
  const { id: projectId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const pid = Number(projectId)
  const { ready, loading: guardLoading, projectStatus } = useWritingGuard(pid, 'extracted')
  const {
    outlineNodes, drafts, clauses, currentNodeId, loading,
    loadOutline, loadDrafts, loadClauses, setCurrentNodeId,
  } = useWritingStore()
  // 余额门禁 -- 全文生成与单章重生成分开阻断, 单章会话快照按章节隔离
  const composeGate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: 'writing_compose',
    paymentEnabled: isPaymentEnabled,
  })
  const singleGate = useInsufficientBalanceGate({
    projectId: pid,
    nodeKey: currentNodeId ? `writing_compose_single:${currentNodeId}` : 'writing_compose_single',
    paymentEnabled: isPaymentEnabled,
  })
  const [saving, setSaving] = useState(false)
  const [composing, setComposing] = useState(false)
  const [composeTitle, setComposeTitle] = useState('内容正在生成中')
  const [composeDetail, setComposeDetail] = useState('')
  const [progressValue, setProgressValue] = useState(0)
  const [editContent, setEditContent] = useState('')
  const [syncedEditorKey, setSyncedEditorKey] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()
  const initialEditorSyncRef = useRef(false)
  const mountedRef = useRef(true)
  const pollAbortRef = useRef<AbortController | null>(null)
  const autoTriggeredRef = useRef(false)
  const [initialLoaded, setInitialLoaded] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(220)
  const [collapsedNodes, setCollapsedNodes] = useState<Record<number, boolean>>({})
  const [nodeImages, setNodeImages] = useState<MaterialImageInfo[]>([])
  const [coverage, setCoverage] = useState<ClauseCoverage | null>(null)
  const [composeRetryState, setComposeRetryState] = useState<WritingRetryState | null>(null)
  const [singleRetryState, setSingleRetryState] = useState<WritingRetryState | null>(null)
  const [retryingNode, setRetryingNode] = useState<string | null>(null)
  const [refreshingRetryNode, setRefreshingRetryNode] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const isDraggingRef = useRef(false)

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDraggingRef.current = true
    const startX = e.clientX
    const startWidth = sidebarWidth

    const onMouseMove = (ev: MouseEvent) => {
      if (!isDraggingRef.current) return
      const newWidth = Math.min(500, Math.max(160, startWidth + (ev.clientX - startX)))
      setSidebarWidth(newWidth)
    }
    const onMouseUp = () => {
      isDraggingRef.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [sidebarWidth])

  const replacePollSignal = useCallback(() => {
    pollAbortRef.current?.abort()
    const controller = new AbortController()
    pollAbortRef.current = controller
    return controller.signal
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      pollAbortRef.current?.abort()
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const parseComposeDetail = useCallback((detail: string) => {
    const countMatch = detail.match(/^\[(\d+)\/(\d+)\]/)
    const current = countMatch ? Number(countMatch[1]) : 0
    const total = countMatch ? Number(countMatch[2]) : 0

    if (current > 0 && total > 0) {
      setComposeTitle(`内容生成中 -- 第 ${current}/${total} 章`)
    }

    let rest = detail
    if (countMatch) rest = rest.slice(countMatch[0].length).trim()

    setComposeDetail(rest)
  }, [])

  const pollComposeStatus = useCallback(async (signal: AbortSignal): Promise<boolean> => {
    await pollWritingTask({
      fetchStatus: () => writingService.getComposeStatus(pid),
      timeoutMs: WRITING_LONG_POLL_TIMEOUT_MS,
      signal,
      visibleIntervalMs: WRITING_PROGRESS_POLL_INTERVAL_MS,
      onProgress: (status) => {
        if (status.currentDetail) {
          parseComposeDetail(status.currentDetail)
        }
        setProgressValue(status.progressPct / 100)
      },
    })
    return true
  }, [pid, parseComposeDetail])

  const pollSingleStatus = useCallback(async (nodeId: number, signal: AbortSignal) => {
    await pollWritingTask({
      fetchStatus: () => writingService.getComposeSingleStatus(pid, nodeId),
      timeoutMs: WRITING_SINGLE_POLL_TIMEOUT_MS,
      signal,
      visibleIntervalMs: WRITING_PROGRESS_POLL_INTERVAL_MS,
    })
  }, [pid])

  const handleCompose = useCallback(async (force = false) => {
    const signal = replacePollSignal()
    setComposing(true)
    setComposeRetryState(null)
    setComposeTitle('内容正在生成中')
    setComposeDetail('初始化...')
    try {
      await writingService.triggerCompose(pid, force)
      const ok = await pollComposeStatus(signal)
      if (ok && mountedRef.current) {
        await loadDrafts(pid)
        composeGate.clear()
        toast.success('生成完成')
      }
    } catch (e) {
      if (isWritingPollAbortError(e) || !mountedRef.current) return
      // 余额不足由门禁接管, 任务未启动不算失败, 不拉取 retryState
      if (!composeGate.capture(e)) {
        const msg = e instanceof Error ? e.message : '生成失败'
        toast.error(msg)
        writingService.getRetryState(pid, 'writing_compose')
          .then(setComposeRetryState)
          .catch(() => {})
      }
    } finally {
      if (mountedRef.current && !signal.aborted) {
        setComposing(false)
        setComposeDetail('')
        setProgressValue(0)
      }
    }
  }, [pid, loadDrafts, pollComposeStatus, composeGate.capture, composeGate.clear, replacePollSignal])

  useEffect(() => {
    if (!pid) return
    setInitialLoaded(false)
    autoTriggeredRef.current = false

    Promise.all([
      loadOutline(pid).catch(() => {}),
      loadDrafts(pid).catch(() => {}),
      loadClauses(pid).catch(() => {}),
    ]).then(() => {
      if (mountedRef.current) setInitialLoaded(true)
    })

    writingService.getComposeStatus(pid).then((status) => {
      if (status.active && mountedRef.current) {
        const signal = replacePollSignal()
        setComposing(true)
        setComposeDetail(status.currentDetail || status.progress || '生成中...')
        setProgressValue(status.progressPct / 100)
        pollComposeStatus(signal)
          .then(async (ok) => {
            if (ok && mountedRef.current) {
              await loadDrafts(pid)
              toast.success('生成完成')
            }
          })
          .catch((e) => {
            if (isWritingPollAbortError(e) || !mountedRef.current) return
            toast.error(e instanceof Error ? e.message : '生成失败')
          })
          .finally(() => {
            if (mountedRef.current && !signal.aborted) {
              setComposing(false)
              setComposeDetail('')
              setProgressValue(0)
            }
          })
      }
    }).catch(() => {})
  }, [pid, loadOutline, loadDrafts, loadClauses, pollComposeStatus, replacePollSignal])

  useEffect(() => {
    if (
      pid && ready && initialLoaded && !composing &&
      drafts.length === 0 && outlineNodes.length > 0 &&
      !autoTriggeredRef.current &&
      // 余额阻断(含刷新恢复)时截断自动生成且不置 ref, 充值清除后可恢复自动
      !composeGate.isBlocked &&
      isAtLeast(projectStatus, 'matched') &&
      !isAtLeast(projectStatus, 'composed')
    ) {
      autoTriggeredRef.current = true
      handleCompose()
    }
  }, [pid, ready, initialLoaded, composing, drafts.length, outlineNodes.length, projectStatus, handleCompose, composeGate.isBlocked])

  const selectedDraft = drafts.find((d) => d.outlineNodeId === currentNodeId)
  const selectedNode = outlineNodes.find((n) => n.id === currentNodeId)
  // 锁定必须能在页面重挂载后由服务端数据重建，
  // 禁止用会话内 drafts.length===0 判定"首次生成"
  const composeInteractionLocked = isFirstComposeInteractionLocked({
    composing,
    outlineNodes,
    draftedNodeIds: drafts.map((d) => d.outlineNodeId),
  })
  const selectedDraftNeedsRegeneration =
    (selectedDraft?.failureState === 'needs_review' || selectedDraft?.reviewRequired === 1)
    && (selectedDraft?.failureReason || '').includes('绑定已变更')

  // 编辑器按 (章节, 草稿) 维度重挂载，初始内容必须在渲染期同步就绪。
  // 若等 effect 晚一帧修正，新章节编辑器会短暂持有上一章内容，
  // 挂载期任何 update 事件都会把上一章 HTML 经自动保存写入新章节。
  const editorInstanceKey = `${currentNodeId ?? 'none'}:${selectedDraft?.id ?? 'none'}`
  if (syncedEditorKey !== editorInstanceKey) {
    setSyncedEditorKey(editorInstanceKey)
    setEditContent(selectedDraft?.content || '')
    initialEditorSyncRef.current = Boolean(selectedDraft?.content)
  }

  const { treeRoots, parentMap } = useMemo(
    () => buildComposeTree(outlineNodes, drafts),
    [outlineNodes, drafts],
  )

  const gateWarningSummary = useMemo(() => {
    const draftMap = new Map(drafts.map((d) => [d.outlineNodeId, d]))
    return summarizeGateWarnings(
      outlineNodes.map((node) => ({
        nodeId: node.id,
        title: node.title,
        gateFlags: (draftMap.get(node.id)?.gateFlags as Record<string, GateFlagEntry> | null | undefined),
      })),
    )
  }, [outlineNodes, drafts])

  const toggleCollapse = useCallback((nodeId: number) => {
    setCollapsedNodes((prev) => ({ ...prev, [nodeId]: !prev[nodeId] }))
  }, [])

  useEffect(() => {
    if (!currentNodeId) return
    setCollapsedNodes((prev) => {
      const next = { ...prev }
      let cur: number | null | undefined = parentMap.get(currentNodeId) ?? null
      let changed = false
      while (cur != null) {
        if (next[cur]) {
          delete next[cur]
          changed = true
        }
        cur = parentMap.get(cur) ?? null
      }
      return changed ? next : prev
    })
  }, [currentNodeId, parentMap])

  useEffect(() => {
    if (currentNodeId || treeRoots.length === 0) return
    function findFirstLeaf(node: WritingComposeTreeNode): number | null {
      if (node.isLeaf) return node.id
      for (const child of node.children) {
        const found = findFirstLeaf(child)
        if (found) return found
      }
      return null
    }
    for (const root of treeRoots) {
      const leafId = findFirstLeaf(root)
      if (leafId) {
        setCurrentNodeId(leafId)
        return
      }
    }
  }, [currentNodeId, treeRoots, setCurrentNodeId])

  useEffect(() => {
    if (composing || drafts.length === 0 || !currentNodeId) return
    if (drafts.some((draft) => draft.outlineNodeId === currentNodeId)) return
    setCurrentNodeId(drafts[0].outlineNodeId)
  }, [composing, currentNodeId, drafts, setCurrentNodeId])

  useEffect(() => {
    if (selectedDraft?.content) {
      setEditContent(selectedDraft.content)
      initialEditorSyncRef.current = true
    } else {
      setEditContent('')
      initialEditorSyncRef.current = false
    }
  }, [selectedDraft?.id, selectedDraft?.content])

  useEffect(() => {
    if (!currentNodeId) {
      setNodeImages([])
      return
    }
    writingService.getNodeImages(pid, currentNodeId)
      .then(imgs => setNodeImages(imgs.map(i => ({
        id: i.id,
        caption: i.caption,
        imageType: i.imageType,
      }))))
      .catch(() => setNodeImages([]))
  }, [pid, currentNodeId])

  const handleRegenerate = useCallback(async () => {
    if (!currentNodeId) return
    const signal = replacePollSignal()
    toast.info('正在重新生成...')
    setSingleRetryState(null)
    setRegenerating(true)
    try {
      await writingService.composeSingle(pid, currentNodeId)
      await pollSingleStatus(currentNodeId, signal)
      if (!mountedRef.current) return
      await loadDrafts(pid)
      singleGate.clear()
      toast.success('重新生成完成')
    } catch (e) {
      if (isWritingPollAbortError(e) || !mountedRef.current) return
      // 余额不足由门禁接管, 不落通用 toast 也不置 retryState
      if (!singleGate.capture(e)) {
        toast.error(e instanceof Error ? e.message : '重新生成失败')
        writingService.getRetryState(pid, 'writing_compose_single', currentNodeId)
          .then(setSingleRetryState)
          .catch(() => {})
      }
    } finally {
      if (!signal.aborted) setRegenerating(false)
    }
  }, [pid, currentNodeId, loadDrafts, singleGate.capture, singleGate.clear, pollSingleStatus, replacePollSignal])

  const refreshComposeRetryState = useCallback(async () => {
    setRefreshingRetryNode('writing_compose')
    try {
      setComposeRetryState(await writingService.getRetryState(pid, 'writing_compose'))
    } finally {
      setRefreshingRetryNode(null)
    }
  }, [pid])

  const retryCompose = useCallback(async () => {
    const signal = replacePollSignal()
    setRetryingNode('writing_compose')
    setComposing(true)
    try {
      await writingService.retryWritingNode(pid, 'writing_compose')
      const ok = await pollComposeStatus(signal)
      if (ok && mountedRef.current) {
        await loadDrafts(pid)
        setComposeRetryState(null)
        composeGate.clear()
        toast.success('全文生成重试完成')
      }
    } catch (e) {
      if (isWritingPollAbortError(e) || !mountedRef.current) return
      // 余额不足重试未启动, 原 retryState 仍有效, 跳过通用 toast 与刷新
      if (!composeGate.capture(e)) {
        toast.error(e instanceof Error ? e.message : '全文生成重试失败')
        await refreshComposeRetryState().catch(() => {})
      }
    } finally {
      if (!signal.aborted) {
        setRetryingNode(null)
        setComposing(false)
        setComposeDetail('')
        setProgressValue(0)
      }
    }
  }, [pid, pollComposeStatus, loadDrafts, refreshComposeRetryState, composeGate.capture, composeGate.clear, replacePollSignal])

  // 充值后继续全文生成; 有重试上下文走失败节点重试, 否则强制重新生成
  const continueComposeAfterRecharge = useCallback(() => {
    composeGate.clear()
    if (composeRetryState) {
      retryCompose()
    } else {
      handleCompose(true)
    }
  }, [composeGate.clear, composeRetryState, retryCompose, handleCompose])

  const refreshSingleRetryState = useCallback(async () => {
    if (!currentNodeId) return
    setRefreshingRetryNode('writing_compose_single')
    try {
      setSingleRetryState(await writingService.getRetryState(pid, 'writing_compose_single', currentNodeId))
    } finally {
      setRefreshingRetryNode(null)
    }
  }, [pid, currentNodeId])

  useEffect(() => {
    if (ready && projectStatus === 'failed') {
      refreshComposeRetryState().catch(() => {})
      refreshSingleRetryState().catch(() => {})
    }
  }, [ready, projectStatus, refreshComposeRetryState, refreshSingleRetryState])

  const retrySingle = useCallback(async () => {
    if (!currentNodeId) return
    const signal = replacePollSignal()
    setRetryingNode('writing_compose_single')
    try {
      await writingService.retryWritingNode(pid, 'writing_compose_single', currentNodeId)
      await pollSingleStatus(currentNodeId, signal)
      if (!mountedRef.current) return
      await loadDrafts(pid)
      setSingleRetryState(null)
      singleGate.clear()
      toast.success('单章生成重试完成')
    } catch (e) {
      if (isWritingPollAbortError(e) || !mountedRef.current) return
      // 余额不足重试未启动, 门禁接管, 跳过通用 toast 与 retryState 刷新
      if (!singleGate.capture(e)) {
        toast.error(e instanceof Error ? e.message : '单章生成重试失败')
        await refreshSingleRetryState().catch(() => {})
      }
    } finally {
      if (!signal.aborted) setRetryingNode(null)
    }
  }, [pid, currentNodeId, loadDrafts, refreshSingleRetryState, singleGate.capture, singleGate.clear, pollSingleStatus, replacePollSignal])

  const handleSave = useCallback(async () => {
    if (!currentNodeId || !editContent) return
    setSaving(true)
    try {
      await writingService.updateDraft(pid, currentNodeId, editContent)
      await loadDrafts(pid)
      toast.success('已保存')
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }, [pid, currentNodeId, editContent, loadDrafts])

  const handleToggleLock = useCallback(async () => {
    if (!currentNodeId) return
    try {
      await writingService.toggleLock(pid, currentNodeId)
      await loadDrafts(pid)
      toast.success(selectedDraft?.isLocked ? '已解锁' : '已锁定')
    } catch {
      toast.error('操作失败')
    }
  }, [pid, currentNodeId, selectedDraft?.isLocked, loadDrafts])

  const handleContentChange = useCallback((value: string) => {
    setEditContent(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const initialEditorSync = initialEditorSyncRef.current
    initialEditorSyncRef.current = false
    if (!shouldAutosaveDraftChange(value, selectedDraft?.content, { initialEditorSync })) {
      return
    }
    debounceRef.current = setTimeout(() => {
      if (currentNodeId && value) {
        writingService.updateDraft(pid, currentNodeId, value).catch(() => {})
      }
    }, 2000)
  }, [pid, currentNodeId, selectedDraft?.content])

  const loadCoverage = useCallback(async () => {
    if (!pid) return
    try {
      setCoverage(await writingService.getClauseCoverage(pid))
    } catch {
      // 覆盖概览为辅助信息, 加载失败静默降级
    }
  }, [pid])

  useEffect(() => {
    const state = location.state as { refreshCoverage?: boolean } | null
    if (!state?.refreshCoverage) return
    loadCoverage()
    navigate(location.pathname, { replace: true, state: null })
  }, [location.pathname, location.state, loadCoverage, navigate])

  // 草稿/约束变化(生成、编辑、重生成后)自动刷新全局覆盖概览
  useEffect(() => {
    loadCoverage()
  }, [loadCoverage, drafts, clauses])

  if (guardLoading || !ready) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>
  }

  return (
    <div>
      <div className="px-6 pt-2">
        <WritingStepBar projectId={pid} currentStep={3} projectStatus={projectStatus} />
      </div>
    <div className="flex h-[calc(100vh-8rem)] gap-0">
      {/* 左栏: 目录导航 */}
      <WritingComposeSidebar
        projectId={projectId}
        treeRoots={treeRoots}
        currentNodeId={currentNodeId}
        collapsedNodes={collapsedNodes}
        sidebarWidth={sidebarWidth}
        disabled={composeInteractionLocked}
        onSelect={setCurrentNodeId}
        onToggle={toggleCollapse}
        onDragStart={handleDragStart}
      />

      {/* 主区: 编辑器 */}
      <div className="flex-1 flex flex-col bg-white">
        <div className="flex items-center justify-between p-3 border-b">
          <div className="flex items-center gap-2">
            <h2 className="font-medium">内容编辑</h2>
            <Badge variant="secondary">{drafts.length} 章节</Badge>
            <WritingClauseCoverageOverview
              coverage={coverage}
              onRefresh={loadCoverage}
              onNavigateBinding={(focus?: CoverageBindingFocus) => navigate(
                `/project/${projectId}/writing/binding`,
                { state: focus ? { focus } : undefined },
              )}
            />
            <WritingGateReviewSummary
              summary={gateWarningSummary}
              onSelectNode={setCurrentNodeId}
            />
          </div>
          <div className="flex gap-2">
            {selectedDraft && (
              <>
                <Button variant="ghost" size="sm" onClick={handleToggleLock} title={selectedDraft.isLocked ? '点击解锁' : '点击锁定'}>
                  {selectedDraft.isLocked ? <Lock className="h-4 w-4" /> : <Unlock className="h-4 w-4" />}
                </Button>
                <Button variant="ghost" size="sm" onClick={handleRegenerate} title="重新生成">
                  <RefreshCw className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="sm" onClick={handleSave} disabled={saving}>
                  <Save className="h-4 w-4 mr-1" />
                  {saving ? '保存中...' : '保存'}
                </Button>
              </>
            )}
            <Button variant="outline" size="sm" onClick={() => handleCompose(true)} disabled={loading || composing}>
              {composing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Play className="h-4 w-4 mr-1" />}
              {composing ? '生成中...' : drafts.length > 0 ? '重新生成全文' : '生成全文'}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                if (drafts.length === 0) { toast.warning('请先生成内容'); return }
                navigate(`/project/${projectId}/writing/preview`)
              }}
              disabled={composing || drafts.length === 0}
              title={drafts.length === 0 ? '请先生成内容' : ''}
            >
              预览导出
              <ArrowRight className="h-4 w-4 ml-1" />
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          <div className="max-w-3xl mx-auto p-6">
            {composeGate.blocked && drafts.length > 0 && (
              <BalanceGateCard
                layout="inline"
                semantic={composeRetryState ? 'retry' : 'completed'}
                copy={getBalanceGateCopy(composeRetryState ? 'writing_retry' : 'writing_compose')}
                message={composeGate.blocked.message}
                paymentEnabled={isPaymentEnabled}
                continueBusy={composing}
                onRecharge={composeGate.openRecharge}
                onContinue={continueComposeAfterRecharge}
              />
            )}
            {composeRetryState?.failedNode === 'writing_compose' && !composeGate.isBlocked && (
              <RetryActionPanel
                state={composeRetryState}
                onRetry={retryCompose}
                onRefresh={refreshComposeRetryState}
                retrying={retryingNode === 'writing_compose'}
                refreshing={refreshingRetryNode === 'writing_compose'}
              />
            )}
            {singleRetryState?.failedNode === 'writing_compose_single' && !singleGate.isBlocked && (
              <RetryActionPanel
                state={singleRetryState}
                onRetry={retrySingle}
                onRefresh={refreshSingleRetryState}
                retrying={retryingNode === 'writing_compose_single'}
                refreshing={refreshingRetryNode === 'writing_compose_single'}
              />
            )}
            {composing ? (
              <div className="py-20 flex justify-center">
                <div className="w-full max-w-md">
                  <InlineProgressBar
                    title={composeTitle}
                    detail={composeDetail}
                    progress={progressValue}
                    accentColor="teal"
                  />
                  <p className="text-xs text-muted-foreground mt-4 text-center">此过程可能需要较长时间,请勿关闭页面</p>
                </div>
              </div>
            ) : composeGate.blocked && drafts.length === 0 ? (
              <BalanceGateCard
                layout="center"
                semantic="completed"
                copy={getBalanceGateCopy('writing_compose')}
                message={composeGate.blocked.message}
                paymentEnabled={isPaymentEnabled}
                continueBusy={composing}
                onRecharge={composeGate.openRecharge}
                onContinue={continueComposeAfterRecharge}
              />
            ) : !selectedDraft && !currentNodeId ? (
              <div className="text-center py-20 text-muted-foreground">
                <FileText className="h-12 w-12 mx-auto mb-4 opacity-30" />
                <p>选择左侧章节查看/编辑内容</p>
                {drafts.length === 0 && (
                  <Button className="mt-4" onClick={() => handleCompose(true)}>
                    <Play className="h-4 w-4 mr-1" />
                    开始生成
                  </Button>
                )}
              </div>
            ) : (
              <div>
                <h3 className="text-lg font-semibold mb-4">{selectedNode?.title}</h3>
                <WritingClauseCoveragePanel
                  clauses={clauses}
                  drafts={drafts}
                  currentNodeId={currentNodeId}
                />
                {singleGate.blocked && (
                  <BalanceGateCard
                    layout="inline"
                    semantic="retry"
                    copy={getBalanceGateCopy('writing_compose_single')}
                    message={singleGate.blocked.message}
                    paymentEnabled={isPaymentEnabled}
                    continueBusy={regenerating || retryingNode === 'writing_compose_single'}
                    onRecharge={singleGate.openRecharge}
                    onContinue={() => {
                      singleGate.clear()
                      handleRegenerate()
                    }}
                  />
                )}
                {selectedDraftNeedsRegeneration && (
                  <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    <p className="font-medium">绑定已变更，请重新生成章节</p>
                    <p className="mt-1 text-xs">
                      {selectedDraft?.failureReason || '当前章节的素材或约束绑定已发生变化，旧草稿可能未消费最新绑定。'}
                    </p>
                  </div>
                )}
                {selectedDraft?.isLocked ? (
                  <div className="relative">
                    <div className="absolute inset-0 bg-white/50 z-10 flex items-start justify-center pt-20">
                      <Button onClick={handleToggleLock}>
                        <Unlock className="h-4 w-4 mr-1" />
                        解锁编辑
                      </Button>
                    </div>
                    <div
                      className="prose prose-sm max-w-none opacity-50"
                      dangerouslySetInnerHTML={{ __html: safeRichHtml(selectedDraft.content || '') }}
                    />
                  </div>
                ) : selectedDraft ? (
                  <RichTextEditor
                    key={`${currentNodeId}-${selectedDraft.id}`}
                    content={editContent}
                    onChange={handleContentChange}
                    editable={true}
                    placeholder="章节内容..."
                    projectId={pid}
                    images={nodeImages}
                  />
                ) : (
                  <div className="text-center py-12 text-muted-foreground">
                    <p>该章节尚未生成内容</p>
                    <Button className="mt-2" variant="outline" onClick={handleRegenerate}>
                      <RefreshCw className="h-4 w-4 mr-1" />
                      生成此章节
                    </Button>
                  </div>
                )}
                {selectedDraft?.wordCount != null && (
                  <p className="text-xs text-muted-foreground mt-4">
                    实际 {selectedDraft.wordCount} 字
                    {selectedNode?.targetWordCount != null && ` / 建议 ${selectedNode.targetWordCount} 字`}
                    {' | '}版本 {selectedDraft.version}
                    {selectedDraft.isUserEdited === 1 && ' | 已编辑'}
                    {isSuggestedWordCountOutOfRange(selectedDraft.wordCount, selectedNode?.targetWordCount)
                      && ' | 与建议篇幅偏差较大，可按需重生成'}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>

    {!composeInteractionLocked && <FirstVisitComposeDialog />}
    </div>
  )
}

export default WritingComposePage
