/**
 * 招标要求页 PDF 预览状态 hook。
 *
 * 从原 RequirementReview.tsx 抽出:
 *   - 主预览 Dialog 控制
 *   - showAllPagesReq : "全部 N 页"列表 Dialog 控制
 *   - handleOpenPdfPreview(req, page) : 来源页码点击入口
 *
 * 关键不变量:
 *   - handleOpenPdfPreview 通过 req.documentId ?? tenderDocId 做兜底,
 *     无 documentId 字段的历史 requirement 使用项目级招标文档 ID 兜底
 *   - 摘录用要求描述作弱针, 定位失败时预览只翻页不加整页框
 */

import { useCallback, useState, type Dispatch, type SetStateAction } from 'react'
import { documentService } from '@/services/documentApi'
import type { Requirement } from '../../requirementReviewTransforms'
import { usePdfEvidencePreview } from '../../result/pdfPreview/usePdfEvidencePreview'

export interface UseRequirementPdfPreviewOptions {
  projectId: string | undefined
  /** 项目级招标文档 ID,用于无 documentId 字段的历史 requirement 兜底 */
  tenderDocId: number | null
}

export interface UseRequirementPdfPreviewReturn {
  pdfOpen: boolean
  setPdfOpen: (open: boolean) => void
  pdfPage: number
  pdfUrl: string
  excerpts: string[]
  handleOpenPdfPreview: (req: Requirement, page: number) => void
  showAllPagesReq: Requirement | null
  setShowAllPagesReq: Dispatch<SetStateAction<Requirement | null>>
}

export function useRequirementPdfPreview(
  options: UseRequirementPdfPreviewOptions,
): UseRequirementPdfPreviewReturn {
  const { projectId, tenderDocId } = options
  const { open, setOpen, page, pdfUrl, excerpts, openPreview } = usePdfEvidencePreview()
  const [showAllPagesReq, setShowAllPagesReq] = useState<Requirement | null>(null)

  const handleOpenPdfPreview = useCallback(
    (req: Requirement, pageNumber: number) => {
      const docId = req.documentId ?? tenderDocId
      if (!docId || !pageNumber || !projectId) return
      const excerpt = (req.description || '').trim()
      openPreview({
        pdfUrl: documentService.getPreviewUrl(parseInt(projectId), docId),
        page: pageNumber,
        excerpts: excerpt ? [excerpt] : [],
      })
    },
    [openPreview, projectId, tenderDocId],
  )

  return {
    pdfOpen: open,
    setPdfOpen: setOpen,
    pdfPage: page,
    pdfUrl,
    excerpts,
    handleOpenPdfPreview,
    showAllPagesReq,
    setShowAllPagesReq,
  }
}
