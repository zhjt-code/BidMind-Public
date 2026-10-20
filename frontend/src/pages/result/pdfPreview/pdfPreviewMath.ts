/**
 * 职责: 预览页码/缩放夹取、解析错误文案、字节拷贝
 * 不做: 不拉 PDF、不碰 worker、不渲染
 */

export const MIN_ZOOM = 0.5
export const MAX_ZOOM = 2.5
export const ZOOM_STEP = 0.25
export const WHEEL_PAGE_TURN_COOLDOWN_MS = 280
export const SCROLL_EDGE_EPSILON_PX = 1

export function clampPage(page: number, numPages: number): number {
  if (numPages <= 0) return Math.max(1, page)
  return Math.min(Math.max(page, 1), numPages)
}

export function clampZoom(zoom: number): number {
  const snapped = Math.round(zoom / ZOOM_STEP) * ZOOM_STEP
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(snapped.toFixed(2))))
}

export function formatPdfParseError(error: unknown): string {
  const message = error instanceof Error && error.message.trim()
    ? error.message
    : '未知错误'
  return `PDF 解析失败: ${message}`
}

export function copyPdfBytes(buffer: ArrayBuffer): Uint8Array {
  const copy = new Uint8Array(buffer.byteLength)
  copy.set(new Uint8Array(buffer))
  return copy
}

export function clonePdfBytes(data: Uint8Array): Uint8Array {
  const copy = new Uint8Array(data.byteLength)
  copy.set(data)
  return copy
}

export function pageNumbers(numPages: number): number[] {
  if (numPages <= 0) return []
  return Array.from({ length: numPages }, (_, index) => index + 1)
}

export type WheelPageTurnResult =
  | { kind: 'scroll' }
  | { kind: 'ignore' }
  | { kind: 'page'; page: number; align: 'start' | 'end' }

export interface WheelPageTurnInput {
  deltaY: number
  pageNumber: number
  numPages: number
  scrollTop: number
  scrollHeight: number
  clientHeight: number
  nowMs: number
  lastTurnAtMs: number
  cooldownMs?: number
  edgeEpsilon?: number
}

/** 页内还能滚则交给原生滚动；碰到边缘才翻页。冷却只挡住连跳，不挡住新页内滚动。 */
export function resolveWheelPageTurn(input: WheelPageTurnInput): WheelPageTurnResult {
  if (input.deltaY === 0 || input.numPages <= 0) return { kind: 'scroll' }

  const epsilon = input.edgeEpsilon ?? SCROLL_EDGE_EPSILON_PX
  const atTop = input.scrollTop <= epsilon
  const atBottom = input.scrollTop + input.clientHeight >= input.scrollHeight - epsilon

  let turn: WheelPageTurnResult | null = null
  if (input.deltaY > 0 && atBottom) {
    const page = clampPage(input.pageNumber + 1, input.numPages)
    if (page !== input.pageNumber) turn = { kind: 'page', page, align: 'start' }
  } else if (input.deltaY < 0 && atTop) {
    const page = clampPage(input.pageNumber - 1, input.numPages)
    if (page !== input.pageNumber) turn = { kind: 'page', page, align: 'end' }
  }

  if (!turn) return { kind: 'scroll' }
  const cooldown = input.cooldownMs ?? WHEEL_PAGE_TURN_COOLDOWN_MS
  if (input.nowMs - input.lastTurnAtMs < cooldown) return { kind: 'ignore' }
  return turn
}

export interface RailScrollInput {
  slotTop: number
  slotHeight: number
  railTop: number
  railClientHeight: number
  railScrollTop: number
  railScrollHeight: number
}

/** 只改侧栏 scrollTop，避免 scrollIntoView 带动对话框祖先。 */
export function computeRailScrollTop(input: RailScrollInput): number {
  const slotTopInContent = input.slotTop - input.railTop + input.railScrollTop
  const centered = slotTopInContent + input.slotHeight / 2 - input.railClientHeight / 2
  const maxScroll = Math.max(0, input.railScrollHeight - input.railClientHeight)
  return Math.min(maxScroll, Math.max(0, centered))
}

/** 预览外扩: 墨水框外留白, 避免描边压在笔画上(相对页宽/页高) */
export const BBOX_PAD_X = 0.01
export const BBOX_PAD_Y = 0.008

export function padNormalizedBbox(
  bbox: [number, number, number, number],
  padX = BBOX_PAD_X,
  padY = BBOX_PAD_Y,
): [number, number, number, number] {
  const clamp = (v: number) => Math.min(1, Math.max(0, v))
  const left = Math.min(bbox[0], bbox[2])
  const top = Math.min(bbox[1], bbox[3])
  const right = Math.max(bbox[0], bbox[2])
  const bottom = Math.max(bbox[1], bbox[3])
  return [
    clamp(left - padX),
    clamp(top - padY),
    clamp(right + padX),
    clamp(bottom + padY),
  ]
}

/** 归一化 bbox [x0,y0,x1,y1] (0..1, 左上原点) -> CSS 百分比矩形(随画布缩放自适应) */
export function bboxToCssRect(
  bbox: [number, number, number, number],
): { left: string; top: string; width: string; height: string } {
  const [x0, y0, x1, y1] = bbox
  const clamp = (v: number) => Math.min(1, Math.max(0, v))
  const left = clamp(Math.min(x0, x1))
  const top = clamp(Math.min(y0, y1))
  const width = Math.max(0, clamp(Math.max(x0, x1)) - left)
  const height = Math.max(0, clamp(Math.max(y0, y1)) - top)
  return {
    left: `${(left * 100).toFixed(3)}%`,
    top: `${(top * 100).toFixed(3)}%`,
    width: `${(width * 100).toFixed(3)}%`,
    height: `${(height * 100).toFixed(3)}%`,
  }
}

/** 归一化 bbox -> 像素矩形(按已渲染页面宽高; 缩放已含在宽高内) */
export function bboxToPixels(
  bbox: [number, number, number, number],
  pageWidthPx: number,
  pageHeightPx: number,
): { left: number; top: number; width: number; height: number } {
  const [x0, y0, x1, y1] = bbox
  const left = Math.min(x0, x1) * pageWidthPx
  const top = Math.min(y0, y1) * pageHeightPx
  return {
    left,
    top,
    width: Math.abs(x1 - x0) * pageWidthPx,
    height: Math.abs(y1 - y0) * pageHeightPx,
  }
}

export function downloadPdfBytes(data: Uint8Array, filename = 'preview.pdf'): void {
  const blob = new Blob([data], { type: 'application/pdf' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
