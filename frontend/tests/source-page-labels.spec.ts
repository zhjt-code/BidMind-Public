import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatPageRangeLabel,
  normalizeLocationLabel,
} from '../src/services/sourcePageLabels.ts'

// ===== formatPageRangeLabel =====

test('formatPageRangeLabel 单页返回 P{n}', () => {
  assert.equal(formatPageRangeLabel(9), 'P9')
  assert.equal(formatPageRangeLabel(9, 9), 'P9', 'start === end 视为单页')
  assert.equal(formatPageRangeLabel(9, null), 'P9')
  assert.equal(formatPageRangeLabel(9, undefined), 'P9')
})

test('formatPageRangeLabel 跨页统一为 P{a}-P{b}', () => {
  assert.equal(formatPageRangeLabel(9, 10), 'P9-P10')
  assert.equal(formatPageRangeLabel(3, 12), 'P3-P12')
})

test('formatPageRangeLabel 起始页缺失返回空串', () => {
  assert.equal(formatPageRangeLabel(null), '')
  assert.equal(formatPageRangeLabel(undefined), '')
  assert.equal(formatPageRangeLabel(0), '', '0 / 负数视为非法页码')
  assert.equal(formatPageRangeLabel(-1), '')
})

test('formatPageRangeLabel 终止页非法时退化为单页', () => {
  assert.equal(formatPageRangeLabel(5, 0), 'P5')
  assert.equal(formatPageRangeLabel(5, -3), 'P5')
})

// ===== normalizeLocationLabel =====

test('normalizeLocationLabel 处理空值与语义占位返回空标签', () => {
  for (const empty of [null, undefined, '', '   ', '无', 'N/A', '——', '-']) {
    const r = normalizeLocationLabel(empty as any)
    assert.equal(r.label, '', `输入 ${JSON.stringify(empty)} 应得空 label`)
    assert.equal(r.page, null)
    assert.equal(r.endPage, null)
  }
})

test('normalizeLocationLabel 纯数字 / "第N页" / "P5" 统一规范化为 P{n}', () => {
  assert.deepEqual(normalizeLocationLabel('5'), {
    label: 'P5', extra: null, page: 5, endPage: null,
  })
  assert.deepEqual(normalizeLocationLabel('第4页'), {
    label: 'P4', extra: null, page: 4, endPage: null,
  })
  assert.deepEqual(normalizeLocationLabel('P5'), {
    label: 'P5', extra: null, page: 5, endPage: null,
  })
  assert.deepEqual(normalizeLocationLabel('页码: 12'), {
    label: 'P12', extra: null, page: 12, endPage: null,
  })
})

test('normalizeLocationLabel 跨页字符串规范化为 P{a}-P{b}', () => {
  const r = normalizeLocationLabel('第4-5页')
  assert.equal(r.label, 'P4-P5')
  assert.equal(r.page, 4)
  assert.equal(r.endPage, 5)

  const r2 = normalizeLocationLabel('第8至10页')
  assert.equal(r2.label, 'P8-P10')
  assert.equal(r2.page, 8)
  assert.equal(r2.endPage, 10)
})

test('normalizeLocationLabel 保留章节后缀，避免丢失 LLM 返回的语义', () => {
  const r = normalizeLocationLabel('第4页《施工资质》章节')
  assert.equal(r.page, 4)
  assert.equal(r.label, 'P4 · 《施工资质》章节')
  assert.equal(r.extra, '《施工资质》章节')
})

test('normalizeLocationLabel 无页号时退化为纯章节文本', () => {
  const r = normalizeLocationLabel('—— 资质章节')
  assert.equal(r.page, null)
  assert.equal(r.label, '资质章节')
  assert.equal(r.extra, '资质章节')
})
