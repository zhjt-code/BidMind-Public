/**
 * 单条要求来源页码超过内联展示上限时,展示全部页码的弹窗。
 *
 * 与主 PDF 预览 Dialog 解耦: 本弹窗点击任一页码会触发
 * onOpenPage 关闭自身并打开主预览。
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
import SourcePageBadge from '@/components/SourcePageBadge'
import type { Requirement } from '../../requirementReviewTransforms'

export interface AllSourcePagesDialogProps {
  /** null 表示关闭;非空时打开,且使用其 sourcePages */
  requirement: Requirement | null
  onClose: () => void
  /** 项目级招标文档 ID,用于 documentId 兜底判断 canPreview */
  tenderDocId: number | null
  /** 点击某页时触发 (打开主 PDF 预览并关闭本弹窗) */
  onOpenPage: (req: Requirement, page: number) => void
}

export const AllSourcePagesDialog: React.FC<AllSourcePagesDialogProps> = ({
  requirement,
  onClose,
  tenderDocId,
  onOpenPage,
}) => {
  return (
    <Dialog
      open={!!requirement}
      onOpenChange={(open) => { if (!open) onClose() }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>全部来源页码</DialogTitle>
          <DialogDescription className="text-xs text-gray-500 leading-relaxed">
            该要求由多处原文合并而来，请点击页码逐页核对招标原文。
          </DialogDescription>
        </DialogHeader>
        {requirement && (
          <div className="flex flex-wrap gap-2 py-2">
            {requirement.sourcePages.map((page) => {
              const docId = requirement.documentId ?? tenderDocId
              const canPreview = docId != null
              return (
                <SourcePageBadge
                  key={page}
                  label={`P${page}`}
                  variant="pill"
                  onClick={
                    canPreview
                      ? () => {
                          onOpenPage(requirement, page)
                          onClose()
                        }
                      : undefined
                  }
                  title={canPreview ? `打开招标文件第 ${page} 页` : '招标文档未就绪，无法打开预览'}
                />
              )
            })}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
