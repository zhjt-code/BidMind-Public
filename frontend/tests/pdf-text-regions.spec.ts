import assert from 'node:assert/strict'
import test from 'node:test'

import { selectPdfjsTextItems, textItemsToRegions } from '../src/pages/result/pdfPreview/pdfjsTextRegions.ts'

test('unknown 文本流先排除 null 再认 str，Boolean() 不能当类型守卫', () => {
  const selected = selectPdfjsTextItems([
    null,
    { str: '安全生产许可证' },
    { type: 'beginMarkedContent' },
  ])
  assert.deepEqual(selected.map((item) => item.str), ['安全生产许可证'])
})

test('同形态第二例：undefined 与无 str 标记项不混入投标保证金文本项', () => {
  const selected = selectPdfjsTextItems([
    undefined,
    { id: 'MC0' },
    { str: '投标保证金' },
  ])
  assert.deepEqual(selected.map((item) => item.str), ['投标保证金'])
})

test('pdf.js 文本项按视口矩阵换成左上原点归一化框', () => {
  const viewport = {
    width: 100,
    height: 200,
    transform: [1, 0, 0, -1, 0, 200],
  }
  // PDF 原点在左下: x=10, y=150, width=40, height=10 → 视口 top = (200-150-10)/200
  const regions = textItemsToRegions(
    [
      {
        str: '安全生产许可证',
        transform: [1, 0, 0, 1, 10, 150],
        width: 40,
        height: 10,
      },
    ],
    viewport,
  )
  const item = regions.find((region) => region.text.includes('安全生产许可证'))
  assert.ok(item)
  assert.ok(Math.abs(item.bbox[0] - 0.10) < 0.02)
  assert.ok(item.bbox[1] < item.bbox[3])
  assert.ok(item.bbox[2] > item.bbox[0])
})

test('同形态第二例：同一基线的建造师证书项会并成行级区域', () => {
  const viewport = {
    width: 100,
    height: 100,
    transform: [1, 0, 0, -1, 0, 100],
  }
  const regions = textItemsToRegions(
    [
      {
        str: '一级',
        transform: [1, 0, 0, 1, 10, 40],
        width: 10,
        height: 8,
      },
      {
        str: '建造师注册证书',
        transform: [1, 0, 0, 1, 22, 40],
        width: 30,
        height: 8,
      },
    ],
    viewport,
  )
  const line = regions.find((region) => region.text.includes('一级') && region.text.includes('建造师'))
  assert.ok(line, '应产出行级合并区，而不是只留单字')
})
