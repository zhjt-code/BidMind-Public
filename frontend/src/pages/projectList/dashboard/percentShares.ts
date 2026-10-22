/** 占比纵轴固定刻度。闭合百分比不能交给自动刻度。 */
export const PERCENT_AXIS_TICKS = [0, 25, 50, 75, 100] as const

/**
 * 把一组非负数量收成总和恰好为 100 的一位小数百分比。
 * 最大余数法，避免四舍五入后堆叠越过 100，把纵轴顶出刻度。
 */
export function toPercentShares(parts: readonly number[]): number[] {
  if (parts.length === 0) return []
  const safe = parts.map((value) => (Number.isFinite(value) && value > 0 ? value : 0))
  const total = safe.reduce((sum, value) => sum + value, 0)
  if (total <= 0) return safe.map(() => 0)

  const exact = safe.map((value) => (value / total) * 100)
  const tenths = exact.map((value) => Math.floor(value * 10))
  let leftover = 1000 - tenths.reduce((sum, value) => sum + value, 0)
  const order = exact
    .map((value, index) => ({ index, remainder: value * 10 - tenths[index] }))
    .sort((left, right) => right.remainder - left.remainder || left.index - right.index)
  const adjusted = [...tenths]
  for (let cursor = 0; leftover > 0; cursor += 1) {
    adjusted[order[cursor % order.length].index] += 1
    leftover -= 1
  }
  return adjusted.map((value) => value / 10)
}
