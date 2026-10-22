import assert from 'node:assert/strict'
import test from 'node:test'

import {
  bboxToCssRect,
  bboxToPixels,
  padNormalizedBbox,
} from '../src/pages/result/pdfPreview/pdfPreviewMath.ts'

test('归一化 bbox -> 像素: 缩放 1.0 与 1.5 均按渲染宽高线性换算', () => {
  const bbox: [number, number, number, number] = [0.1, 0.2, 0.5, 0.4]
  // zoom 1.0: 页面渲染 600x800
  const base = bboxToPixels(bbox, 600, 800)
  assert.deepEqual(base, { left: 60, top: 160, width: 240, height: 160 })
  // zoom 1.5: 渲染尺寸放大 1.5 倍, 像素矩形同步放大
  const zoomed = bboxToPixels(bbox, 900, 1200)
  assert.deepEqual(zoomed, { left: 90, top: 240, width: 360, height: 240 })
  assert.equal(zoomed.width / base.width, 1.5)
})

test('CSS 百分比矩形: 越界值被夹取, 反向坐标被归正', () => {
  const rect = bboxToCssRect([0.5, 0.25, 0.1, 1.4])
  assert.equal(rect.left, '10.000%')
  assert.equal(rect.top, '25.000%')
  assert.equal(rect.width, '40.000%')
  assert.equal(rect.height, '75.000%')
})

test('预览外扩: 墨水框四周留白且不越出页面', () => {
  const padded = padNormalizedBbox([0.10, 0.20, 0.50, 0.40])
  assert.ok(padded[0] < 0.10)
  assert.ok(padded[1] < 0.20)
  assert.ok(padded[2] > 0.50)
  assert.ok(padded[3] > 0.40)
})

test('页缘框外扩被夹取到 0..1(第二形态: 贴边短框)', () => {
  const padded = padNormalizedBbox([0.0, 0.0, 0.02, 0.02])
  assert.equal(padded[0], 0)
  assert.equal(padded[1], 0)
  assert.ok(padded[2] > 0.02)
  assert.ok(padded[3] > 0.02)
  assert.ok(padded[2] <= 1)
  assert.ok(padded[3] <= 1)
})
