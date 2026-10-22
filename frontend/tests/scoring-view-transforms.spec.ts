/**
 * 结果页"评分项视图"纯函数防御测试（分组轴 = 原文评分卷单轴）。
 *
 * 映射真实风险:
 * - : 无分值项(资格/格式, maxScore=0)不得混入评分项全集，否则污染评分项视图；
 * 且卷内得分一律取后端权威桶，禁止前端逐条累加。
 * - : reconcile 回填满分到 normal 主行的重点评审项(is_key_review && maxScore>0)必须计入。
 * - : 卷桶键与明细行过滤键必须同一构造，否则点击卷筛选后列表为空；同卷标签
 * 有分歧时不得让首个标签冒名整卷。
 * - : 多包"包·卷"复合键与"未识别归属"是中文契约字面，前后端须逐字一致。
 * - : 左栏卷序与中栏列表序必须同源，否则三栏顺序割裂。
 * - : 卷轴只能来自原文组标签，不得回落六类枚举压缩。
 * - 前向兼容契约: categoryBasis 显式 null（后端零命中）须在条目徽标上显式可见；
 * 字段缺失（旧数据）不算零命中。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import type { ReviewResult, VolumeScore } from '../src/pages/result/types.ts'
import {
  isScoringUniverseMember,
  filterScoringResults,
  isCategoryInferenceMiss,
  UNCATEGORIZED_CATEGORY_LABEL,
  buildInheritedVolumeLabels,
  createScoringVolumeKeyResolver,
  formatScoringVolumeLabel,
  buildScoringVolumeOrder,
  buildScoringVolumeNavItems,
  filterByScoringVolume,
  sortScoringResultsByVolume,
  UNASSIGNED_LOT_LABEL,
  LOT_VOLUME_SEPARATOR,
} from '../src/pages/result/scoringViewTransforms.ts'

const makeResult = (o: Partial<ReviewResult> = {}): ReviewResult => ({
  id: 1,
  category: 'technical',
  requirementSummary: 'req',
  responseSummary: 'resp',
  complianceLevel: 'fully',
  score: 80,
  comment: 'comment',
  riskLevel: 'none',
  ...o,
})

// ===== isScoringUniverseMember（对齐后端口径）=====

test('scoring_item 恒为评分项', () => {
  assert.equal(isScoringUniverseMember(makeResult({ requirementKind: 'scoring_item' })), true)
})

test('is_key_review 且满分>0 的回填主行计入评分项', () => {
  assert.equal(
    isScoringUniverseMember(makeResult({ requirementKind: 'normal', isKeyReview: true, maxScore: 15 })),
    true,
  )
})

test('is_key_review 但满分为 0/缺失 不计入（防资格/格式项污染）', () => {
  assert.equal(
    isScoringUniverseMember(makeResult({ isKeyReview: true, maxScore: 0 })),
    false,
  )
  assert.equal(
    isScoringUniverseMember(makeResult({ isKeyReview: true, maxScore: null })),
    false,
  )
})

test('普通要求非评分项', () => {
  assert.equal(isScoringUniverseMember(makeResult({ requirementKind: 'normal', isKeyReview: false })), false)
})

// ===== filterScoringResults =====

test('filterScoringResults 仅保留评分项全集', () => {
  const list = [
    makeResult({ id: 1, requirementKind: 'scoring_item' }),
    makeResult({ id: 2, requirementKind: 'normal', isKeyReview: true, maxScore: 10 }),
    makeResult({ id: 3, requirementKind: 'normal', isKeyReview: false }),
    makeResult({ id: 4, isKeyReview: true, maxScore: 0 }),
  ]
  const ids = filterScoringResults(list).map((r) => r.id)
  assert.deepEqual(ids, [1, 2])
})

// ===== 分类零命中标记（前向兼容契约；单轴化后由条目徽标承担 fail-visible）=====

test('前向兼容: categoryBasis 显式为 null 判为零命中，徽标须显示"未分类"', () => {
  assert.equal(isCategoryInferenceMiss(makeResult({ category: 'technical', categoryBasis: null })), true)
  assert.equal(UNCATEGORIZED_CATEGORY_LABEL, '未分类')
})

test('前向兼容: 旧数据无 categoryBasis 字段不算零命中（不得误标未分类）', () => {
  assert.equal(isCategoryInferenceMiss(makeResult({ category: 'technical' })), false)
})

test('categoryBasis 非空关键词不算零命中', () => {
  assert.equal(
    isCategoryInferenceMiss(makeResult({ category: 'performance', categoryBasis: '业绩' })),
    false,
  )
})

// ===== 评分卷桶键（与后端 _aggregate_scoring_scope 桶键构造同构）=====

// 映射真实风险:
// 桶键与明细行过滤键错位 → 点击卷筛选后列表为空 / 首个标签冒名整卷。
// 多包文档"包·卷"复合键与"未识别归属"是中文契约字面，两侧必须逐字一致。
// 卷轴必须来自原文组标签，不得回落六类枚举压缩。

const keyOf = (list: ReviewResult[], target: ReviewResult, multiLot = false): string =>
  createScoringVolumeKeyResolver(list, multiLot)(target)

test('桶键优先取条目自带的原文组标签', () => {
  const item = makeResult({ id: 1, volume: 'technical', volumeLabel: '商务技术评审部分' })
  assert.equal(keyOf([item], item), '商务技术评审部分')
})

test('同粗卷标签唯一时，继承给该卷缺标签的行', () => {
  const labeled = makeResult({ id: 1, volume: 'technical', volumeLabel: '设计方案' })
  const bare = makeResult({ id: 2, volume: 'technical' })
  const list = [labeled, bare]
  assert.deepEqual(buildInheritedVolumeLabels(list), { technical: '设计方案' })
  assert.equal(keyOf(list, bare), '设计方案')
})

test('同粗卷标签有分歧时不继承，缺标签行回退标准卷名（禁止首标签冒名整卷）', () => {
  const a = makeResult({ id: 1, volume: 'technical', volumeLabel: '设计方案' })
  const b = makeResult({ id: 2, volume: 'technical', volumeLabel: '施工方案' })
  const bare = makeResult({ id: 3, volume: 'technical' })
  const list = [a, b, bare]
  assert.deepEqual(buildInheritedVolumeLabels(list), {})
  assert.equal(keyOf(list, bare), '技术标')
  assert.equal(keyOf(list, a), '设计方案')
})

test('无任何组标签时回退标准卷名映射；卷缺失归"其他"', () => {
  const priced = makeResult({ id: 1, volume: 'price' })
  const none = makeResult({ id: 2 })
  const list = [priced, none]
  assert.equal(keyOf(list, priced), '价格')
  assert.equal(keyOf(list, none), '其他')
})

test('多包文档桶键为"包·卷"复合键', () => {
  const item = makeResult({ id: 1, volume: 'technical', volumeLabel: '技术部分', lot: '采购包1' })
  assert.equal(keyOf([item], item, true), '采购包1·技术部分')
})

test('多包且 lot 缺失时用"未识别归属"前缀，不静默并入任何包', () => {
  const item = makeResult({ id: 1, volume: 'price', volumeLabel: '价格部分' })
  assert.equal(keyOf([item], item, true), '未识别归属·价格部分')
})

test('契约字面: 未识别归属标签与"包·卷"分隔符与后端常量逐字一致', () => {
  assert.equal(UNASSIGNED_LOT_LABEL, '未识别归属')
  assert.equal(LOT_VOLUME_SEPARATOR, '·')
})

// ===== formatScoringVolumeLabel（展示清洗）=====

test('卷名自带满分括注在展示时剥离（全角与半角括号均覆盖）', () => {
  assert.equal(formatScoringVolumeLabel('磋商报价（20分）'), '磋商报价')
  assert.equal(formatScoringVolumeLabel('磋商报价(20分)'), '磋商报价')
  assert.equal(formatScoringVolumeLabel('技术部分（49.5分）'), '技术部分')
})

test('无括注或剥离后为空时回退原键，不产生空标签', () => {
  assert.equal(formatScoringVolumeLabel('技术部分'), '技术部分')
  assert.equal(formatScoringVolumeLabel('（20分）'), '（20分）')
})

// ===== buildScoringVolumeNavItems =====

const volumeScore = (o: Partial<VolumeScore> = {}): VolumeScore => ({
  estimated: 0,
  max: 0,
  count: 0,
  ...o,
})

test('导航得分取自后端权威卷桶，禁止前端按条目累加', () => {
  const list = [
    makeResult({ id: 1, volumeLabel: '技术部分', estimatedScore: 10 }),
    makeResult({ id: 2, volumeLabel: '技术部分', estimatedScore: 10 }),
  ]
  // 后端桶给 15（折算/clamp 后口径），前端若逐条累加会得 20。
  const nav = buildScoringVolumeNavItems(
    list,
    { 技术部分: volumeScore({ estimated: 15, max: 50, count: 2 }) },
    createScoringVolumeKeyResolver(list, false),
    { hasEstimate: true },
  )
  assert.deepEqual(nav, [{ key: '技术部分', label: '技术部分', count: 2, earned: 15, max: 50 }])
})

test('无预估产出时 earned 为 null，禁止显示 0 被误读为"评了0分"', () => {
  const list = [makeResult({ id: 1, volumeLabel: '技术部分' })]
  const nav = buildScoringVolumeNavItems(
    list,
    { 技术部分: volumeScore({ estimated: 0, max: 50, count: 1 }) },
    createScoringVolumeKeyResolver(list, false),
    { hasEstimate: false },
  )
  assert.equal(nav[0].earned, null)
  assert.equal(nav[0].max, 50)
})

test('后端无对应桶时该桶仍出现且满分记 0（fail-visible: 键不一致须可见, 不得静默丢项）', () => {
  const list = [makeResult({ id: 1, volumeLabel: '未知卷' })]
  const nav = buildScoringVolumeNavItems(
    list,
    {},
    createScoringVolumeKeyResolver(list, false),
    { hasEstimate: true },
  )
  assert.deepEqual(nav, [{ key: '未知卷', label: '未知卷', count: 1, earned: null, max: 0 }])
})

test('桶顺序沿用后端桶序（文档内出现顺序），后端未含的桶排其后', () => {
  const list = [
    makeResult({ id: 1, volumeLabel: '价格评审部分' }),
    makeResult({ id: 2, volumeLabel: '商务技术评审部分' }),
    makeResult({ id: 3, volumeLabel: '游离卷' }),
  ]
  const nav = buildScoringVolumeNavItems(
    list,
    {
      商务技术评审部分: volumeScore({ max: 20, count: 1 }),
      价格评审部分: volumeScore({ max: 80, count: 1 }),
    },
    createScoringVolumeKeyResolver(list, false),
    { hasEstimate: true },
  )
  assert.deepEqual(nav.map((v) => v.key), ['商务技术评审部分', '价格评审部分', '游离卷'])
})

test('导航项计数按前端解析键统计，与筛选结果条数一致（点击必有结果）', () => {
  const list = [
    makeResult({ id: 1, volume: 'technical', volumeLabel: '技术部分' }),
    makeResult({ id: 2, volume: 'technical' }),
    makeResult({ id: 3, volume: 'price', volumeLabel: '价格部分' }),
  ]
  const resolve = createScoringVolumeKeyResolver(list, false)
  const nav = buildScoringVolumeNavItems(list, {}, resolve, { hasEstimate: true })
  for (const item of nav) {
    assert.equal(filterByScoringVolume(list, item.key, resolve).length, item.count)
  }
})

// ===== filterByScoringVolume =====

test('filterByScoringVolume: null 不过滤；按桶键过滤且与导航同源解析', () => {
  const list = [
    makeResult({ id: 1, volumeLabel: '技术部分' }),
    makeResult({ id: 2, volumeLabel: '价格部分' }),
  ]
  const resolve = createScoringVolumeKeyResolver(list, false)
  assert.equal(filterByScoringVolume(list, null, resolve).length, 2)
  assert.deepEqual(filterByScoringVolume(list, '技术部分', resolve).map((r) => r.id), [1])
})

// ===== sortScoringResultsByVolume（三栏同序: 列表序必须与左栏卷序同源）=====

test('列表按 卷序→来源页→id 排，卷序与左栏导航同源', () => {
  const list = [
    makeResult({ id: 30, volumeLabel: '价格评审部分', requirementSourcePages: [42] }),
    makeResult({ id: 20, volumeLabel: '商务技术评审部分', requirementSourcePages: [42] }),
    makeResult({ id: 11, volumeLabel: '商务技术评审部分', requirementSourcePages: [40] }),
  ]
  const resolve = createScoringVolumeKeyResolver(list, false)
  // 后端桶序为 商务技术→价格，列表须据此排，而非按前端首见序（价格在前）。
  const order = buildScoringVolumeOrder(
    list,
    ['商务技术评审部分', '价格评审部分'],
    resolve,
  )
  assert.deepEqual(order, ['商务技术评审部分', '价格评审部分'])
  const ids = sortScoringResultsByVolume(list, resolve, order).map((r) => r.id)
  assert.deepEqual(ids, [11, 20, 30])
})

test('卷序中缺失的桶排最后；缺来源页的项排同卷尾部且不崩溃', () => {
  const list = [
    makeResult({ id: 1, volumeLabel: '游离卷' }),
    makeResult({ id: 2, volumeLabel: '技术部分' }),
    makeResult({ id: 3, volumeLabel: '技术部分', requirementSourcePages: [3] }),
  ]
  const resolve = createScoringVolumeKeyResolver(list, false)
  const ids = sortScoringResultsByVolume(list, resolve, ['技术部分']).map((r) => r.id)
  assert.deepEqual(ids, [3, 2, 1])
})

test('buildScoringVolumeOrder: 后端未含的桶按前端首见序排其后', () => {
  const list = [
    makeResult({ id: 1, volumeLabel: '游离卷A' }),
    makeResult({ id: 2, volumeLabel: '价格评审部分' }),
    makeResult({ id: 3, volumeLabel: '游离卷B' }),
  ]
  const resolve = createScoringVolumeKeyResolver(list, false)
  assert.deepEqual(
    buildScoringVolumeOrder(list, ['价格评审部分'], resolve),
    ['价格评审部分', '游离卷A', '游离卷B'],
  )
})

// ===== 一致性契约（源码护栏）: 禁止前端重算预估总分 =====

test('一致性契约: 评分视图纯函数模块不触碰单条 estimatedScore（禁止前端重算）', () => {
  const source = readFileSync(
    new URL('../src/pages/result/scoringViewTransforms.ts', import.meta.url),
    'utf8',
  )
  assert.equal(
    /estimatedScore/.test(source),
    false,
    '评分换轴后纯函数模块不得读取单条 estimatedScore 做任何聚合',
  )
})

test('一致性契约: 左栏预估总分只消费后端权威聚合（summary），不逐条累加', () => {
  const navSource = readFileSync(
    new URL('../src/pages/result/CategoryNav.tsx', import.meta.url),
    'utf8',
  )
  assert.equal(/scoringTotal\.estimated/.test(navSource), true, '总分展示必须来自 scoringTotal')
  assert.equal(
    /estimatedScore/.test(navSource),
    false,
    'CategoryNav 不得读取单条 estimatedScore 重算总分',
  )
  assert.equal(
    navSource.includes('预估仅供参考，不代表实际评标得分'),
    true,
    '左栏预估总分必须可见声明预估不代表实际评标得分',
  )

  const resultSource = readFileSync(
    new URL('../src/pages/Result.tsx', import.meta.url),
    'utf8',
  )
  const detailSource = readFileSync(
    new URL('../src/pages/result/ResultDetail.tsx', import.meta.url),
    'utf8',
  )
  assert.equal(
    detailSource.includes('预估仅供参考，不代表实际评标得分'),
    true,
    '明细卡预估评标必须可见声明预估不代表实际评标得分',
  )
  assert.equal(
    /summary\.estimatedTotalScore/.test(resultSource),
    true,
    'scoringTotal 必须直接取自 summary.estimatedTotalScore（后端权威聚合）',
  )
})
