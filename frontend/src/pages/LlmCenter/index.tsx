import React, { useState, useEffect, useCallback } from 'react'
import { Cpu, Eye, Code2, Play, Zap, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  llmCenterService,
  type LlmFlowSummary,
  type LlmFlowTopology,
  type BatchRunResponse,
  type BatchRunItem,
} from '@/services/llmCenterApi'
import type { ViewMode, ConfirmDialogState } from './types'
import { FLOW_TABS } from './lib/constants'
import { StageAccordion } from './components/StageAccordion'
import { NodeDrawer } from './components/NodeDrawer'
import { BatchResultPanel } from './components/BatchResultPanel'
import { ConfirmDialog } from './components/ConfirmDialog'

const LlmCenterPage: React.FC = () => {
  const [flows, setFlows] = useState<LlmFlowSummary[]>([])
  const [activeFlow, setActiveFlow] = useState('writing')
  const [topology, setTopology] = useState<LlmFlowTopology | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('business')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [registryStatus, setRegistryStatus] = useState<{ total_nodes: number; total_active_versions: number } | null>(null)

  const [expandedStages, setExpandedStages] = useState<Set<string>>(new Set())
  const [batchScope, setBatchScope] = useState<'flow' | 'all_pending'>('flow')
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchResult, setBatchResult] = useState<BatchRunResponse | null>(null)
  const [publishingPassed, setPublishingPassed] = useState(false)
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState>({
    open: false, title: '', message: '', confirmLabel: '', onConfirm: () => {},
  })

  const loadFlows = useCallback(async () => {
    try {
      const data = await llmCenterService.listFlows()
      setFlows(data)
      llmCenterService.getRegistryStatus().then(setRegistryStatus).catch(() => toast.warning('Registry 状态获取失败'))
    } catch { toast.error('加载流程列表失败') }
  }, [])

  const loadTopology = useCallback(async (flowKey: string) => {
    setLoading(true)
    try {
      const data = await llmCenterService.getFlowTopology(flowKey)
      setTopology(data)
      setExpandedStages(new Set(data.stages.map(s => s.stage_key)))
    } catch { toast.error('加载流程拓扑失败') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { loadFlows() }, [loadFlows])
  useEffect(() => { loadTopology(activeFlow) }, [activeFlow, loadTopology])

  const toggleStage = useCallback((stageKey: string) => {
    setExpandedStages(prev => {
      const next = new Set(prev)
      if (next.has(stageKey)) next.delete(stageKey)
      else next.add(stageKey)
      return next
    })
  }, [])

  const locateNode = useCallback((item: BatchRunItem) => {
    if (item.flow_key && item.flow_key !== activeFlow) {
      setActiveFlow(item.flow_key)
    }
    setExpandedStages(prev => new Set([...prev, item.stage_key]))
    setSelectedNode(item.node_key)
  }, [activeFlow])

  const executeBatchValidate = useCallback(async () => {
    const doRun = async () => {
      setBatchRunning(true)
      setBatchResult(null)
      try {
        const result = await llmCenterService.batchValidate({
          scope: batchScope,
          flow_key: batchScope === 'flow' ? activeFlow : undefined,
          include_technical: viewMode === 'technical',
        })
        setBatchResult(result)
        if (result.summary.total === 0) {
          toast.info('当前范围内没有待校验草稿')
        } else if (result.summary.validated_failed === 0) {
          toast.success(`${result.summary.validated_passed} 个节点全部校验通过`)
        } else {
          toast.warning(`${result.summary.validated_passed} 通过, ${result.summary.validated_failed} 失败`)
        }
        loadTopology(activeFlow)
      } catch { toast.error('批量校验失败') }
      finally { setBatchRunning(false) }
    }

    if (batchScope === 'all_pending') {
      setConfirmDialog({
        open: true,
        title: '确认全局校验',
        message: '将对所有流程中的待处理节点执行校验。\n此操作影响范围较大，请确认继续。',
        confirmLabel: '确认校验',
        onConfirm: () => { setConfirmDialog(prev => ({ ...prev, open: false })); doRun() },
      })
    } else {
      doRun()
    }
  }, [batchScope, activeFlow, viewMode, loadTopology])

  const executeBatchValidatePublish = useCallback(async () => {
    const doRun = async () => {
      setBatchRunning(true)
      setBatchResult(null)
      try {
        const result = await llmCenterService.batchValidateAndPublish({
          scope: batchScope,
          flow_key: batchScope === 'flow' ? activeFlow : undefined,
          include_technical: viewMode === 'technical',
        })
        setBatchResult(result)
        if (result.summary.total === 0) {
          toast.info('已完成节点同步，当前范围内没有待校验草稿')
        } else if (result.summary.published > 0 && result.summary.validated_failed === 0) {
          toast.success(`${result.summary.published} 个节点已同步、校验并发布`)
        } else if (result.summary.validated_failed > 0) {
          toast.warning(`${result.summary.validated_passed} 通过, ${result.summary.validated_failed} 失败，未自动发布`)
        }
        loadTopology(activeFlow)
      } catch { toast.error('批量校验发布失败') }
      finally { setBatchRunning(false) }
    }

    if (batchScope === 'all_pending') {
      setConfirmDialog({
        open: true,
        title: '确认全局同步校验并发布',
        message: '将先同步代码注册表，再对所有流程中的待处理节点执行校验，全部通过则自动发布。\n此操作影响范围较大，请确认继续。',
        confirmLabel: '确认同步校验并发布',
        onConfirm: () => { setConfirmDialog(prev => ({ ...prev, open: false })); doRun() },
      })
    } else {
      doRun()
    }
  }, [batchScope, activeFlow, viewMode, loadTopology])

  const executePublishPassed = useCallback(async () => {
    if (!batchResult) return
    const passedKeys = batchResult.items.filter(i => i.status === 'validated').map(i => i.node_key)
    if (passedKeys.length === 0) return

    setPublishingPassed(true)
    try {
      const result = await llmCenterService.batchPublishPassed({ node_keys: passedKeys })
      setBatchResult(result)
      if (result.summary.published > 0) {
        toast.success(`${result.summary.published} 个节点已发布`)
      }
      loadTopology(activeFlow)
    } catch { toast.error('发布已通过节点失败') }
    finally { setPublishingPassed(false) }
  }, [batchResult, activeFlow, loadTopology])

  const totalNodes = topology?.stages.reduce((sum, s) => sum + s.nodes.length, 0) || 0
  const visibleNodes = topology?.stages.reduce((sum, s) => {
    const filtered = viewMode === 'business' ? s.nodes.filter(n => n.visibility === 'main') : s.nodes
    return sum + filtered.length
  }, 0) || 0

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">LLM Center</h1>
          <p className="text-sm text-gray-500 mt-1">LLM 编排管理 -- 全系统 LLM 调用链路透明化</p>
        </div>
        {registryStatus && (
          <div className="flex items-center gap-3 text-xs text-gray-500 bg-gray-50 rounded-lg px-4 py-2 border">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-green-400" />
              <span>Registry</span>
            </div>
            <span>{registryStatus.total_nodes} nodes</span>
            <span>{registryStatus.total_active_versions} cached</span>
          </div>
        )}
      </div>

      {/* Flow Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        {flows.map(f => (
          <div
            key={f.flow_key}
            className={`p-4 rounded-lg border cursor-pointer transition-all ${
              activeFlow === f.flow_key
                ? 'border-primary bg-primary/5 shadow-sm'
                : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
            onClick={() => setActiveFlow(f.flow_key)}
          >
            <div className="flex items-center justify-between">
              <h3 className="font-medium text-gray-900">{f.name}</h3>
              <Cpu className={`h-4 w-4 ${activeFlow === f.flow_key ? 'text-primary' : 'text-gray-400'}`} />
            </div>
            <div className="flex gap-4 mt-2 text-xs text-gray-500">
              <span>{f.stage_count} stages</span>
              <span>{f.node_count} nodes</span>
            </div>
          </div>
        ))}
      </div>

      {/* Batch Actions Bar */}
      <div className="flex items-center justify-between mb-4 p-3 bg-white rounded-lg border border-gray-200">
        <div className="flex items-center gap-2">
          <select
            value={batchScope}
            onChange={e => setBatchScope(e.target.value as 'flow' | 'all_pending')}
            className="text-xs border border-gray-300 rounded-md px-2 py-1.5 text-gray-700 focus:ring-1 focus:ring-primary focus:border-primary"
          >
            <option value="flow">当前流程</option>
            <option value="all_pending">全部待处理节点</option>
          </select>
          <button
            onClick={executeBatchValidate}
            disabled={batchRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-blue-300 text-blue-700 bg-blue-50 rounded-md hover:bg-blue-100 disabled:opacity-50"
          >
            {batchRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            一键校验
          </button>
          <button
            onClick={executeBatchValidatePublish}
            disabled={batchRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-primary rounded-md hover:bg-primary/90 disabled:opacity-50"
          >
            {batchRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
            同步校验并发布
          </button>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{visibleNodes}/{totalNodes} nodes</span>
          <div className="flex bg-gray-100 rounded-md p-0.5">
            <button
              onClick={() => setViewMode('business')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                viewMode === 'business' ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500'
              }`}
            >
              <Eye className="h-3.5 w-3.5" /> 业务视图
            </button>
            <button
              onClick={() => setViewMode('technical')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors ${
                viewMode === 'technical' ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500'
              }`}
            >
              <Code2 className="h-3.5 w-3.5" /> 技术视图
            </button>
          </div>
        </div>
      </div>

      {/* Controls - Flow Tabs */}
      <div className="flex items-center gap-2 mb-4">
        {FLOW_TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveFlow(t.key)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              activeFlow === t.key ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Batch Result Panel */}
      {batchResult && (
        <BatchResultPanel
          result={batchResult}
          onLocateNode={locateNode}
          onPublishPassed={executePublishPassed}
          publishingPassed={publishingPassed}
          onDismiss={() => setBatchResult(null)}
        />
      )}

      {/* Flow Content */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 min-h-[400px]">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="h-8 w-8 animate-spin rounded-full border-3 border-primary border-t-transparent" />
          </div>
        ) : topology ? (
          <div>
            {topology.stages.map(stage => (
              <StageAccordion
                key={stage.stage_key}
                stage={stage}
                viewMode={viewMode}
                edges={topology.edges}
                expanded={expandedStages.has(stage.stage_key)}
                onToggle={() => toggleStage(stage.stage_key)}
                onNodeClick={setSelectedNode}
              />
            ))}
          </div>
        ) : (
          <p className="text-center text-gray-400 py-16">无流程数据</p>
        )}
      </div>

      {/* Node Detail Drawer */}
      <NodeDrawer
        nodeKey={selectedNode}
        onClose={() => setSelectedNode(null)}
        onChanged={() => loadTopology(activeFlow)}
      />

      {/* Confirm Dialog */}
      <ConfirmDialog
        open={confirmDialog.open}
        title={confirmDialog.title}
        message={confirmDialog.message}
        confirmLabel={confirmDialog.confirmLabel}
        onConfirm={confirmDialog.onConfirm}
        onCancel={() => setConfirmDialog(prev => ({ ...prev, open: false }))}
      />
    </div>
  )
}

export default LlmCenterPage
