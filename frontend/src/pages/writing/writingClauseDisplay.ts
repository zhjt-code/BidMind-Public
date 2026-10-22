/**
 * 职责: 将写标约束原始内容转换为用户可读的只读展示文案
 * 不做: 不修改约束数据、不承担编辑表单取值、不渲染 UI
 */

import { sanitizeUserFacingText } from '../../services/progressStatusTransforms.ts'

const NODE_TYPE_LABELS: Record<string, string> = {
  fixed_template: '固定模板',
  data_fill: '资料填充',
  free_writing: '自由论述',
}

const VOLUME_TYPE_LABELS: Record<string, string> = {
  technical: '技术标',
  commercial: '商务标',
  qualification: '资格标',
  price: '报价标',
}

const STRUCTURE_MODE_LABELS: Record<string, string> = {
  tech_comm: '技术标 + 商务标',
  tech_comm_qualification: '技术标 + 商务标 + 资格标',
  single: '单卷结构',
}

const INTERNAL_TOKEN_LABELS: Record<string, string> = {
  fixed_template_placeholder: '模板占位未替换',
  fixed_template: '固定模板',
  data_fill: '资料填充',
  free_writing: '自由论述',
  tech_comm: '技术标 + 商务标',
}

const INTERNAL_PARAM_PATTERN =
  /\b(?:node_type(?:_source)?|structure_mode|has_explicit_directory|has_technical|has_commercial|has_qualification|volume_type|volume_title|technical_sections|commercial_sections|qualification_sections|fixed_template_sections)\s*[:=]\s*[\w.-]+\b/gi

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function parseJsonRecord(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return null
  try {
    const parsed = JSON.parse(trimmed) as unknown
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(stringValue).filter(Boolean)
    : []
}

function nodeTitle(item: Record<string, unknown>): string {
  const title = stringValue(item.title) || stringValue(item.name)
  if (!title) return ''
  const typeLabel = NODE_TYPE_LABELS[stringValue(item.node_type)]
  return typeLabel ? `${title}（${typeLabel}）` : title
}

function volumeSummary(volume: Record<string, unknown>): string {
  const title =
    stringValue(volume.volume_title)
    || VOLUME_TYPE_LABELS[stringValue(volume.volume_type)]
    || '分卷'
  const items = Array.isArray(volume.items)
    ? volume.items.filter(isRecord).map(nodeTitle).filter(Boolean)
    : []
  if (items.length === 0) return `${title}：未提取到明确章节`
  const visible = items.slice(0, 6).join('、')
  return items.length > 6 ? `${title}：${visible}等 ${items.length} 项` : `${title}：${visible}`
}

function pushSection(parts: string[], label: string, value: unknown): void {
  const items = stringArray(value)
  if (items.length > 0) parts.push(`${label}：${items.slice(0, 6).join('、')}`)
}

function inferStructureMode(record: Record<string, unknown>): string {
  const enabled = [
    record.has_technical === true ? '技术标' : '',
    record.has_commercial === true ? '商务标' : '',
    record.has_qualification === true ? '资格标' : '',
  ].filter(Boolean)
  return enabled.join(' + ')
}

function jsonClauseSummary(record: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof record.has_explicit_directory === 'boolean') {
    parts.push(record.has_explicit_directory
      ? '目录结构：招标文件已明确目录'
      : '目录结构：招标文件未明确完整目录')
  }

  const mode = stringValue(record.structure_mode)
  const modeLabel = STRUCTURE_MODE_LABELS[mode] || inferStructureMode(record)
  if (modeLabel) parts.push(`结构模式：${modeLabel}`)

  if (Array.isArray(record.volumes)) {
    parts.push(...record.volumes.filter(isRecord).map(volumeSummary))
  }

  pushSection(parts, '技术标', record.technical_sections)
  pushSection(parts, '商务标', record.commercial_sections)
  pushSection(parts, '资格标', record.qualification_sections)
  pushSection(parts, '固定模板', record.fixed_template_sections)

  const title = stringValue(record.title)
  if (parts.length === 0 && title) parts.push(title)

  return parts.length > 0
    ? parts.join('；')
    : '结构化约束：请返回约束确认页查看并编辑。'
}

function sanitizeInternalTokens(rawText: string): string {
  let text = sanitizeUserFacingText(rawText)
  text = text.replace(INTERNAL_PARAM_PATTERN, ' ')
  for (const token of Object.keys(INTERNAL_TOKEN_LABELS).sort((a, b) => b.length - a.length)) {
    text = text.replace(new RegExp(`\\b${token}\\b`, 'gi'), INTERNAL_TOKEN_LABELS[token])
  }
  // 仅压缩行内空白与同行残留标点；禁止用 \s 吞换行，否则多行 Markdown 标题会粘段尾
  return text
    .replace(/[ \t]*[;,]+[ \t]*/g, ' ')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .trim()
}

const CONTRACT_HEADING_RE = /^#{1,4}\s+[1-6][.\s、]/
const HANDWRITTEN_CN_HEADING_RE = /^#{1,4}\s*[一二三四五六七八九十]+、/
const DOTTED_HEADING_RE = /^#{1,4}\s+\d+\.\d+/

export function hasRichClauseMarkup(text: string): boolean {
  return /[#*•]/.test(text)
}

export function shouldApplyClauseHeadingNumbers(text: string): boolean {
  // 六段契约标题才套中文序号；手写「一、」或 3.1 标题不再二次编号。
  const headings = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => /^#{1,4}\s+\S/.test(line))
  if (headings.length < 2) return false
  const contractLike = headings.filter((line) => CONTRACT_HEADING_RE.test(line)).length
  if (contractLike < 2) return false
  if (headings.some((line) => HANDWRITTEN_CN_HEADING_RE.test(line))) return false
  if (headings.some((line) => DOTTED_HEADING_RE.test(line))) return false
  return true
}

export function toUserFacingClauseText(rawText: string | null | undefined): string {
  const text = String(rawText || '').trim()
  if (!text) return ''
  const jsonRecord = parseJsonRecord(text)
  if (jsonRecord) return jsonClauseSummary(jsonRecord)
  return sanitizeInternalTokens(text)
}

export function shortUserFacingClauseText(rawText: string | null | undefined, maxLength = 72): string {
  const compact = toUserFacingClauseText(rawText).replace(/\s+/g, ' ').trim()
  return compact.length > maxLength ? `${compact.slice(0, maxLength)}...` : compact
}
