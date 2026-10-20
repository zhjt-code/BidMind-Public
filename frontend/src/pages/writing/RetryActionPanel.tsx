/**
 * 职责: 把后端的"重试状态"渲染为中断级错误弹窗（最高优先级，阻断式）
 * 不做: 不感知具体节点/阶段（由 RetryContract 承载）；不调用后端；不暴露日志/错误码等后端参数
 *
 * 设计: 与项目主题一致的居中弹窗
 *   - 三段式: 顶层错误类型(图标+标题) → 中间原因+建议 → 底部操作按钮
 *   - 全中文, 系统字体, primary 蓝主按钮, 状态语义色(蓝/橙/红)
 *   - 用户点击操作按钮后弹窗关闭, 进入对应处理流程
 */
import React, { useEffect, useRef, useState } from 'react'
import {
  AlertOctagon,
  AlertTriangle,
  Clock,
  Loader2,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import {
  FAILURE_THEME,
  type FailureKind,
  type RetryContract,
} from '@/contracts/retryContract'
import { adaptLegacyRetryState } from '@/services/writingRetryAdapter'
import type { WritingRetryState } from '@/services/writingRetryTransforms'

const KIND_ICON: Record<FailureKind, React.ComponentType<{ className?: string }>> = {
  transient: RefreshCw,
  business: AlertTriangle,
  dependency: Clock,
  fatal: AlertOctagon,
}

// ----------------------------------------------------------------------------
// Props: 向后兼容旧形态, 让 11 处调用方零改动
// ----------------------------------------------------------------------------

/** 旧版 props（过渡期保留） */
interface LegacyProps {
  state: WritingRetryState
  onRetry: () => void | Promise<void>
  onRefresh: () => void | Promise<void>
  retrying?: boolean
  refreshing?: boolean
}

/** 新版 props（契约升级后使用） */
interface ContractProps {
  contract: RetryContract
  onAction: (actionId: string) => void | Promise<void>
}

export type RetryActionPanelProps = LegacyProps | ContractProps

function isLegacyProps(props: RetryActionPanelProps): props is LegacyProps {
  return 'state' in props && !('contract' in props)
}

const RetryActionPanel: React.FC<RetryActionPanelProps> = (props) => {
  // 适配层：旧 props → 新契约
  let contract: RetryContract
  let onAction: (id: string) => void | Promise<void>

  if (isLegacyProps(props)) {
    const adapted = adaptLegacyRetryState(props.state)
    const retryRunning = (props.retrying ?? false) || adapted.retryRunning
    contract = { ...adapted, retryRunning }
    onAction = async (id: string) => {
      if (id === 'primary-retry') {
        await props.onRetry()
        return
      }
      // 稍后处理 / 我知道了：旧 API 用一次刷新重新探测状态
      await props.onRefresh()
    }
  } else {
    contract = props.contract
    onAction = props.onAction
  }

  const { context, failure, retryRunning } = contract
  const theme = FAILURE_THEME[failure.kind]
  const Icon = KIND_ICON[failure.kind]

  // 弹窗开合：初始打开；同一错误处理后不重复弹，新错误（签名变化）重新弹
  const [open, setOpen] = useState(true)
  const signature = `${context.scope}|${context.attempt}|${failure.detail}`
  const lastSignature = useRef(signature)
  useEffect(() => {
    if (lastSignature.current !== signature) {
      lastSignature.current = signature
      setOpen(true)
    }
  }, [signature])

  const primaryAction = contract.actions.find((a) => a.kind === 'primary')
  const dismissAction = contract.actions.find((a) => a.kind === 'destructive')

  const handlePrimary = () => {
    if (!primaryAction) return
    setOpen(false)
    void onAction(primaryAction.id)
  }

  const handleDismiss = () => {
    setOpen(false)
    if (dismissAction) void onAction(dismissAction.id)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-md">
        {/* ===== 顶层：错误类型（图标 + 标题同行，左对齐） ===== */}
        <DialogHeader className="space-y-0">
          <div className="flex items-center gap-3 pr-6">
            <span
              className={cn(
                'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
                theme.iconBg,
              )}
            >
              <Icon className={cn('h-5 w-5', theme.iconColor)} />
            </span>
            <div className="min-w-0 flex-1 text-left">
              <DialogTitle className="text-base font-semibold text-foreground">
                {failure.title}
              </DialogTitle>
              <p className="mt-0.5 text-sm text-muted-foreground">{theme.summary}</p>
            </div>
          </div>
        </DialogHeader>

        {/* ===== 中间：详细信息 ===== */}
        <div className="space-y-3">
          <div className="rounded-lg border bg-muted/30 p-4">
            <p className="text-xs font-medium text-muted-foreground">失败原因</p>
            <p className="mt-1.5 text-sm leading-relaxed text-foreground">
              {failure.detail}
            </p>
            {failure.hint && (
              <>
                <p className="mt-3 text-xs font-medium text-muted-foreground">处理建议</p>
                <p className="mt-1.5 text-sm leading-relaxed text-foreground">
                  {failure.hint}
                </p>
              </>
            )}
          </div>

          {context.attempt > 1 && (
            <p className="text-center text-xs text-muted-foreground">
              已尝试 {context.attempt} 次
            </p>
          )}
        </div>

        {/* ===== 底部：操作按钮 ===== */}
        <DialogFooter className="sm:justify-center sm:space-x-3">
          {dismissAction && (
            <Button
              variant="outline"
              onClick={handleDismiss}
              className="min-w-[96px]"
            >
              {dismissAction.label}
            </Button>
          )}
          {primaryAction && (
            <Button
              onClick={handlePrimary}
              disabled={retryRunning}
              className="min-w-[96px]"
            >
              {retryRunning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  重试中
                </>
              ) : (
                <>
                  <RotateCcw className="h-4 w-4" />
                  {primaryAction.label}
                </>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default RetryActionPanel
