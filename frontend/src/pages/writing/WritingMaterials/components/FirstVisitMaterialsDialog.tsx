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
import { useFirstVisitDialog } from '@/hooks/useFirstVisitDialog'

export const FirstVisitMaterialsDialog: React.FC = () => {
  const { open, dismiss } = useFirstVisitDialog('bidmind_writing_guide_materials')
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) dismiss() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>素材上传建议</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            素材质量直接影响 AI 生成内容的专业性和准确性。请尽量按招标要求上传对应素材文件。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-3 p-3 bg-muted rounded-lg space-y-1.5 text-sm text-foreground/80">
          <p className="text-sm font-medium text-foreground mb-1">操作提示</p>
          <p>&middot; 文件名称应清晰规范（如"营业执照"而非"IMG_001"）</p>
          <p>&middot; 规范的命名可显著提升 AI 的章节匹配精度</p>
          <p>&middot; 页面下方列出了招标文件要求的素材清单，可对照上传</p>
        </div>
        <DialogFooter>
          <Button onClick={dismiss}>知道了</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
