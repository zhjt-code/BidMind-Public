/**
 * 批量确认对话框组件。
 * 从 WritingBinding.tsx L435-474 整段迁出（行为 byte-equal）。
 */

import React from 'react'
import { Check, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

export interface BatchConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  batchConfirming: boolean
  toConfirmCount: number
  toConfirmHighConf: number
  toConfirmLowConf: number
  conflictCount: number
  onConfirmAll: () => void
}

export const BatchConfirmDialog: React.FC<BatchConfirmDialogProps> = ({
  open, onOpenChange, batchConfirming, toConfirmCount, toConfirmHighConf, toConfirmLowConf, conflictCount, onConfirmAll,
}) => {
  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (!batchConfirming) onOpenChange(next)
    }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>确认全部待确认绑定</DialogTitle>
          <DialogDescription>
            系统将一次性确认当前已关联章节的待确认绑定。确认后可继续逐条拒绝不合适的绑定。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div className="rounded-lg border bg-muted/30 p-3">
            <p className="text-xs text-muted-foreground">待确认</p>
            <p className="mt-1 text-lg font-semibold">{toConfirmCount}</p>
          </div>
          <div className="rounded-lg border bg-green-50 p-3">
            <p className="text-xs text-green-700">高置信</p>
            <p className="mt-1 text-lg font-semibold text-green-700">{toConfirmHighConf}</p>
          </div>
          <div className="rounded-lg border bg-amber-50 p-3">
            <p className="text-xs text-amber-700">低置信</p>
            <p className="mt-1 text-lg font-semibold text-amber-700">{toConfirmLowConf}</p>
          </div>
        </div>
        {conflictCount > 0 && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            当前仍有 {conflictCount} 个潜在冲突素材。建议确认后继续复核冲突筛选结果。
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={batchConfirming}>
            取消
          </Button>
          <Button onClick={onConfirmAll} disabled={batchConfirming || toConfirmCount === 0}>
            {batchConfirming ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Check className="h-4 w-4 mr-1" />}
            确认全部
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
