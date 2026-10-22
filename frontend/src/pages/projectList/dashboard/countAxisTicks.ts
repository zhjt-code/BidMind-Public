/**
 * 数量纵轴刻度：等步长，顶端是不小于数据最大值的步长整数倍。
 * 不做: 不处理百分比轴，也不读取图表模式。
 */

const TARGET_INTERVALS = 4

export function countAxisTicks(maxValue: number): number[] {
  const max = Number.isFinite(maxValue) ? Math.ceil(maxValue) : 0
  if (max <= 0) return [0, 1]
  const rough = max / TARGET_INTERVALS
  if (rough <= 1) {
    return Array.from({ length: max + 1 }, (_, index) => index)
  }
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const residual = rough / magnitude
  const nice = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10
  const step = Math.max(1, Math.round(nice * magnitude))
  const ceiling = Math.ceil(max / step) * step
  const ticks: number[] = []
  for (let value = 0; value <= ceiling; value += step) {
    ticks.push(value)
  }
  return ticks
}

/** 按最长刻度留出标签宽度，避免三位及以上数字被裁掉首位。 */
export function countAxisWidth(ticks: readonly number[]): number {
  const longest = ticks.reduce((width, value) => Math.max(width, String(value).length), 1)
  return longest * 8 + 20
}
