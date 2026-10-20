/**
 * PDF 预览面板状态 hook。
 *
 * 抽自 WritingMaterials.tsx 的 pdfOpen / pdfPage / previewMaterial 状态
 * 及 handleOpenPdfPreview / pdfUrl 推导。
 */

import { useCallback } from 'react'
import { writingService } from '@/services/writingApi'
import type { WritingMaterial } from '@/services/writingApi'
import { usePdfEvidencePreview } from '../../../result/pdfPreview/usePdfEvidencePreview'
import { materialPreviewExcerpts } from './materialPreviewExcerpts'

interface UsePdfPreviewStateOptions {
  pid: number
}

export interface PdfPreviewController {
  pdfOpen: boolean
  pdfPage: number
  previewMaterial: WritingMaterial | null
  pdfUrl: string
  excerpts: string[]
  setPdfOpen: (open: boolean) => void
  openPreview: (material: WritingMaterial) => void
}

export function usePdfPreviewState(options: UsePdfPreviewStateOptions): PdfPreviewController {
  const { pid } = options
  const { open, setOpen, page, pdfUrl, excerpts, openPreview: openEvidence } = usePdfEvidencePreview()

  const openPreview = useCallback((material: WritingMaterial) => {
    const initialPage = material.sourcePageStart ?? material.sourcePage ?? 1
    openEvidence({
      pdfUrl: writingService.getMaterialPreviewUrl(pid, material.id),
      page: initialPage,
      excerpts: materialPreviewExcerpts(material),
    })
  }, [openEvidence, pid])

  return {
    pdfOpen: open,
    pdfPage: page,
    previewMaterial: null,
    pdfUrl,
    excerpts,
    setPdfOpen: setOpen,
    openPreview,
  }
}
