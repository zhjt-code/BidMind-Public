/**
 * 单条 binding 操作 hook（confirm / reject / assign）。
 *
 * 从 WritingBinding.tsx L240-320 整段迁出（行为 byte-equal）。
 * 内聚 pendingBindingAction state + 两个 store helpers + 三个操作回调。
 *
 * 不内聚批量确认（handleConfirmAll）—— 那部分在 useBatchConfirmFlow 中，与对话框 state 同处。
 *
 * Plan §三 Q1 决议（用户批准方案 B）：单条 vs 批量 hook 拆分，hook 总数从卡片明文 4 个变 5 个。
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { useWritingStore } from '@/stores'
import { writingService, type WritingBinding as WritingBindingType } from '@/services/writingApi'
import {
  applyRejectedBindingDecision,
  isBindingNotFoundError,
  removeBindingById,
  type PendingBindingAction,
} from '../../writingBindingState'

interface UseBindingActionsInputs {
  pid: number
}

export interface UseBindingActionsResult {
  pendingBindingAction: PendingBindingAction | null
  setPendingBindingAction: React.Dispatch<React.SetStateAction<PendingBindingAction | null>>
  hasLocalAction: boolean
  handleConfirmOne: (bindingId: number) => Promise<void>
  handleReject: (bindingId: number) => Promise<void>
  handleRestore: (bindingId: number) => Promise<void>
  handleAssignToChapter: (bindingId: number, nodeId: number) => Promise<void>
}

export function useBindingActions({ pid }: UseBindingActionsInputs): UseBindingActionsResult {
  const [pendingBindingAction, setPendingBindingAction] = useState<PendingBindingAction | null>(null)

  const patchBindingInStore = useCallback((bindingId: number, patch: Partial<WritingBindingType>) => {
    useWritingStore.setState((state) => ({
      bindings: state.bindings.map((b) => (
        b.id === bindingId ? { ...b, ...patch } : b
      )),
    }))
  }, [])

  const replaceBindingInStore = useCallback((updated: WritingBindingType) => {
    useWritingStore.setState((state) => ({
      bindings: state.bindings.map((b) => (
        b.id === updated.id ? updated : b
      )),
    }))
  }, [])

  const handleConfirmOne = useCallback(async (bindingId: number) => {
    setPendingBindingAction({ bindingId, action: 'confirm' })
    try {
      const result = await writingService.confirmBindings(pid, [bindingId])
      if (result.confirmed === 0) {
        useWritingStore.setState((state) => ({
          bindings: removeBindingById(state.bindings, bindingId),
        }))
        toast.info('绑定已更新')
        return
      }
      patchBindingInStore(bindingId, { status: 'confirmed' })
      toast.success('已确认')
    } catch (error) {
      if (isBindingNotFoundError(error)) {
        useWritingStore.setState((state) => ({
          bindings: removeBindingById(state.bindings, bindingId),
        }))
        toast.info('绑定已更新')
      } else {
        toast.error('确认失败')
      }
    } finally {
      setPendingBindingAction(null)
    }
  }, [pid, patchBindingInStore])

  const handleReject = useCallback(async (bindingId: number) => {
    setPendingBindingAction({ bindingId, action: 'reject' })
    try {
      await writingService.updateBinding(pid, bindingId, { status: 'rejected' })
      useWritingStore.setState((state) => ({
        bindings: applyRejectedBindingDecision(state.bindings, bindingId),
      }))
      toast.success('已拒绝')
    } catch (error) {
      if (isBindingNotFoundError(error)) {
        useWritingStore.setState((state) => ({
          bindings: removeBindingById(state.bindings, bindingId),
        }))
        toast.info('绑定已更新')
      } else {
        toast.error('操作失败')
      }
    } finally {
      setPendingBindingAction(null)
    }
  }, [pid])

  const handleRestore = useCallback(async (bindingId: number) => {
    setPendingBindingAction({ bindingId, action: 'restore' })
    try {
      const updated = await writingService.updateBinding(pid, bindingId, { status: 'suggested' })
      replaceBindingInStore(updated)
      toast.success('已恢复为待处理')
    } catch (error) {
      if (isBindingNotFoundError(error)) {
        useWritingStore.setState((state) => ({
          bindings: removeBindingById(state.bindings, bindingId),
        }))
        toast.info('绑定已更新')
      } else {
        toast.error('恢复失败')
      }
    } finally {
      setPendingBindingAction(null)
    }
  }, [pid, replaceBindingInStore])

  const handleAssignToChapter = useCallback(async (bindingId: number, nodeId: number) => {
    setPendingBindingAction({ bindingId, action: 'assign' })
    try {
      const updated = await writingService.updateBinding(pid, bindingId, {
        outline_node_id: nodeId,
        scope: null,
        status: 'suggested',
      })
      replaceBindingInStore(updated)
      toast.success('已分配到章节')
    } catch (error) {
      if (isBindingNotFoundError(error)) {
        useWritingStore.setState((state) => ({
          bindings: removeBindingById(state.bindings, bindingId),
        }))
        toast.info('绑定已更新')
      } else {
        toast.error('分配失败')
      }
    } finally {
      setPendingBindingAction(null)
    }
  }, [pid, replaceBindingInStore])

  return {
    pendingBindingAction,
    setPendingBindingAction,
    hasLocalAction: pendingBindingAction != null,
    handleConfirmOne,
    handleReject,
    handleRestore,
    handleAssignToChapter,
  }
}
