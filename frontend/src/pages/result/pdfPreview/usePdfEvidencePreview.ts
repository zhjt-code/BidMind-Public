/**
 * 职责: 统一「打开 PDF 指定页并带上摘录/几何框」的预览状态
 * 不做: 不渲染 Dialog、不算 bbox、不解析页码字符串
 *
 * 本文件必须是 .ts（禁止 JSX）。扩展名从 .ts 改成 .tsx 后，无后缀 import
 * 仍会去拉旧 .ts，懒加载祖先模块整棵失败。
 */

import { useCallback, useState } from 'react'
import type { PdfHighlight } from './PdfBBoxOverlay'

export interface PdfEvidenceOpenArgs {
  pdfUrl: string
  page: number
  excerpts?: string[]
  highlights?: PdfHighlight[]
}

export interface PdfEvidencePreviewHostProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  pdfUrl: string
  page: number
  excerpts: string[]
  highlights: PdfHighlight[]
}

export function usePdfEvidencePreview(): {
  open: boolean
  setOpen: (open: boolean) => void
  page: number
  pdfUrl: string
  excerpts: string[]
  highlights: PdfHighlight[]
  openPreview: (args: PdfEvidenceOpenArgs) => void
  hostProps: PdfEvidencePreviewHostProps
} {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pdfUrl, setPdfUrl] = useState('')
  const [excerpts, setExcerpts] = useState<string[]>([])
  const [highlights, setHighlights] = useState<PdfHighlight[]>([])

  const openPreview = useCallback((args: PdfEvidenceOpenArgs) => {
    if (!args.pdfUrl || !args.page || args.page <= 0) return
    setPdfUrl(args.pdfUrl)
    setPage(args.page)
    setExcerpts((args.excerpts || []).map((item) => item.trim()).filter(Boolean))
    setHighlights(args.highlights || [])
    setOpen(true)
  }, [])

  return {
    open,
    setOpen,
    page,
    pdfUrl,
    excerpts,
    highlights,
    openPreview,
    hostProps: {
      open,
      onOpenChange: setOpen,
      pdfUrl,
      page,
      excerpts,
      highlights,
    },
  }
}
