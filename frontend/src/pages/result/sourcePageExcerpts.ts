/**
 * 职责: 按文档+页收集预览摘录
 * 不做: 不打开 PDF、不算 bbox、不渲染页码
 */

export interface PageExcerptRef {
  documentId: number
  page: number
  excerpt?: string | null
}

export function excerptsForPage(input: {
  page: number
  documentId: number
  locations?: PageExcerptRef[]
  pageExcerpts?: PageExcerptRef[]
}): string[] {
  const rows = [
    ...(input.locations || []),
    ...(input.pageExcerpts || []),
  ]
  const found: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    if (row.documentId !== input.documentId || row.page !== input.page) continue
    const excerpt = (row.excerpt || '').trim()
    if (!excerpt || seen.has(excerpt)) continue
    seen.add(excerpt)
    found.push(excerpt)
  }
  return found
}
