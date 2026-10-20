import assert from 'node:assert/strict'
import test from 'node:test'
import {
  MAX_ZOOM,
  MIN_ZOOM,
  WHEEL_PAGE_TURN_COOLDOWN_MS,
  clampPage,
  clampZoom,
  clonePdfBytes,
  computeRailScrollTop,
  copyPdfBytes,
  formatPdfParseError,
  pageNumbers,
  resolveWheelPageTurn,
} from '../src/pages/result/pdfPreview/pdfPreviewMath.ts'

test('clampPage：有总页时夹取，无总页时仍至少为 1', () => {
  assert.equal(clampPage(0, 10), 1)
  assert.equal(clampPage(12, 10), 10)
  assert.equal(clampPage(3, 10), 3)
  assert.equal(clampPage(0, 0), 1)
})

test('同形态：缩放夹取也按上下限收口，不按页码特例分支', () => {
  assert.equal(clampZoom(0.1), MIN_ZOOM)
  assert.equal(clampZoom(9), MAX_ZOOM)
  assert.equal(clampZoom(1.2), 1.25)
})

test('formatPdfParseError 保留底层原因，非 Error 走未知错误', () => {
  assert.equal(
    formatPdfParseError(new Error('Setting up fake worker failed')),
    'PDF 解析失败: Setting up fake worker failed',
  )
  assert.equal(formatPdfParseError('not-an-error'), 'PDF 解析失败: 未知错误')
})

test('copy/clone 产出独立字节，原缓冲被改动不影响副本', () => {
  const source = new Uint8Array([0x25, 0x50, 0x44, 0x46]).buffer
  const copied = copyPdfBytes(source)
  new Uint8Array(source)[0] = 0
  assert.deepEqual(Array.from(copied), [0x25, 0x50, 0x44, 0x46])

  const first = new Uint8Array([1, 2, 3])
  const cloned = clonePdfBytes(first)
  first[0] = 9
  assert.deepEqual(Array.from(cloned), [1, 2, 3])
})

test('pageNumbers 以页码为值，空文档不造页', () => {
  assert.deepEqual(pageNumbers(0), [])
  assert.deepEqual(pageNumbers(3), [1, 2, 3])
})

test('滚到底再向下滚才翻下一页；页中滚动不翻页', () => {
  const viewport = {
    pageNumber: 3,
    numPages: 138,
    scrollTop: 400,
    scrollHeight: 1018,
    clientHeight: 485,
    nowMs: 10_000,
    lastTurnAtMs: 0,
  }
  assert.deepEqual(resolveWheelPageTurn({ ...viewport, deltaY: 80 }), { kind: 'scroll' })
  assert.deepEqual(
    resolveWheelPageTurn({ ...viewport, scrollTop: 533, deltaY: 80 }),
    { kind: 'page', page: 4, align: 'start' },
  )
})

test('同形态第二例：滚到顶再向上滚才翻上一页，短页冷却期内不连跳', () => {
  const shortPage = {
    pageNumber: 12,
    numPages: 20,
    scrollTop: 0,
    scrollHeight: 400,
    clientHeight: 485,
    deltaY: 120,
    nowMs: 20_000,
    lastTurnAtMs: 0,
  }
  assert.deepEqual(
    resolveWheelPageTurn(shortPage),
    { kind: 'page', page: 13, align: 'start' },
  )
  assert.deepEqual(
    resolveWheelPageTurn({
      ...shortPage,
      pageNumber: 2,
      deltaY: -80,
      lastTurnAtMs: 0,
    }),
    { kind: 'page', page: 1, align: 'end' },
  )
  assert.deepEqual(
    resolveWheelPageTurn({
      ...shortPage,
      lastTurnAtMs: 20_000 - (WHEEL_PAGE_TURN_COOLDOWN_MS - 10),
    }),
    { kind: 'ignore' },
  )
  assert.deepEqual(
    resolveWheelPageTurn({ ...shortPage, pageNumber: 20 }),
    { kind: 'scroll' },
  )
  assert.deepEqual(
    resolveWheelPageTurn({ ...shortPage, pageNumber: 1, deltaY: -80 }),
    { kind: 'scroll' },
  )
})

test('缩略图侧栏按选中页滚入视口中心，不依赖祖先 scrollIntoView', () => {
  assert.equal(
    computeRailScrollTop({
      slotTop: 200,
      slotHeight: 160,
      railTop: 130,
      railClientHeight: 485,
      railScrollTop: 0,
      railScrollHeight: 22000,
    }),
    0,
  )
  assert.equal(
    computeRailScrollTop({
      slotTop: 130,
      slotHeight: 160,
      railTop: 130,
      railClientHeight: 485,
      railScrollTop: 0,
      railScrollHeight: 22000,
    }),
    0,
  )
})

test('同形态第二例：远处页码把侧栏滚到选中槽中心并夹在可滚动范围', () => {
  assert.equal(
    computeRailScrollTop({
      slotTop: 130,
      slotHeight: 148,
      railTop: 130,
      railClientHeight: 485,
      railScrollTop: 0,
      railScrollHeight: 22000,
    }),
    0,
  )
  const far = computeRailScrollTop({
    slotTop: 130,
    slotHeight: 148,
    railTop: 130,
    railClientHeight: 485,
    railScrollTop: 11840,
    railScrollHeight: 22000,
  })
  assert.equal(far, 11840 + 148 / 2 - 485 / 2)
  assert.equal(
    computeRailScrollTop({
      slotTop: 130,
      slotHeight: 148,
      railTop: 130,
      railClientHeight: 485,
      railScrollTop: 21800,
      railScrollHeight: 22000,
    }),
    22000 - 485,
  )
})
