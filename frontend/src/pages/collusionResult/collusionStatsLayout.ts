export const COLLUSION_STATS_SCROLL_BODY_CLASS =
  'p-4 space-y-4 max-h-[45vh] overflow-y-auto overscroll-contain'

export const SINGLE_GROUP_RISK_GAUGE_LEVELS = [
  { key: 'none', label: '无风险', color: 'hsl(142, 76%, 36%)' },
  { key: 'low', label: '低风险', color: 'hsl(45, 93%, 47%)' },
  { key: 'medium', label: '中风险', color: 'hsl(25, 95%, 53%)' },
  { key: 'high', label: '高风险', color: 'hsl(0, 84%, 60%)' },
  { key: 'inconclusive', label: '待复核', color: 'hsl(215, 16%, 47%)' },
] as const

/**
 * 是否使用五段均分风险圆环（判定盘），而非计数饼图。
 *
 * - 单群组：始终用圆环表达该群组判定（视觉）
 * - 分布仅 0/1 种风险等级：计数饼会退化成单色实心环（“老样子”），改用圆环
 * - 两种及以上风险并存：仍用计数饼表达分布
 */
export const shouldUseRiskLevelGauge = (params: {
  groupCount: number
  distinctRiskCount: number
}): boolean => params.groupCount === 1 || params.distinctRiskCount <= 1

/** @deprecated 请用 shouldUseRiskLevelGauge；保留兼容旧测试命名 */
export const shouldUseSingleGroupRiskGauge = (
  riskDistributionBasis: string | undefined,
  groupCount: number,
): boolean => riskDistributionBasis === 'groups' && groupCount === 1

export const buildRiskChartTitle = (params: {
  useGauge: boolean
  groupCount: number
  riskDistributionBasis: string | undefined
}): string => {
  if (params.useGauge) {
    return params.groupCount === 1 ? '群组风险判定' : '项目风险判定'
  }
  return params.riskDistributionBasis === 'groups' ? '群组风险分布' : '文件对风险分布'
}

export const resolveRiskGaugeLevel = (params: {
  groupCount: number
  groupRiskLevel: string | undefined
  singleDistributionRiskKey: string | undefined
  projectRiskLevel: string | undefined
}): string => {
  if (params.groupCount === 1 && params.groupRiskLevel) {
    return params.groupRiskLevel
  }
  if (params.singleDistributionRiskKey) {
    return params.singleDistributionRiskKey
  }
  return params.projectRiskLevel || 'inconclusive'
}

export const buildSingleGroupRiskGaugeData = (riskLevel: string | undefined) => {
  const normalized = riskLevel?.toLowerCase()
  const activeKey = SINGLE_GROUP_RISK_GAUGE_LEVELS.some(level => level.key === normalized)
    ? normalized
    : 'inconclusive'

  return SINGLE_GROUP_RISK_GAUGE_LEVELS.map(level => ({
    ...level,
    name: level.label,
    value: 1,
    active: level.key === activeKey,
  }))
}
