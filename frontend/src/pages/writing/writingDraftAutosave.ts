export function shouldAutosaveDraftChange(
  nextContent: string,
  currentContent: string | null | undefined,
  options: { initialEditorSync?: boolean } = {},
): boolean {
  const { initialEditorSync = false } = options
  const next = (nextContent || '').trim()
  const current = (currentContent || '').trim()
  if (!next || next === current) return false
  if (initialEditorSync && normalizeVisibleText(next) === normalizeVisibleText(current)) {
    return false
  }
  return true
}

function normalizeVisibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, '')
}
