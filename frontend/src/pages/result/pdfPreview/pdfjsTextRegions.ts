/**
 * 职责: 把 pdf.js TextContent 项换成页内文本区(单字/行级)
 * 不做: 不定摘录、不画叠层、不渲染页面
 */

import type { TextRegion } from './locateHighlights'

export interface PdfjsViewport {
  width: number
  height: number
  transform: number[]
}

export interface PdfjsTextItem {
  str?: string
  transform?: number[]
  width?: number
  height?: number
}

export function isPdfjsTextItem(item: unknown): item is PdfjsTextItem {
  return typeof item === 'object' && item !== null && 'str' in item
}

export function selectPdfjsTextItems(items: readonly unknown[]): PdfjsTextItem[] {
  return items.filter(isPdfjsTextItem)
}

function multiply(a: number[], b: number[]): number[] {
  return [
    a[0] * b[0] + a[2] * b[1],
    a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3],
    a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4],
    a[1] * b[4] + a[3] * b[5] + a[5],
  ]
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

function hypot(x: number, y: number): number {
  return Math.sqrt(x * x + y * y)
}

export function textItemToBbox(
  item: PdfjsTextItem,
  viewport: PdfjsViewport,
): [number, number, number, number] | null {
  if (!item.transform || item.transform.length < 6) return null
  if (!(viewport.width > 0) || !(viewport.height > 0)) return null
  const matrix = multiply(viewport.transform, item.transform)
  const x = matrix[4]
  const y = matrix[5]
  const scaleX = hypot(matrix[0], matrix[1]) || 1
  const scaleY = hypot(matrix[2], matrix[3]) || 1
  const width = (item.width || 0) * scaleX
  const glyphHeight = (item.height || 0) > 0 ? (item.height as number) * scaleY : scaleY
  const flipped = (viewport.transform[3] ?? -1) < 0
  const topPx = flipped ? y - glyphHeight : y
  const bottomPx = flipped ? y : y + glyphHeight
  const left = clamp01(Math.min(x, x + width) / viewport.width)
  const right = clamp01(Math.max(x, x + width) / viewport.width)
  const top = clamp01(Math.min(topPx, bottomPx) / viewport.height)
  const bottom = clamp01(Math.max(topPx, bottomPx) / viewport.height)
  if (right - left <= 0 || bottom - top <= 0) return null
  return [left, top, right, bottom]
}

function union(
  a: [number, number, number, number],
  b: [number, number, number, number],
): [number, number, number, number] {
  return [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.max(a[2], b[2]),
    Math.max(a[3], b[3]),
  ]
}

export function textItemsToRegions(
  items: PdfjsTextItem[],
  viewport: PdfjsViewport,
): TextRegion[] {
  const spans: TextRegion[] = []
  for (const item of items) {
    const text = (item.str || '').trim()
    if (!text) continue
    const bbox = textItemToBbox(item, viewport)
    if (!bbox) continue
    spans.push({ text, bbox })
  }

  const lines = new Map<string, TextRegion>()
  for (const span of spans) {
    const key = span.bbox[1].toFixed(3)
    const existing = lines.get(key)
    if (!existing) {
      lines.set(key, { text: span.text, bbox: span.bbox })
      continue
    }
    existing.text += span.text
    existing.bbox = union(existing.bbox, span.bbox)
  }

  return [...spans, ...lines.values()]
}
