import React, { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Lightbulb, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import {
  opsService,
  type LlmWeeklyAdviceItem,
  type LlmAdvicePreview,
} from '@/services/opsApi'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import {
  formatAdviceActionLabel,
  formatAdviceRuleLabel,
  formatAdviceStatusLabel,
  formatPurposeLabel,
  LLM_PURPOSE_LABELS,
} from '../reliabilityLabels'
import {
  adviceBoardWeekLabel,
  boardNodeItems,
  boardPurposeItems,
} from '../weeklyAdviceBoard'

const PRIORITY_LABELS: Record<number, string> = {
  0: '默认首选',
  50: '次选',
  100: '普通',
  200: '备用',
  999: '末位备选',
}

function formatConfigHint(configId: string): string {
  for (const [purpose, label] of Object.entries(LLM_PURPOSE_LABELS)) {
    if (configId.startsWith(`${purpose}_`)) {
      return `${label}配置`
    }
  }
  return '模型配置'
}

function formatPriorityMap(map: Record<string, number> | null | undefined): string {
  if (!map || Object.keys(map).length === 0) return '（空）'
  return Object.entries(map)
    .map(([cid, p]) => `${formatConfigHint(cid)}: ${PRIORITY_LABELS[p] ?? p}`)
    .join('\n')
}

function adviceTargetLabel(item: LlmWeeklyAdviceItem): string {
  if (item.target_display) return item.target_display
  if (item.scope === 'purpose') return formatPurposeLabel(item.target)
  return item.target
}

function adviceRuleLabel(item: LlmWeeklyAdviceItem): string {
  return item.rule_label || formatAdviceRuleLabel(item.rule_id)
}

function adviceStatusLabel(item: LlmWeeklyAdviceItem): string {
  return item.status_label || formatAdviceStatusLabel(item.status)
}

function adviceActionLabel(item: LlmWeeklyAdviceItem): string {
  return item.advice_action_label || formatAdviceActionLabel(item.advice_action)
}

function recommendedConfigLabel(item: LlmWeeklyAdviceItem): string | null {
  if (!item.recommended_config_id) return null
  return item.recommended_config_display || formatConfigHint(item.recommended_config_id)
}

function demoteConfigLabel(item: LlmWeeklyAdviceItem): string | null {
  if (!item.demote_config_id) return null
  return item.demote_config_display || formatConfigHint(item.demote_config_id)
}

const WeeklyAdviceCard: React.FC = () => {
  const queryClient = useQueryClient()
  const [confirmItem, setConfirmItem] = useState<LlmWeeklyAdviceItem | null>(null)
  const [preview, setPreview] = useState<LlmAdvicePreview | null>(null)

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['llm-weekly-advice'],
    queryFn: () => opsService.getLlmAdvice(),
    staleTime: 60_000,
  })

  const items = data?.items ?? []
  const purposeItems = useMemo(() => boardPurposeItems(items), [items])
  const nodeItems = useMemo(() => boardNodeItems(items, 5), [items])
  const weekLabel = useMemo(() => adviceBoardWeekLabel(purposeItems), [purposeItems])

  const generateMut = useMutation({
    mutationFn: () => opsService.generateLlmAdvice(),
    onSuccess: () => {
      toast.success('已生成周建议')
      queryClient.invalidateQueries({ queryKey: ['llm-weekly-advice'] })
    },
    onError: (e: unknown) => toast.error(`生成失败: ${(e as Error).message || '未知错误'}`),
  })

  const applyMut = useMutation({
    mutationFn: async () => {
      if (!confirmItem || !preview?.current?.fingerprint) {
        throw new Error('缺少确认指纹')
      }
      return opsService.applyLlmAdvice(confirmItem.id, preview.current.fingerprint)
    },
    onSuccess: () => {
      toast.success('已写回调度优先级')
      setConfirmItem(null)
      setPreview(null)
      queryClient.invalidateQueries({ queryKey: ['llm-weekly-advice'] })
    },
    onError: (e: unknown) => toast.error(`写回失败: ${(e as Error).message || '未知错误'}`),
  })

  const rollbackMut = useMutation({
    mutationFn: (id: number) => opsService.rollbackLlmAdvice(id),
    onSuccess: () => {
      toast.success('已回滚优先级')
      queryClient.invalidateQueries({ queryKey: ['llm-weekly-advice'] })
    },
    onError: (e: unknown) => toast.error(`回滚失败: ${(e as Error).message || '未知错误'}`),
  })

  const openConfirm = async (item: LlmWeeklyAdviceItem) => {
    try {
      const p = await opsService.previewLlmAdvice(item.id)
      setPreview(p)
      setConfirmItem(item)
    } catch (e) {
      toast.error(`预览失败: ${(e as Error).message || '未知错误'}`)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <Lightbulb className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-medium text-gray-700">本周模型调度建议</span>
          <span className="text-xs text-gray-400">算法真源 · 人工确认后写回</span>
          {weekLabel ? (
            <span className="text-xs text-gray-400">统计周 {weekLabel}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => generateMut.mutate()}
            disabled={generateMut.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {generateMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            立即分析
          </button>
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"
            disabled={isFetching}
          >
            <RefreshCw className={`h-3 w-3 ${isFetching ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-gray-400 py-4 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载建议…
        </div>
      ) : purposeItems.length === 0 && nodeItems.length === 0 ? (
        <div className="text-xs text-gray-400 py-3">
          暂无周建议。可点击「立即分析」基于近一周生产流量生成。
        </div>
      ) : (
        <div className="space-y-3">
          {purposeItems.map((item) => {
            const recLabel = recommendedConfigLabel(item)
            const demLabel = demoteConfigLabel(item)
            return (
              <div
                key={item.id}
                className="rounded-md border border-gray-100 bg-gray-50/50 px-3 py-2 text-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium text-gray-800">
                      用途池 · {adviceTargetLabel(item)}
                      <span className="ml-2 text-xs font-normal text-gray-500">
                        {adviceActionLabel(item)}
                      </span>
                      <span className="ml-2 text-[11px] text-gray-400">
                        状态 {adviceStatusLabel(item)}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-gray-600">
                      规则 {adviceRuleLabel(item)}
                      {recLabel ? ` · 推荐配置 ${recLabel}` : ''}
                      {demLabel ? ` · 降权 ${demLabel}` : ''}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {item.writable && item.status === 'pending' && (
                      <button
                        type="button"
                        onClick={() => openConfirm(item)}
                        className="rounded px-2 py-1 text-xs bg-primary text-white hover:opacity-90"
                      >
                        确认写回
                      </button>
                    )}
                    {item.status === 'applied' && (
                      <button
                        type="button"
                        onClick={() => rollbackMut.mutate(item.id)}
                        disabled={rollbackMut.isPending}
                        className="rounded px-2 py-1 text-xs border border-amber-300 text-amber-800 hover:bg-amber-50"
                      >
                        回滚
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}

          {nodeItems.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1">节点级建议（只读，不写回）</div>
              <ul className="space-y-1">
                {nodeItems.map((item) => {
                  const recLabel = recommendedConfigLabel(item)
                  return (
                    <li key={item.id} className="text-xs text-gray-600 truncate" title={item.target}>
                      {adviceTargetLabel(item)} · {adviceActionLabel(item)}
                      {recLabel ? ` · ${recLabel}` : ''}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </div>
      )}

      <Dialog open={!!confirmItem} onOpenChange={(open) => !open && setConfirmItem(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>二次确认：写回调度优先级</DialogTitle>
          </DialogHeader>
          {confirmItem && preview && (
            <div className="space-y-3 text-sm">
              <p className="text-gray-600">
                用途池 <strong>{adviceTargetLabel(confirmItem)}</strong>
                ，动作 <strong>{adviceActionLabel(confirmItem)}</strong>。
                写回前已拍摄快照，可一键回滚。
              </p>
              <div>
                <div className="text-xs text-gray-500 mb-1">当前优先级（变更前）</div>
                <pre className="text-[11px] bg-gray-50 border rounded p-2 whitespace-pre-wrap max-h-32 overflow-auto">
                  {formatPriorityMap(preview.current.priority)}
                </pre>
              </div>
              <div>
                <div className="text-xs text-gray-500 mb-1">建议优先级（变更后）</div>
                <pre className="text-[11px] bg-gray-50 border rounded p-2 whitespace-pre-wrap max-h-32 overflow-auto">
                  {formatPriorityMap(preview.proposed_priority)}
                </pre>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmItem(null)}>取消</Button>
            <Button onClick={() => applyMut.mutate()} disabled={applyMut.isPending}>
              {applyMut.isPending ? '写入中…' : '确认写回'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default WeeklyAdviceCard
