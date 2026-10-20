/**
 * 重新提取招标要求的二次确认弹窗。
 *
 * 职责: 说明清空范围，并要求用户显式确认后才触发重新提取。
 * 不做: 不直接调用 API、不持有项目状态、不展示提取进度。
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

export interface ReextractConfirmDialogProps {
  open: boolean
  reextracting: boolean
  onCancel: () => void
  onConfirm: () => void
}

export const ReextractConfirmDialog: React.FC<ReextractConfirmDialogProps> = ({
  open,
  reextracting,
  onCancel,
  onConfirm,
}) => {
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onCancel() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>重新生成招标要求确认清单</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            此操作仅复用已解析文档，不会重新解析 PDF。系统会清空当前确认清单中的全部
            条目（含普通要求、评分项、已确认项和手工补录），以及投标响应、审查结果
            和相关审查中间产物，然后重新提取招标要求。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          适用于提取结果异常或系统修复后的复核场景。确认后需要重新审核新生成的清单，
          再点击“确认并开始审查”。
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={reextracting}>
            取消
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={reextracting}>
            {reextracting ? '重新提取中...' : '确认重新提取'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
