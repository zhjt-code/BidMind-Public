export const formatTimeRangeLabel = (hours: number): string => {
  if (hours <= 1) return '近1小时'
  if (hours <= 24) return '近24小时'
  if (hours <= 168) return `近${Math.round(hours / 24)}天`
  return '全部'
}

export const formatRelativeTime = (iso: string | null): string => {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}分钟前`
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)}小时前`
  return `${Math.floor(diff / 86400_000)}天前`
}
