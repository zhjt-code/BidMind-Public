/**
 * 职责: 把提取警告改写成确认页可读文案，并区分「请核对 / 已自动处理」。
 * 不做: 不请求接口、不改警告是否出现，只改展示句与横幅标题。
 */

export type ExtractionWarningTone = 'action' | 'info'
export type ExtractionWarningKind = 'mandatory' | 'general'

export interface ExtractionWarningDisplay {
  raw: string
  summary: string
  details: string[]
  tone: ExtractionWarningTone
  kind: ExtractionWarningKind
}

const UNCLAIMED_OLD_RE =
  /有\s*(\d+)\s*处废标\/无效投标信号未能对应到独立审查条件（涉及第\s*([^）]+)\s*页）/
const UNCLAIMED_NEW_RE =
  /第\s*(.+?)\s*页有\s*(\d+)\s*处写明「废标」或「投标无效」/
const AUTO_OLD_RE =
  /已自动核验高风险条款：补标否决\s*(\d+)\s*条，补提独立条件\s*(\d+)\s*条/
const AUTO_NEW_RE = /系统已自动处理：/
const ANCHOR_OLD_RE = /高风险章节内可逐条枚举的条件比已提取条目多\s*(\d+)\s*条/
const ANCHOR_NEW_RE = /带序号或★的条件比当前招标要求多\s*(\d+)\s*条/

function formatUnclaimedSummary(count: string, pageText: string): string {
  return (
    `第 ${pageText} 页有 ${count} 处写明「废标」或「投标无效」的内容，` +
    '还没有提取成单独的一条招标要求。请对照原文检查：是漏了，还是几条被合成了一条。'
  )
}

function formatAnchorSummary(count: string): string {
  return (
    `否决或资格相关章节里，带序号或★的条件比当前招标要求多 ${count} 条。` +
    '请核对这些条款是否都已提取出来。'
  )
}

function formatAutoSummary(promoted: number, inserted: number): string {
  const parts: string[] = []
  if (promoted > 0) {
    parts.push(`${promoted} 条已有要求已标为否决项`)
  }
  if (inserted > 0) {
    parts.push(`${promoted > 0 ? '另新增' : '新增'} ${inserted} 条独立否决条件`)
  }
  if (parts.length === 0) {
    return '系统已自动处理部分否决项。'
  }
  return `系统已自动处理：${parts.join('，')}。`
}

function withDisplay(
  warning: string,
  details: string[],
  summary: string,
  tone: ExtractionWarningTone,
  kind: ExtractionWarningKind,
): ExtractionWarningDisplay {
  return { raw: warning, summary, details, tone, kind }
}

export function formatExtractionWarning(warning: string): ExtractionWarningDisplay {
  const lines = warning
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const firstLine = lines[0] || warning
  const details = lines
    .slice(1)
    .map((line) => line.replace(/^[-*]\s*/, '').trim())
    .filter(Boolean)

  const discardedMatch = firstLine.match(/(?:另有\s*)?(\d+)\s*条要求因字段校验失败被丢弃/)
  if (discardedMatch) {
    return withDisplay(
      warning,
      details,
      `有 ${discardedMatch[1]} 条疑似要求未能自动整理成列表。系统已保留其他有效要求，请结合原文重点核对。`,
      'action',
      'general',
    )
  }

  const chunkFailureMatch = firstLine.match(
    /有\s*(\d+)\s*个文档分块提取失败(?:（涉及第\s*([^）]+)\s*页）)?/,
  )
  if (chunkFailureMatch) {
    const pageText = chunkFailureMatch[2] ? `，涉及第 ${chunkFailureMatch[2]} 页` : ''
    return withDisplay(
      warning,
      details,
      `有 ${chunkFailureMatch[1]} 处文档内容未能自动提取${pageText}。请对照招标文件原文核对是否漏提要求。`,
      'action',
      'general',
    )
  }

  const unclaimedOld = firstLine.match(UNCLAIMED_OLD_RE)
  if (unclaimedOld) {
    return withDisplay(
      warning,
      details,
      formatUnclaimedSummary(unclaimedOld[1], unclaimedOld[2].trim()),
      'action',
      'mandatory',
    )
  }
  if (UNCLAIMED_NEW_RE.test(firstLine)) {
    return withDisplay(warning, details, firstLine, 'action', 'mandatory')
  }

  const autoOld = firstLine.match(AUTO_OLD_RE)
  if (autoOld) {
    return withDisplay(
      warning,
      details,
      formatAutoSummary(Number(autoOld[1]), Number(autoOld[2])),
      'info',
      'mandatory',
    )
  }
  if (AUTO_NEW_RE.test(firstLine)) {
    return withDisplay(warning, details, firstLine, 'info', 'mandatory')
  }

  const anchorOld = firstLine.match(ANCHOR_OLD_RE)
  if (anchorOld) {
    return withDisplay(warning, details, formatAnchorSummary(anchorOld[1]), 'action', 'mandatory')
  }
  if (ANCHOR_NEW_RE.test(firstLine)) {
    return withDisplay(warning, details, firstLine, 'action', 'mandatory')
  }

  return withDisplay(warning, details, firstLine.replace(/[:：]$/, ''), 'action', 'general')
}

export function extractionWarningBannerTitle(
  displays: ExtractionWarningDisplay[],
): string {
  const hasMandatoryAction = displays.some(
    (item) => item.kind === 'mandatory' && item.tone === 'action',
  )
  const hasGeneral = displays.some((item) => item.kind === 'general')
  const hasMandatoryInfo = displays.some(
    (item) => item.kind === 'mandatory' && item.tone === 'info',
  )
  if (hasMandatoryAction) {
    return '请先核对否决项是否齐全'
  }
  if (hasGeneral) {
    return '部分内容需要人工核对'
  }
  if (hasMandatoryInfo) {
    return '部分否决项已自动处理'
  }
  return '部分内容需要人工核对'
}
