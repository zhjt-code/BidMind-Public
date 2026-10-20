/**
 * 防御测试: 串标证据等级筛选纯函数。
 *
 * 关键不变式 (从《collusion.mdc 二、三级证据体系》互斥分类导出):
 * - finding 模式严格语义: 一条 finding 不得跨等级计数; 'first' 仅计 first, 不计 second/third。
 * - pair 模式包含语义: 一个 pair 含 first+third 时, first 与 third 各计 1, second 计 0。
 * - 'all' 视为不筛选,直通。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countFindingsByLevel,
  filterFindingsByLevel,
  pairContainsEvidenceLevel,
  countPairsByEvidenceLevel,
} from '../src/pages/collusionResult/evidenceFiltering.ts'

const findings = [
  { level: 'first' },
  { level: 'first' },
  { level: 'second' },
  { level: 'second' },
  { level: 'second' },
  { level: 'third' },
]

test('countFindingsByLevel 各档独立计数,禁止 first 累计入 second/third', () => {
  const counts = countFindingsByLevel(findings)
  assert.equal(counts.all, 6)
  assert.equal(counts.first, 2, 'first 必须为 2,而非阈值累计')
  assert.equal(counts.second, 3, 'second 必须为 3,first 不得累计入 second')
  assert.equal(counts.third, 1, 'third 必须为 1,first/second 不得累计入 third')
})

test('countFindingsByLevel 空数组返回全零', () => {
  const counts = countFindingsByLevel([])
  assert.deepEqual(counts, { all: 0, first: 0, second: 0, third: 0 })
})

test('countFindingsByLevel 忽略未知 level (如 unknown)', () => {
  const counts = countFindingsByLevel([{ level: 'unknown' }, { level: 'first' }])
  assert.equal(counts.all, 2)
  assert.equal(counts.first, 1)
  assert.equal(counts.second, 0)
  assert.equal(counts.third, 0)
})

test('filterFindingsByLevel 严格等号匹配,选 third 时仅返回 third', () => {
  const onlyThird = filterFindingsByLevel(findings, 'third')
  assert.equal(onlyThird.length, 1)
  assert.ok(onlyThird.every(f => f.level === 'third'),
    'first/second 必须被过滤,不得放行')
})

test('filterFindingsByLevel 选 second 时不放行 first', () => {
  const onlySecond = filterFindingsByLevel(findings, 'second')
  assert.equal(onlySecond.length, 3)
  assert.ok(onlySecond.every(f => f.level === 'second'),
    '阈值语义被消除后,first 不再被算作 "含二级"')
})

test('filterFindingsByLevel level=all 原样返回 (浅拷贝)', () => {
  const all = filterFindingsByLevel(findings, 'all')
  assert.equal(all.length, findings.length)
  assert.notStrictEqual(all, findings)
})

test('pairContainsEvidenceLevel level=all 直通返回 true', () => {
  assert.equal(pairContainsEvidenceLevel({ findings: [] }, 'all'), true)
})

test('pairContainsEvidenceLevel pair 含 first+second 时,third 返回 false', () => {
  const pair = { findings: [{ level: 'first' }, { level: 'second' }] }
  assert.equal(pairContainsEvidenceLevel(pair, 'first'), true)
  assert.equal(pairContainsEvidenceLevel(pair, 'second'), true)
  assert.equal(pairContainsEvidenceLevel(pair, 'third'), false,
    '阈值语义被消除后,含 first 的 pair 在 third 筛选下不再误显示')
})

test('countPairsByEvidenceLevel pair 含 first+third 时,first 与 third 各计 1,second 计 0', () => {
  const pairs = [
    { findings: [{ level: 'first' }, { level: 'third' }] },
    { findings: [{ level: 'second' }] },
  ]
  const counts = countPairsByEvidenceLevel(pairs)
  assert.equal(counts.all, 2)
  assert.equal(counts.first, 1)
  assert.equal(counts.second, 1)
  assert.equal(counts.third, 1)
})

test('countPairsByEvidenceLevel 同一 pair 含多档等级时可同时计入多档 (包含语义)', () => {
  const pair = {
    findings: [{ level: 'first' }, { level: 'second' }, { level: 'third' }],
  }
  const counts = countPairsByEvidenceLevel([pair])
  assert.equal(counts.first, 1)
  assert.equal(counts.second, 1)
  assert.equal(counts.third, 1)
})
