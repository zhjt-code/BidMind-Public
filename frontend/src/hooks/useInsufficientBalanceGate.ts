/**
 * 职责: 页面级余额不足门禁状态编排（捕获/清除/会话恢复/充值入口/toast 去重）
 * 不做: 发起业务请求、渲染 UI、查询钱包、充值到账后自动继续消费
 */
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { INSUFFICIENT_BALANCE_TOAST } from '@/lib/balanceGateCopy'
import {
  INSUFFICIENT_BALANCE_TOAST_ID,
  clearBalanceGateSnapshot,
  parseInsufficientBalanceError,
  readBalanceGateSnapshot,
  writeBalanceGateSnapshot,
  type InsufficientBalanceInfo,
} from '@/lib/insufficientBalance'

export interface BalanceGateController {
  /** 当前阻断信息；null 表示未被余额阻断 */
  blocked: InsufficientBalanceInfo | null
  isBlocked: boolean
  /** 捕获错误；是余额不足则落阻断态并返回 true，否则返回 false 交还调用方 */
  capture: (error: unknown) => boolean
  /** 任务成功启动/节点已越过时清除阻断态与会话快照 */
  clear: () => void
  /** 打开全局充值弹窗 */
  openRecharge: () => void
}

interface UseInsufficientBalanceGateOptions {
  projectId: number
  /** 节点标识，用于会话快照隔离（同页多入口用不同 nodeKey） */
  nodeKey: string
  /** 支付关闭时 capture 不自动弹充值窗 */
  paymentEnabled?: boolean
  /** capture 命中时 toast 的前缀，如「启动失败」；空则只用定性点数不足文案 */
  toastPrefix?: string
}

function toInfo(snapshot: ReturnType<typeof readBalanceGateSnapshot>): InsufficientBalanceInfo | null {
  return snapshot
    ? { message: snapshot.message, available: snapshot.available, required: snapshot.required }
    : null
}

export function useInsufficientBalanceGate({
  projectId,
  nodeKey,
  paymentEnabled = true,
  toastPrefix = '',
}: UseInsufficientBalanceGateOptions): BalanceGateController {
  const [blocked, setBlocked] = useState<InsufficientBalanceInfo | null>(
    () => toInfo(readBalanceGateSnapshot(projectId, nodeKey)),
  )

  // projectId/nodeKey 切换时重新从会话快照恢复，防止跨项目携带陈旧阻断
  useEffect(() => {
    setBlocked(toInfo(readBalanceGateSnapshot(projectId, nodeKey)))
  }, [projectId, nodeKey])

  const capture = useCallback((error: unknown): boolean => {
    const info = parseInsufficientBalanceError(error)
    if (!info) return false
    setBlocked(info)
    writeBalanceGateSnapshot({ projectId, nodeKey, ...info })
    toast.error(
      toastPrefix ? `${toastPrefix}: ${INSUFFICIENT_BALANCE_TOAST}` : INSUFFICIENT_BALANCE_TOAST,
      { id: INSUFFICIENT_BALANCE_TOAST_ID },
    )
    if (paymentEnabled) return true
    return true
  }, [projectId, nodeKey, paymentEnabled, toastPrefix])

  const clear = useCallback(() => {
    setBlocked(null)
    clearBalanceGateSnapshot(projectId, nodeKey)
  }, [projectId, nodeKey])

  const openRecharge = useCallback(() => {
    if (!paymentEnabled) return
  }, [paymentEnabled])

  return {
    blocked,
    isBlocked: blocked !== null,
    capture,
    clear,
    openRecharge,
  }
}
