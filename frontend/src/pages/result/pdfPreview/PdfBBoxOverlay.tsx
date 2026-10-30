import React from 'react'
import { bboxToCssRect, padNormalizedBbox } from './pdfPreviewMath'

/**
 * 职责: 在 pdf.js 画布上按归一化 bbox 绘制高亮(外扩+外侧描边, 避免压字)
 * 不做: 不渲染 PDF、不做坐标换算以外的逻辑
 */

export interface PdfHighlight {
  page: number
  bbox: [number, number, number, number]
}

interface PdfBBoxOverlayProps {
  /** 当前页需要高亮的矩形(调用方已按页过滤) */
  highlights: PdfHighlight[]
}

const PdfBBoxOverlay: React.FC<PdfBBoxOverlayProps> = ({ highlights }) => {
  if (!highlights.length) return null
  return (
    <div className="absolute inset-0 pointer-events-none" data-testid="pdf-bbox-overlay">
      {highlights.map((highlight, index) => {
        const rect = bboxToCssRect(padNormalizedBbox(highlight.bbox))
        return (
          <div
            key={`${highlight.page}-${index}`}
            className="absolute rounded-sm bg-amber-400/15 outline-solid outline-2 outline-amber-500/80 outline-offset-2"
            style={rect}
          />
        )
      })}
    </div>
  )
}

export default PdfBBoxOverlay
