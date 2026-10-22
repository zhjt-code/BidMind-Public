export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  try {
    const date = new Date(dateStr)
    if (isNaN(date.getTime())) return '-'
    return date.toLocaleString('zh-CN')
  } catch {
    return dateStr ?? '-'
  }
}

export function getPollingInterval(stage: string, includeVectorizing = false): number {
  switch (stage) {
    case 'vl_recognition': return 2000
    case 'parsing':        return 5000
    case 'vectorizing':    return includeVectorizing ? 8000 : 3000
    default:               return 3000
  }
}
