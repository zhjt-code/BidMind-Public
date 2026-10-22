export interface BuildCollusionOverviewNoticesOptions {
  dataQualityFlags?: string[]
  profileQualityFlags?: string[]
  industryProfileConfidence?: string
}

const HIGH_CONFIDENCE_SET = new Set(['high', 'medium'])

export function buildCollusionOverviewNotices({
  dataQualityFlags = [],
  profileQualityFlags = [],
  industryProfileConfidence,
}: BuildCollusionOverviewNoticesOptions): string[] {
  const notices = [...dataQualityFlags]
  if (profileQualityFlags.length > 0) {
    const confidence = String(industryProfileConfidence || 'unknown').toLowerCase()
    notices.push(
      HIGH_CONFIDENCE_SET.has(confidence)
        ? '本次分析已结合项目材料中的通用内容边界校正相似性判断，请结合明细证据综合查看。'
        : '本次分析未额外依赖稳定行业先验，系统已按保守口径判断文本相似与风险，请结合明细证据综合判断。'
    )
  }
  return Array.from(new Set(notices))
}
