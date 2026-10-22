/**
 * 重新匹配确认对话框。
 *
 * 职责: 让用户在"保留人工审核重新匹配"与"重置审核并重新匹配"之间显式选择。
 * 不做: 不发起请求、不轮询、不持有匹配状态机。
 *
 * : 默认操作保留 confirmed/rejected，仅重算 suggested；重置为危险操作，
 * 需用户明确选择后才清空全部绑定。
 */

import React from 'react'
import { Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { getRematchModeContent } from '../../writingRematchPolicy'

export interface RematchConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  matching: boolean
  onRematch: (resetReviewedBindings: boolean) => void
}

export const RematchConfirmDialog: React.FC<RematchConfirmDialogProps> = ({
  open, onOpenChange, matching, onRematch,
}) => {
  const keep = getRematchModeContent('keep')
  const reset = getRematchModeContent('reset')
  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (!matching) onOpenChange(next)
    }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重新匹配</DialogTitle>
          <DialogDescription>
            请选择重新匹配的方式。默认会保留你已经审核过的绑定。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="font-medium">{keep.confirmLabel}</p>
            <p className="mt-1 text-xs text-muted-foreground">{keep.description}</p>
          </div>
          <div className="rounded-lg border border-red-200 bg-red-50 p-3">
            <p className="font-medium text-red-700">{reset.confirmLabel}</p>
            <p className="mt-1 text-xs text-red-700">{reset.description}</p>
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={matching}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={() => onRematch(true)}
            disabled={matching}
          >
            {matching ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Trash2 className="h-4 w-4 mr-1" />}
            {reset.confirmLabel}
          </Button>
          <Button onClick={() => onRematch(false)} disabled={matching}>
            {matching ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-1" />}
            {keep.confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
