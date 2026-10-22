/**
 * 周建议看板：每个 (scope, target) 只保留当前最新一条。
 * 不做：不请求接口、不写回 priority。
 */

import { purposePoolSortIndex } from './reliabilityLabels.ts'

export interface AdviceBoardRow {
  id: number
  scope: string
  target: string
  week_start: string | null
}

function weekStartMs(iso: string | null): number {
  if (!iso) return Number.NEGATIVE_INFINITY
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms
}

function isNewerAdvice(next: AdviceBoardRow, prev: AdviceBoardRow): boolean {
  const delta = weekStartMs(next.week_start) - weekStartMs(prev.week_start)
  if (delta !== 0) return delta > 0
  return next.id > prev.id
}

export function pickLatestAdviceByTarget<T extends AdviceBoardRow>(items: T[]): T[] {
  const latest = new Map<string, T>()
  for (const item of items) {
    const key = `${item.scope}\0${item.target}`
    const prev = latest.get(key)
    if (!prev || isNewerAdvice(item, prev)) {
      latest.set(key, item)
    }
  }
  return [...latest.values()]
}

export function boardPurposeItems<T extends AdviceBoardRow>(items: T[]): T[] {
  return pickLatestAdviceByTarget(items)
    .filter((item) => item.scope === 'purpose')
    .sort((a, b) => purposePoolSortIndex(a.target) - purposePoolSortIndex(b.target))
}

export function boardNodeItems<T extends AdviceBoardRow>(items: T[], limit = 5): T[] {
  return pickLatestAdviceByTarget(items)
    .filter((item) => item.scope === 'node')
    .sort((a, b) => {
      const delta = weekStartMs(b.week_start) - weekStartMs(a.week_start)
      if (delta !== 0) return delta
      return b.id - a.id
    })
    .slice(0, limit)
}

function formatCstDate(iso: string): string | null {
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return null
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(ms))
}

export function adviceBoardWeekLabel(items: Array<{ week_start: string | null }>): string | null {
  let latestIso: string | null = null
  let latestMs = Number.NEGATIVE_INFINITY
  for (const item of items) {
    const ms = weekStartMs(item.week_start)
    if (ms > latestMs && item.week_start) {
      latestMs = ms
      latestIso = item.week_start
    }
  }
  if (!latestIso) return null
  const start = formatCstDate(latestIso)
  if (!start) return null
  const endMs = Date.parse(latestIso) + 6 * 24 * 60 * 60 * 1000
  const end = formatCstDate(new Date(endMs).toISOString())
  if (!end) return null
  return `${start} ~ ${end}`
}
