/**
 * 职责: 判断素材正文是否适合作为 PDF 预览定位摘录。
 * 不做: 打开预览、画高亮、解析 PDF。
 */

export const MAX_MATERIAL_HIGHLIGHT_SPAN = 3
export const MAX_MATERIAL_HIGHLIGHT_CHARS = 2000

export interface MaterialPreviewExcerptSource {
  sourcePage?: number | null
  sourcePageStart?: number | null
  sourcePageEnd?: number | null
  content?: string | null
}

export function materialPreviewExcerpts(material: MaterialPreviewExcerptSource): string[] {
  const start = material.sourcePageStart ?? material.sourcePage
  const end = material.sourcePageEnd ?? start
  if (start != null && end != null && end - start + 1 > MAX_MATERIAL_HIGHLIGHT_SPAN) {
    return []
  }
  const content = (material.content || '').trim()
  if (!content || content.length > MAX_MATERIAL_HIGHLIGHT_CHARS) return []
  return [content.slice(0, 280)]
}
