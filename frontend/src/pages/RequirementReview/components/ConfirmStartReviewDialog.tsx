/**
 * 开始审查前确认弹窗（仅未查看风险告知时出现）。
 *
 * 职责: 提示存在不参与审查但含风险语义的条款，提供返回查看或继续开始审查。
 * 不做: 不直接调用确认/启动审查 API；不持有风险条款明细。
 */
import React from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

export interface ConfirmStartReviewDialogProps {
  open: boolean
  confirming: boolean
  riskCount: number
  onReturnToView: () => void
  onContinue: () => void
  onDismiss: () => void
}

export const ConfirmStartReviewDialog: React.FC<ConfirmStartReviewDialogProps> = ({
  open,
  confirming,
  riskCount,
  onReturnToView,
  onContinue,
  onDismiss,
}) => {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onDismiss()
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>开始审查前确认</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            系统识别到 {riskCount} 条风险告知条款。该条款不参与本次审查打分，但含否决/程序风险语义，建议核对后再开始审查。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          你可以返回查看风险告知，也可以继续开始审查。
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onReturnToView} disabled={confirming}>
            返回查看
          </Button>
          <Button onClick={onContinue} disabled={confirming}>
            {confirming ? '处理中...' : '继续开始审查'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
