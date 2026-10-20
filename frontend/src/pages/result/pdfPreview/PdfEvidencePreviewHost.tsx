/**
 * 职责: 把预览状态接到共享 PdfPreviewDialog
 * 不做: 不维护页码/摘录状态、不定位 bbox
 */

import { useAuthStore } from '@/stores'
import PdfPreviewDialog from '../PdfPreviewDialog'
import type { PdfEvidencePreviewHostProps } from './usePdfEvidencePreview'

export default function PdfEvidencePreviewHost(props: PdfEvidencePreviewHostProps) {
  const accessToken = useAuthStore((state) => state.accessToken)
  return (
    <PdfPreviewDialog
      open={props.open}
      onOpenChange={props.onOpenChange}
      pdfUrl={props.pdfUrl}
      initialPage={props.page}
      accessToken={accessToken}
      highlights={props.highlights}
      excerpts={props.excerpts}
    />
  )
}
