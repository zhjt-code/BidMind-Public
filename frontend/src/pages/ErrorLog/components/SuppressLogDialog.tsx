import React from 'react'
import { Loader2 } from 'lucide-react'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { ErrorLogItem } from '../types'

interface SuppressLogDialogProps {
  target: ErrorLogItem | null
  reason: string
  submitting: boolean
  onReasonChange: (reason: string) => void
  onCancel: () => void
  onConfirm: () => void
}

export const SuppressLogDialog: React.FC<SuppressLogDialogProps> = ({
  target, reason, submitting, onReasonChange, onCancel, onConfirm,
}) => {
  return (
    <Dialog open={!!target} onOpenChange={open => { if (!open) onCancel() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>创建日志抑制规则</DialogTitle>
          <DialogDescription>
            后续同类日志将不再写入数据库，但仍保留在服务器日志文件中，可随时在抑制规则管理中恢复。
          </DialogDescription>
        </DialogHeader>
        {target && (
          <div className="space-y-4">
            <div className="bg-gray-50 rounded-lg p-3 space-y-2 text-xs">
              <div className="flex gap-2">
                <span className="text-gray-400 w-16 text-right shrink-0">匹配模块</span>
                <span className="text-gray-700 font-mono">{target.module || '-'}</span>
              </div>
              <div className="flex gap-2">
                <span className="text-gray-400 w-16 text-right shrink-0">消息前缀</span>
                <span className="text-gray-700 break-all">{(target.message || '').slice(0, 200)}</span>
              </div>
              <div className="flex gap-2">
                <span className="text-gray-400 w-16 text-right shrink-0">级别</span>
                <span className="text-gray-700">{target.level}</span>
              </div>
            </div>
            <div>
              <label className="text-sm text-gray-600 mb-1 block">抑制原因（可选）</label>
              <Textarea
                placeholder="例如：已知的 token 过期警告，不影响业务"
                value={reason}
                onChange={e => onReasonChange(e.target.value)}
                rows={2}
                maxLength={500}
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button onClick={onConfirm} disabled={submitting}>
            {submitting && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
            确认创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
