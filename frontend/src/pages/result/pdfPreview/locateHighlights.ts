/**
 * 职责: 按页内文本区定位摘录对应的归一化 bbox
 * 不做: 不渲染 PDF、不拉取文件、不把失败回退成整页框
 *
 * 规则与质检 evidence_geom.locate_bbox 同形态:
 * 折叠空白标点后, 优先「摘录被某区完整包含」的最小框;
 * 否则合并足够长的摘录真子集。禁止短 span 抢最小面积。
 */

import type { PdfHighlight } from './PdfBBoxOverlay'

export type BBox = [number, number, number, number]

export interface TextRegion {
  text: string
  bbox: BBox
}

const TRAILING_PAGE_RE = /[\s.·…]+[\d]{1,4}\s*$/

export function foldExcerpt(text: string): string {
  return Array.from(text || '')
    .filter((ch) => /\p{L}|\p{N}/u.test(ch) || (ch >= '\u4e00' && ch <= '\u9fff'))
    .join('')
}

function needles(excerpt: string): string[] {
  const ordered: string[] = []
  const seen = new Set<string>()
  for (const raw of [excerpt || '', (excerpt || '').replace(TRAILING_PAGE_RE, '')]) {
    const folded = foldExcerpt(raw)
    if (folded && !seen.has(folded)) {
      seen.add(folded)
      ordered.push(folded)
    }
  }
  return ordered
}

function area(bbox: BBox): number {
  return Math.abs(bbox[2] - bbox[0]) * Math.abs(bbox[3] - bbox[1])
}

function union(boxes: BBox[]): BBox {
  const left = Math.min(...boxes.map((box) => Math.min(box[0], box[2])))
  const top = Math.min(...boxes.map((box) => Math.min(box[1], box[3])))
  const right = Math.max(...boxes.map((box) => Math.max(box[0], box[2])))
  const bottom = Math.max(...boxes.map((box) => Math.max(box[1], box[3])))
  return [left, top, right, bottom]
}

function foldedRegions(regions: TextRegion[]): Array<{ text: string; bbox: BBox }> {
  return regions
    .map((region) => ({ text: foldExcerpt(region.text), bbox: region.bbox }))
    .filter((region) => region.text.length > 0)
}

export function locateHighlightBbox(
  excerpt: string,
  regions: TextRegion[],
): BBox | null {
  const prepared = foldedRegions(regions)
  if (!prepared.length) return null
  for (const needle of needles(excerpt)) {
    const full = prepared.filter((region) => region.text.includes(needle))
    if (full.length) {
      full.sort((a, b) => area(a.bbox) - area(b.bbox))
      return full[0].bbox
    }
    const minLen = Math.max(8, Math.floor((needle.length * 3) / 10))
    const parts = prepared.filter(
      (region) => needle.includes(region.text) && region.text.length >= minLen,
    )
    if (parts.length) {
      return union(parts.map((part) => part.bbox))
    }
  }
  return null
}

export function locateHighlights(
  page: number,
  excerpts: string[],
  regions: TextRegion[],
): PdfHighlight[] {
  const highlights: PdfHighlight[] = []
  for (const excerpt of excerpts) {
    const bbox = locateHighlightBbox(excerpt, regions)
    if (bbox) highlights.push({ page, bbox })
  }
  return highlights
}
