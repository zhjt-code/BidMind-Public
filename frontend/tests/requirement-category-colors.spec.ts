/**
 * 防御测试：招标要求分类配色契约（RequirementFilterBar 视觉优化）。
 *
 * 锁定两条业务红线，防止后续误改：
 *   红线1: 类别色板一律不含 red —— red 专属"否决项 / 失败"语义；
 *   红线2: "仅显示否决项"开关必须用 red，与类别色互斥。
 * 同时保证 6 类配色字段完整、未知类别有中性兜底（防运行时无样式 chip）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ALL_FILTER_CLASSES,
  FALLBACK_CATEGORY_COLOR,
  MANDATORY_FILTER_CLASSES,
  categoryColorClasses,
  getCategoryColorClasses,
  type CategoryColorClasses,
} from '../src/lib/constants.ts'

const KNOWN_LABELS = ['资质要求', '技术要求', '商务要求', '业绩要求', '人员要求', '格式要求']
const FIELDS: (keyof CategoryColorClasses)[] = ['chipInactive', 'chipActive', 'badge', 'dot']

test('6 类分类配色字段完整且非空', () => {
  for (const label of KNOWN_LABELS) {
    const c = categoryColorClasses[label]
    assert.ok(c, `缺少分类配色: ${label}`)
    for (const f of FIELDS) {
      assert.equal(typeof c[f], 'string')
      assert.ok(c[f].length > 0, `${label}.${f} 不应为空`)
    }
  }
})

test('红线1：类别色板一律不含 red（red 专属否决项语义）', () => {
  for (const label of KNOWN_LABELS) {
    const c = categoryColorClasses[label]
    for (const f of FIELDS) {
      assert.ok(!c[f].includes('red'), `分类 ${label}.${f} 不得使用 red：${c[f]}`)
    }
  }
})

test('红线2：仅显示否决项开关必须使用 red', () => {
  assert.ok(MANDATORY_FILTER_CLASSES.active.includes('red'))
  assert.ok(MANDATORY_FILTER_CLASSES.inactive.includes('red'))
})

test('全部筛选项不占用任一类别色与否决红（可用独立中性色相）', () => {
  // 类别色全集（含格式要求的 slate）+ 否决红，"全部"均不得占用，防撞色误导。
  const RESERVED_HUES = ['red', 'blue', 'cyan', 'amber', 'violet', 'emerald', 'slate']
  for (const cls of [ALL_FILTER_CLASSES.active, ALL_FILTER_CLASSES.inactive]) {
    for (const hue of RESERVED_HUES) {
      assert.ok(!cls.includes(hue), `全部筛选项不应占用 ${hue}：${cls}`)
    }
  }
})

test('全部筛选项不得回退为黑白灰（历史反复点：gray-800→500→200 均被否决）', () => {
  // 用户明确要求"全部"用彩色中性色相而非黑白灰，固化为护栏防再次回退。
  const GRAYSCALE = ['gray', 'slate', 'zinc', 'neutral', 'stone']
  for (const cls of [ALL_FILTER_CLASSES.active, ALL_FILTER_CLASSES.inactive]) {
    for (const g of GRAYSCALE) {
      assert.ok(!cls.includes(g), `全部筛选项不得使用灰阶 ${g}（应为彩色中性色相）：${cls}`)
    }
  }
})

test('getCategoryColorClasses 已知 label 命中、未知 label 兜底为中性灰', () => {
  assert.equal(getCategoryColorClasses('技术要求'), categoryColorClasses['技术要求'])
  assert.equal(getCategoryColorClasses('不存在的分类'), FALLBACK_CATEGORY_COLOR)
  for (const f of FIELDS) {
    assert.ok(!FALLBACK_CATEGORY_COLOR[f].includes('red'), `兜底色 ${f} 不得含 red`)
  }
})
