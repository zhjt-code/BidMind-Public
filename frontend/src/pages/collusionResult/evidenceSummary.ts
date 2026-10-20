export interface FindingListSummaryInput {
  description?: string | null
  dimension?: string | null
}

interface FindingListSummaryOptions {
  maxLength?: number
}

const DEFAULT_SUMMARY_MAX_LENGTH = 86

function stripSummaryMarkdown(text: string | undefined | null): string {
  if (!text) return ''
  return text
    .replace(/^#{1,4}\s+/gm, '')
    .replace(/^[*-]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
}

function normalizeSummaryText(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

function removeDimensionPrefix(text: string): string {
  return text.replace(/^D[1-8]\s*[^：:]{0,20}[：:]\s*/, '')
}

function compactMetadataSummary(text: string, dimension?: string | null): string {
  const dim = dimension?.toUpperCase()
  const isMetadataFinding = dim === 'D8' || /PDF生成(工具|器)|文档元数据/.test(text)
  if (!isMetadataFinding) return text

  const parts = text
    .split(/[；;]/)
    .map(part => part.trim())
    .filter(Boolean)
    .map(part => {
      const sameToolMatch = part.match(/^([^：:；;]{2,32}?相同)[：:]\s*[^（(]+(\s*[（(].+[）)])$/)
      return sameToolMatch ? `${sameToolMatch[1]}${sameToolMatch[2]}` : part
    })

  const noteMatches = parts
    .map(part => part.match(/^(.+?)(（.+）|\(.+\))$/))
    .filter((match): match is RegExpMatchArray => Boolean(match))

  if (parts.length > 1 && noteMatches.length === parts.length) {
    const firstNote = noteMatches[0][2]
    const allSameNote = noteMatches.every(match => match[2] === firstNote)
    if (allSameNote) {
      const subjects = noteMatches.map(match => match[1].trim())
      return `${subjects.join('、')}${firstNote}`
    }
  }

  return parts.join('；')
}

function clampSummary(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text
  if (maxLength <= 3) return text.slice(0, maxLength)
  return `${text.slice(0, maxLength - 3).trimEnd()}...`
}

export function buildFindingListSummary(
  finding: FindingListSummaryInput,
  options: FindingListSummaryOptions = {},
): string {
  const maxLength = options.maxLength ?? DEFAULT_SUMMARY_MAX_LENGTH
  const plainText = normalizeSummaryText(stripSummaryMarkdown(finding.description))
  const withoutDimensionPrefix = removeDimensionPrefix(plainText)
  const compacted = compactMetadataSummary(withoutDimensionPrefix, finding.dimension)
  return clampSummary(compacted, maxLength)
}
