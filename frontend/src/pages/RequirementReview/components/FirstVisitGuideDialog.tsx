/**
 * 首次进入招标要求确认页的引导提示弹窗。
 *
 * 由 useFirstVisitDialog('bidmind_guide_requirement_review') 控制 open / dismiss,
 * 调用方负责传入 open + onDismiss。
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

export interface FirstVisitGuideDialogProps {
  open: boolean
  onDismiss: () => void
}

export const FirstVisitGuideDialog: React.FC<FirstVisitGuideDialogProps> = ({
  open,
  onDismiss,
}) => {
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onDismiss() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>确认前提醒</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            招标要求是后续 AI 审查的基准，其准确性将直接影响审查质量。请您务必对照招标文件原文，逐项核实以下 AI 提取的内容。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-3 p-3 bg-muted rounded-lg space-y-1.5 text-sm text-foreground/80">
          <p className="text-sm font-medium text-foreground mb-1">操作提示</p>
          <p>&middot; 点击否决项标记可切换是否为否决项</p>
          <p>&middot; 点击条目内容或编辑按钮可修改提取结果</p>
          <p>&middot; 重复的条目可直接删除</p>
          <p>&middot; 如有遗漏，可点击&quot;添加&quot;手动补充招标要求</p>
        </div>
        <DialogFooter>
          <Button onClick={onDismiss}>知道了</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
