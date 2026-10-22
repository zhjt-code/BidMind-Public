/**
 * 职责: 项目列表表头与行共用的列轨、紧凑时间文案、更新时间是否占格
 * 不做: 不渲染组件、不决定路由、不判断删除权限
 */

export interface ProjectListColumnLabel {
  key: string
  label: string
  align: 'start' | 'center' | 'end'
}

export function projectListColumnAlignClass(align: ProjectListColumnLabel['align']): string {
  if (align === 'center') return 'text-center'
  if (align === 'end') return 'text-right'
  return ''
}

export function projectListGridTracks(isAdmin: boolean, selectionMode: boolean): string[] {
  // 普通用户名称 2.5fr、后列各 1fr，下限仍是原固定宽。max-w-7xl 下名称大约少 1/3。
  // ponytail: 不要改成小于下限的 fr（如 0.2fr），浏览器会把该列钉死，名称列仍占满剩余空间。
  const tail = (min: string) => (isAdmin ? min : `minmax(${min}, 1fr)`)
  return [
    selectionMode ? '1.25rem' : '',
    isAdmin ? 'minmax(0, 1fr)' : 'minmax(0, 2.5fr)',
    isAdmin ? '8.5rem' : '',
    tail('6.5rem'),
    tail('6.5rem'),
    tail('6rem'),
    tail('6.75rem'),
    tail('12rem'),
  ].filter((track) => track !== '')
}

export function projectListGridTemplate(isAdmin: boolean, selectionMode: boolean): string {
  return projectListGridTracks(isAdmin, selectionMode).join(' ')
}

export function projectListColumnLabels(isAdmin: boolean, selectionMode: boolean): ProjectListColumnLabel[] {
  const labels: ProjectListColumnLabel[] = []
  if (selectionMode) labels.push({ key: 'select', label: '', align: 'start' })
  labels.push({ key: 'name', label: '项目名称', align: 'start' })
  if (isAdmin) labels.push({ key: 'owner', label: '创建者', align: 'start' })
  labels.push(
    { key: 'created', label: '创建时间', align: 'start' },
    { key: 'updated', label: '更新时间', align: 'start' },
    { key: 'progress', label: '进度', align: 'center' },
    { key: 'status', label: '状态', align: 'center' },
    { key: 'action', label: '操作', align: 'center' },
  )
  return labels
}

export function hasDistinctListUpdate(
  createdAt: string | null | undefined,
  updatedAt: string | null | undefined,
): boolean {
  return Boolean(updatedAt) && updatedAt !== createdAt
}

const MINUTE_MS = 60 * 1000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS

/**
 * ponytail: 月按 30 天、年按 365 天折算，保证列内是短文案；绝对时间放在悬停层。
 * 升级路径: 若要自然月，改为日历差，并同步加宽时间列。
 */
export function formatListAge(dateString: string | null | undefined, now = Date.now()): string {
  if (!dateString) return '未知时间'
  const date = new Date(dateString)
  if (Number.isNaN(date.getTime())) return '未知时间'

  const elapsed = now - date.getTime()
  if (elapsed < MINUTE_MS) return '刚刚'
  const minutes = Math.floor(elapsed / MINUTE_MS)
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(elapsed / HOUR_MS)
  if (hours < 24) return `${hours}小时前`
  const days = Math.floor(elapsed / DAY_MS)
  if (days < 30) return `${days}天前`
  if (days < 365) return `${Math.floor(days / 30)}个月前`
  return `${Math.floor(days / 365)}年前`
}
