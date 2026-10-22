/**
 * 写标素材重试状态 hook。
 *
 * 抽自 WritingMaterials.tsx 的 retryState / retrying / refreshingRetryState 状态及
 * refreshRetryState / handleRetryMaterialProcess / handleRefreshRetryState 三个动作。
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { parseInsufficientBalanceError } from '@/lib/insufficientBalance'
import { writingService } from '@/services/writingApi'
import type { MaterialBatchStatus } from '@/services/writingApi'
import type { WritingRetryState } from '@/services/writingRetryTransforms'

interface UseWritingMaterialsRetryOptions {
  pid: number
  /** 用于刷新状态后同步 batchStatus（与原页面 setBatchStatus 行为一致） */
  onBatchStatusSync: (status: MaterialBatchStatus) => void
  /** 刷新后同步 store 中 materials / checklist */
  onMaterialsReload: () => Promise<void>
  /** 触发重试时启动轮询（同时收起列表/通知折叠）；仅在重试成功启动后调用 */
  onRetryStart: () => void
  /** 重试启动命中余额不足 403 时的阻断回调（命中则跳过通用失败 toast） */
  onBalanceBlocked?: (error: unknown) => void
}

export interface WritingMaterialsRetryController {
  retryState: WritingRetryState | null
  retrying: boolean
  refreshingRetryState: boolean
  setRetryState: React.Dispatch<React.SetStateAction<WritingRetryState | null>>
  /** 主动刷新最新的 retry-state（用于首屏 + projectStatus=failed 时增强失败态展示） */
  refreshRetryState: () => Promise<WritingRetryState>
  /** 用户点击"刷新状态"按钮：拉取最新 status + reload materials/checklist */
  handleRefreshRetryState: () => Promise<void>
  /** 用户点击"重试"按钮：调用 retryWritingNode 并启动轮询 */
  handleRetryMaterialProcess: () => Promise<void>
}

export function useWritingMaterialsRetry(
  options: UseWritingMaterialsRetryOptions,
): WritingMaterialsRetryController {
  const { pid, onBatchStatusSync, onMaterialsReload, onRetryStart, onBalanceBlocked } = options
  const [retryState, setRetryState] = useState<WritingRetryState | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [refreshingRetryState, setRefreshingRetryState] = useState(false)

  const refreshRetryState = useCallback(async () => {
    const state = await writingService.getRetryState(pid, 'writing_material_process')
    setRetryState(state)
    return state
  }, [pid])

  const handleRefreshRetryState = useCallback(async () => {
    setRefreshingRetryState(true)
    try {
      const status = await writingService.getMaterialsProcessStatus(pid)
      onBatchStatusSync(status)
      setRetryState(status)
      await onMaterialsReload()
      toast.success('状态已刷新')
    } catch {
      toast.error('刷新状态失败')
    } finally {
      setRefreshingRetryState(false)
    }
  }, [pid, onBatchStatusSync, onMaterialsReload])

  const handleRetryMaterialProcess = useCallback(async () => {
    setRetrying(true)
    try {
      const result = await writingService.retryWritingNode(pid, 'writing_material_process')
      setRetryState(result)
      toast.success(result.message || '素材处理重试已启动')
      onRetryStart()
    } catch (e) {
      // 余额不足 403 交给门禁回调阻断，跳过通用 toast；其余错误保持原提示
      if (parseInsufficientBalanceError(e) && onBalanceBlocked) {
        onBalanceBlocked(e)
      } else {
        toast.error('重试启动失败')
      }
      await refreshRetryState().catch(() => undefined)
    } finally {
      setRetrying(false)
    }
  }, [pid, onRetryStart, onBalanceBlocked, refreshRetryState])

  return {
    retryState,
    retrying,
    refreshingRetryState,
    setRetryState,
    refreshRetryState,
    handleRefreshRetryState,
    handleRetryMaterialProcess,
  }
}
