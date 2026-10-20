/**
 * 批量确认流 hook：handleConfirmAll + 对话框 state。
 *
 * 从 WritingBinding.tsx 整段迁出（行为 byte-equal）：
 *   - batchConfirmOpen / batchConfirming state
 *   - handleConfirmAll: 过滤 status=suggested && outlineNodeId!=null → 一次性 confirm → reload bindings
 *
 * 与 useBindingActions 共享 setPendingBindingAction 入口（通过 props 传入），
 * 避免拆分后两个 hook 同时持有 pendingBindingAction state 互不感知导致状态不一致。
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { writingService, type WritingBinding as WritingBindingType } from '@/services/writingApi'
import type { PendingBindingAction } from '../../writingBindingState'

interface UseBatchConfirmFlowInputs {
  pid: number
  bindings: WritingBindingType[]
  loadBindings: (pid: number) => Promise<void>
  setPendingBindingAction: React.Dispatch<React.SetStateAction<PendingBindingAction | null>>
}

export interface UseBatchConfirmFlowResult {
  batchConfirmOpen: boolean
  setBatchConfirmOpen: React.Dispatch<React.SetStateAction<boolean>>
  batchConfirming: boolean
  handleConfirmAll: () => Promise<void>
}

export function useBatchConfirmFlow({
  pid,
  bindings,
  loadBindings,
  setPendingBindingAction,
}: UseBatchConfirmFlowInputs): UseBatchConfirmFlowResult {
  const [batchConfirmOpen, setBatchConfirmOpen] = useState(false)
  const [batchConfirming, setBatchConfirming] = useState(false)

  const handleConfirmAll = useCallback(async () => {
    const toConfirm = bindings.filter(
      (b) => b.status === 'suggested' && b.outlineNodeId != null,
    )
    if (toConfirm.length === 0) {
      toast.info('没有待确认的绑定')
      return
    }
    setBatchConfirming(true)
    setPendingBindingAction({ bindingId: null, action: 'batch-confirm' })
    try {
      await writingService.confirmBindings(pid, toConfirm.map((b) => b.id))
      await loadBindings(pid).catch(() => {})
      setBatchConfirmOpen(false)
      toast.success(`已确认 ${toConfirm.length} 条绑定`)
    } catch {
      toast.error('确认失败')
    } finally {
      setPendingBindingAction(null)
      setBatchConfirming(false)
    }
  }, [pid, bindings, loadBindings, setPendingBindingAction])

  return {
    batchConfirmOpen,
    setBatchConfirmOpen,
    batchConfirming,
    handleConfirmAll,
  }
}
