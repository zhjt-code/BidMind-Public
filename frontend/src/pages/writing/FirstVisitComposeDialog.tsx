/**
 * 职责: 内容编辑页首访引导弹窗
 * 不做: 业务请求、生成/门禁逻辑
 *
 * 收敛期从 WritingCompose.tsx 原样迁出（复杂度护栏: 页面超 800 行）。
 */
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { useFirstVisitDialog } from '@/hooks/useFirstVisitDialog'

export default function FirstVisitComposeDialog() {
  const { open, dismiss } = useFirstVisitDialog('bidmind_writing_guide_compose')
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) dismiss() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>内容编辑说明</DialogTitle>
          <DialogDescription className="text-sm text-foreground leading-relaxed">
            AI 已根据目录和素材生成各章节内容。此页面仅用于做必要的文字修正。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-3 p-3 bg-muted rounded-lg space-y-1.5 text-sm text-foreground/80">
          <p className="text-sm font-medium text-foreground mb-1">操作提示</p>
          <p>&middot; 如无明显错误，建议保持 AI 生成内容原样</p>
          <p>&middot; AI 已按章节匹配图片并在正文显示，可悬停移除或用工具栏补充</p>
          <p>&middot; 最终排版格式将在渲染阶段统一处理</p>
          <p>&middot; 左侧目录树可快速定位到具体章节</p>
        </div>
        <DialogFooter>
          <Button onClick={dismiss}>知道了</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
