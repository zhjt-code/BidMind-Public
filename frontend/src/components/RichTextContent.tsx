import React from 'react'
import { safeInlineMarkdown } from '@/lib/sanitizeHtml'

const CN_NUMBERS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
const CIRCLE_NUMBERS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']

function hasMarkdownSyntax(text: string): boolean {
  return /^#{1,4}\s|^[*-•]\s|^\d+\.\s|\*\*.+?\*\*/.test(text)
}

interface RichTextContentProps {
  text: string | undefined | null
  className?: string
  /** compact: tighter spacing for sidebar/card; default: normal reading */
  variant?: 'default' | 'compact'
  /** cn: heading levels use 一、/1./① numbering, matching writing clause cards. */
  numbering?: 'cn' | 'arabic' | 'circle'
}

const HEADING_STYLES_DEFAULT: Record<number, string> = {
  1: 'font-semibold text-sm mt-3 mb-1',
  2: 'font-medium text-sm mt-2 mb-0.5',
  3: 'font-medium text-xs mt-1.5 mb-0.5 text-gray-600',
}

const HEADING_STYLES_COMPACT: Record<number, string> = {
  1: 'font-semibold text-xs mt-1.5 mb-0.5',
  2: 'font-medium text-xs mt-1 mb-0',
  3: 'font-medium text-[11px] mt-0.5 mb-0 text-gray-600',
}

const RichTextContent: React.FC<RichTextContentProps> = ({ text, className, variant = 'default', numbering }) => {
  if (!text) return null

  if (!text.includes('\n') && !hasMarkdownSyntax(text)) {
    return (
      <span
        className={className}
        dangerouslySetInnerHTML={{ __html: safeInlineMarkdown(text) }}
      />
    )
  }

  const isCompact = variant === 'compact'
  const headingStyles = isCompact ? HEADING_STYLES_COMPACT : HEADING_STYLES_DEFAULT
  const textSize = isCompact ? 'text-xs' : 'text-sm'
  const listPl = isCompact ? 'pl-4' : 'pl-6'
  const spacing = isCompact ? 'space-y-0.5' : 'space-y-1'

  const lines = text.split('\n')

  let minHeadingLevel = 4
  for (const line of lines) {
    const m = line.trim().match(/^(#{1,4})\s/)
    if (m && m[1].length < minHeadingLevel) minHeadingLevel = m[1].length
  }

  const elements: React.ReactNode[] = []
  let ulItems: string[] = []
  let olItems: string[] = []
  const headingCounters: Record<number, number> = { 1: 0, 2: 0, 3: 0 }

  const flushUl = () => {
    if (ulItems.length > 0) {
      elements.push(
        <ul key={`ul-${elements.length}`} className={`list-disc ${listPl} ${spacing} ${textSize}`}>
          {ulItems.map((item, i) => (
            <li key={i} dangerouslySetInnerHTML={{ __html: safeInlineMarkdown(item) }} />
          ))}
        </ul>,
      )
      ulItems = []
    }
  }
  const flushOl = () => {
    if (olItems.length > 0) {
      elements.push(
        <ol key={`ol-${elements.length}`} className={`list-decimal ${listPl} ${spacing} ${textSize}`}>
          {olItems.map((item, i) => (
            <li key={i} dangerouslySetInnerHTML={{ __html: safeInlineMarkdown(item) }} />
          ))}
        </ol>,
      )
      olItems = []
    }
  }
  const flushAll = () => { flushUl(); flushOl() }

  const formatHeadingNum = (normalizedLevel: number): string => {
    const count = headingCounters[normalizedLevel]
    if (numbering === 'arabic') return `${count}. `
    if (numbering === 'circle') return `${CIRCLE_NUMBERS[count - 1] || `(${count})`} `
    if (normalizedLevel === 1) return `${CN_NUMBERS[count - 1] || count}、`
    if (normalizedLevel === 2) return `${count}. `
    return `${CIRCLE_NUMBERS[count - 1] || `(${count})`} `
  }

  for (const line of lines) {
    const trimmed = line.trim()

    if (!trimmed) {
      flushAll()
      continue
    }

    const hMatch = trimmed.match(/^(#{1,4})\s+(.+)/)
    if (hMatch) {
      flushAll()
      const rawLevel = hMatch[1].length
      const normalized = Math.min(3, Math.max(1, rawLevel - minHeadingLevel + 1))
      const cls = headingStyles[normalized]
      for (let level = normalized + 1; level <= 3; level++) headingCounters[level] = 0
      headingCounters[normalized] = (headingCounters[normalized] || 0) + 1
      const headingText = numbering
        ? `${formatHeadingNum(normalized)}${hMatch[2].replace(/^\d+\.\s*/, '')}`
        : hMatch[2]
      elements.push(
        <p key={elements.length} className={cls} dangerouslySetInnerHTML={{ __html: safeInlineMarkdown(headingText) }} />,
      )
      continue
    }

    if (/^[*-•]\s/.test(trimmed)) {
      flushOl()
      ulItems.push(trimmed.replace(/^[*-•]\s+/, ''))
      continue
    }

    const olMatch = trimmed.match(/^(\d+)\.\s+(.+)/)
    if (olMatch) {
      flushUl()
      olItems.push(olMatch[2])
      continue
    }

    flushAll()
    elements.push(
      <p key={elements.length} className={textSize} dangerouslySetInnerHTML={{ __html: safeInlineMarkdown(trimmed) }} />,
    )
  }
  flushAll()

  return <div className={`${spacing} ${className || ''}`}>{elements}</div>
}

/**
 * Strip Markdown heading/list/bold/italic syntax for plain-text contexts
 * (e.g. list-item summaries where full rendering is unnecessary).
 */
export function stripMarkdownSyntax(text: string | undefined | null): string {
  if (!text) return ''
  return text
    .replace(/^#{1,4}\s+/gm, '')
    .replace(/^[*-•]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
}

export default RichTextContent
