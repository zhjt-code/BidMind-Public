/**
 * 职责: 将趋势图 Tooltip 数值格式化为 [展示值, 系列名]
 * 不做: 图表渲染、数据聚合、单位换算
 */
export function formatTrendTooltipValue(
  value: unknown,
  unit?: string,
  yLabel?: string,
): [string | number, string] {
  const name = yLabel || '数值'
  if (value == null || value === '') {
    return ['—', name]
  }
  if (unit) {
    return [`${value} ${unit}`, name]
  }
  if (typeof value === 'number' || typeof value === 'string') {
    return [value, name]
  }
  return [String(value), name]
}
