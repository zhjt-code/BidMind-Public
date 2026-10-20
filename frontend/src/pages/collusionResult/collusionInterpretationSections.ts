export type CollusionInterpretationTone = 'neutral' | 'warning' | 'fact' | 'ai'

export interface CollusionInterpretationSection {
  title: string
  tone: CollusionInterpretationTone
  badge?: string
  items: string[]
}

interface DraftCollusionInterpretationSection {
  title: string
  tone: CollusionInterpretationTone
  badge?: string
  items: Array<string | undefined | null>
}

interface BuildCollusionInterpretationSectionsOptions {
  hasTenderDoc: boolean
  overviewNotices?: string[]
  riskConsistencyNote?: string
  decisionEngineVersion: number
  narrativeSummary?: string
  factualExplanation?: string
  factualExplanationItems?: string[]
  synthesisSummary?: string
  investigationSuggestions?: string[]
}

interface GetCollusionDecisionSummaryTextOptions {
  projectDecision?: string
  reviewRequired: boolean
  fallbackHint?: string
}

const cleanText = (value: string | undefined | null): string =>
  typeof value === 'string' ? value.trim() : ''

const MISSING_TENDER_DOC_LIMITATION =
  '未上传招标文件参考，部分文本一致性可能来源于招标模板，请结合原文人工甄别。'

const isMissingTenderDocNotice = (value: string): boolean =>
  value.includes('未上传招标文件') && value.includes('模板')

const limitationIdentity = (value: string): string =>
  isMissingTenderDocNotice(value) ? 'missing-tender-doc' : value

const uniqueLimitations = (items: Array<string | undefined | null>): string[] => {
  const result: string[] = []
  const seen = new Set<string>()
  for (const item of items) {
    const text = cleanText(item)
    if (!text) continue
    const key = limitationIdentity(text)
    if (seen.has(key)) continue
    seen.add(key)
    result.push(key === 'missing-tender-doc' ? MISSING_TENDER_DOC_LIMITATION : text)
  }
  return result
}

const splitLegacyFactualExplanation = (value: string | undefined): string[] =>
  cleanText(value)
    .split(/\r?\n/)
    .map(line => line.trim().replace(/^·\s*/, ''))
    .filter(line => line && !line.startsWith('以下文件对存在数据质量信号'))
    .filter(line => !isMissingTenderDocNotice(line))

const addSection = (
  sections: CollusionInterpretationSection[],
  section: DraftCollusionInterpretationSection,
) => {
  const items = section.items.map(cleanText).filter(Boolean)
  if (items.length === 0) return
  sections.push({ ...section, items })
}

export function buildCollusionInterpretationSections({
  hasTenderDoc,
  overviewNotices = [],
  riskConsistencyNote,
  decisionEngineVersion,
  narrativeSummary,
  factualExplanation,
  factualExplanationItems = [],
  synthesisSummary,
  investigationSuggestions = [],
}: BuildCollusionInterpretationSectionsOptions): CollusionInterpretationSection[] {
  const sections: CollusionInterpretationSection[] = []
  const limitations = uniqueLimitations([
    !hasTenderDoc
      ? MISSING_TENDER_DOC_LIMITATION
      : '',
    ...overviewNotices,
    riskConsistencyNote,
    decisionEngineVersion < 2
      ? '当前结果基于旧版判定口径生成，建议重新执行串标分析后再下载报告。'
      : '',
  ])
  const factualItems = factualExplanationItems.map(cleanText).filter(Boolean)
  const normalizedFactualItems = factualItems.length > 0
    ? factualItems
    : splitLegacyFactualExplanation(factualExplanation)

  // 呈现顺序: AI 综合研判 → 判断依据 → 事实依据 → 调查建议 → 使用限制
  // - AI 综合研判置顶: 用户首屏先看结论性研判, 符合"概览"信息密度优先级
  // - 使用限制沉底: 限制说明是"已知不确定因素"补注, 不应抢在研判之前
  addSection(sections, {
    title: 'AI 综合研判',
    tone: 'ai',
    items: [synthesisSummary],
  })
  addSection(sections, {
    title: '判断依据',
    tone: 'neutral',
    items: [narrativeSummary],
  })
  addSection(sections, {
    title: '事实依据',
    tone: 'fact',
    items: normalizedFactualItems,
  })
  addSection(sections, {
    title: '调查建议',
    tone: 'neutral',
    items: investigationSuggestions,
  })
  addSection(sections, {
    title: '使用限制',
    tone: 'warning',
    items: limitations,
  })

  return sections
}

export function getCollusionDecisionSummaryText({
  projectDecision,
  reviewRequired,
  fallbackHint,
}: GetCollusionDecisionSummaryTextOptions): string {
  if (reviewRequired) {
    return '当前仍有文件对需要人工复核，请结合下方证据明细判断。'
  }
  if (projectDecision === 'suspicious_found') {
    return '系统已发现可支撑进一步核查的异常线索，请结合下方证据明细确认。'
  }
  if (projectDecision === 'clear_no_risk') {
    return '系统未发现可支撑串标认定的异常证据。'
  }
  return cleanText(fallbackHint) || '自动化分析未形成明确项目结论，请结合明细证据人工复核。'
}
