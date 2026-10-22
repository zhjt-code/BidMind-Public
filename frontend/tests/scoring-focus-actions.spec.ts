/**
 * 防御测试：评分核对范围聚合 + 提示条行动区（聚焦评分项 / 新增评分项）。
 *
 * 覆盖的真实风险：
 * - 无具体分值的"重点评审项"必须计入核对范围项数，但禁止计入分值合计
 * （否则用户会以为分值缺口来自漏提，实际是未标注分值）
 * - scoreWeight 为 null / NaN / <= 0 不得被当成 0 分项混入 scoredCount
 * - 分值合计不得出现浮点尾差（20.1 + 0.2 类求和）
 * - 一键聚焦必须复用页面筛选状态与稳定元素 id，禁止依赖 DOM 结构路径
 * - 一键聚焦必须展开含评分项的分类分组，否则默认折叠视图下点完仍看不到条目
 * - 补录入口必须复用既有 openAddDialog('scoring_item')，禁止新造弹窗
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildScoringFocusSummary,
  getScoringFocusGroupLabels,
  isScoringFocusItem,
} from '../src/pages/RequirementReview/helpers.ts'
import type { Requirement } from '../src/pages/requirementReviewTransforms.ts'

const bannerSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)
const filterBarSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/RequirementFilterBar.tsx', import.meta.url),
  'utf8',
)
const pageSource = readFileSync(
  new URL('../src/pages/RequirementReview/index.tsx', import.meta.url),
  'utf8',
)

const makeReq = (overrides: Partial<Requirement> = {}): Requirement => ({
  id: 1,
  category: 'TECH',
  description: 'desc',
  isMandatory: false,
  confirmed: false,
  documentId: null,
  sourcePage: null,
  sourcePages: [],
  mergedCount: null,
  subRequirements: [],
  memberIds: [1],
  reviewRelevance: 'actionable',
  relevanceReason: null,
  requirementKind: 'normal',
  scoreWeight: null,
  isKeyReview: false,
  scoringMetadata: null,
  ...overrides,
})

// ===== isScoringFocusItem：两类都算核对范围 =====

test('isScoringFocusItem 同时认定评分项与无分值重点评审项', () => {
  assert.equal(
    isScoringFocusItem(makeReq({ requirementKind: 'scoring_item', scoreWeight: 20 })),
    true,
    '带分值评分项必须计入核对范围',
  )
  assert.equal(
    isScoringFocusItem(makeReq({ requirementKind: 'normal', isKeyReview: true })),
    true,
    '无具体分值的重点评审项同样属于核对范围',
  )
  assert.equal(
    isScoringFocusItem(makeReq({ requirementKind: 'normal', isKeyReview: false })),
    false,
    '普通要求不属于评分核对范围',
  )
})

// ===== buildScoringFocusSummary：项数与分值口径分离 =====

test('buildScoringFocusSummary 分别统计有分值项与无分值重点项', () => {
  const summary = buildScoringFocusSummary([
    makeReq({ id: 1, requirementKind: 'scoring_item', scoreWeight: 60, category: 'COMM' }),
    makeReq({ id: 2, requirementKind: 'scoring_item', scoreWeight: 20, category: 'TECH' }),
    makeReq({ id: 3, requirementKind: 'scoring_item', scoreWeight: null, category: 'TECH' }),
    makeReq({ id: 4, isKeyReview: true, scoreWeight: null, category: 'QUAL' }),
    makeReq({ id: 5, requirementKind: 'normal' }),
  ])

  assert.equal(summary.totalCount, 4, '4 个评分/重点项都要计入范围总数')
  assert.equal(summary.scoredCount, 2, '仅 2 项带有效分值')
  assert.equal(summary.keyOnlyCount, 2, '无有效分值的 2 项归入重点未标注分值')
  assert.equal(summary.totalScore, 80, '分值合计只累加有效分值项')
})

test('buildScoringFocusSummary 非法分值不得冒充 0 分项计入 scoredCount', () => {
  const summary = buildScoringFocusSummary([
    makeReq({ id: 1, requirementKind: 'scoring_item', scoreWeight: 0 }),
    makeReq({ id: 2, requirementKind: 'scoring_item', scoreWeight: -5 }),
    makeReq({ id: 3, requirementKind: 'scoring_item', scoreWeight: Number.NaN }),
  ])

  assert.equal(summary.totalCount, 3)
  assert.equal(summary.scoredCount, 0, '0 / 负数 / NaN 均不是有效满分')
  assert.equal(summary.keyOnlyCount, 3)
  assert.equal(summary.totalScore, 0)
})

test('buildScoringFocusSummary 分值合计收敛浮点尾差', () => {
  const summary = buildScoringFocusSummary([
    makeReq({ id: 1, requirementKind: 'scoring_item', scoreWeight: 20.1 }),
    makeReq({ id: 2, requirementKind: 'scoring_item', scoreWeight: 0.2 }),
  ])

  assert.equal(summary.totalScore, 20.3, '禁止出现 20.299999999999997 这类尾差')
})

test('buildScoringFocusSummary 空列表返回全 0（横幅据此不渲染行动区）', () => {
  const summary = buildScoringFocusSummary([])
  assert.deepEqual(summary, {
    totalCount: 0,
    scoredCount: 0,
    keyOnlyCount: 0,
    totalScore: 0,
  })
})

// ===== getScoringFocusGroupLabels：聚焦时要展开哪些分组 =====

test('getScoringFocusGroupLabels 按分类优先级去重返回含评分项的分组标签', () => {
  const labels = getScoringFocusGroupLabels([
    makeReq({ id: 1, category: 'COMM', requirementKind: 'scoring_item', scoreWeight: 10 }),
    makeReq({ id: 2, category: 'TECH', requirementKind: 'scoring_item', scoreWeight: 10 }),
    makeReq({ id: 3, category: 'technical', isKeyReview: true }),
    makeReq({ id: 4, category: 'QUAL', requirementKind: 'normal' }),
  ])

  assert.deepEqual(
    labels,
    ['技术要求', '商务要求'],
    '按分类优先级排序、枚举名与小写 value 归一为同一标签，且不含无评分项的分类',
  )
})

test('getScoringFocusGroupLabels 无评分项时返回空数组', () => {
  assert.deepEqual(getScoringFocusGroupLabels([makeReq({ id: 1 })]), [])
})

// ===== 提示条行动区源码契约 =====

test('ScoringTotalsBanner 行动区提供聚焦评分项按钮且由 focusActionVisible 门禁驱动', () => {
  const focusBtnIdx = bannerSource.indexOf('聚焦评分项')
  assert.ok(focusBtnIdx !== -1, '"聚焦评分项"按钮文本必须存在')
  assert.match(
    bannerSource.slice(Math.max(0, focusBtnIdx - 600), focusBtnIdx),
    /focusActionVisible/,
    '"聚焦评分项"按钮必须由 focusActionVisible 门禁驱动',
  )
  assert.match(
    bannerSource,
    /focusActionVisible\s*=\s*[\s\S]{0,160}totalCount\s*>\s*0/,
    '无评分项时不得渲染聚焦按钮（否则点了是空列表）',
  )
})

test('ScoringTotalsBanner 行动区保留按钮但不再重复核对范围文案', () => {
  assert.doesNotMatch(
    bannerSource,
    /当前核对范围/,
    '核对范围与顶部评分项合计重复，行动区不再渲染该文案',
  )
  assert.doesNotMatch(
    bannerSource,
    /focusScopeText/,
    '删除核对范围后不应保留专用文案 helper',
  )
  assert.match(bannerSource, /聚焦评分项/, '聚焦按钮仍保留，便于用户定位评分项')
})

test('ScoringTotalsBanner 通过 props 接收聚合与聚焦回调（不自行计算业务口径）', () => {
  assert.match(bannerSource, /focusSummary\?:\s*ScoringFocusSummary \| null/)
  assert.match(bannerSource, /onFocusScoringItems\?:\s*\(\) => void/)
})

// ===== 筛选栏受控契约 =====

test('RequirementFilterBar 评分项筛选为纯受控 chip 且计数为 0 时不渲染', () => {
  assert.match(filterBarSource, /showScoringFocusOnly:\s*boolean/)
  assert.match(filterBarSource, /onToggleScoringFocusOnly:\s*\(\) => void/)
  assert.match(filterBarSource, /scoringFocusCount\s*>\s*0\s*&&/, '无评分项时不渲染该开关')
  assert.match(filterBarSource, /aria-pressed=\{showScoringFocusOnly\}/, '开关态必须可无障碍读取')
})

// ===== 页面编排契约 =====

test('RequirementReview 一键聚焦复用筛选状态并滚动到稳定元素 id', () => {
  assert.match(pageSource, /const focusScoringItems = useCallback/, '聚焦逻辑必须是页面级回调')
  assert.match(
    pageSource,
    /id="requirement-review-table-card"/,
    '滚动目标必须是稳定 id，禁止依赖 DOM 结构路径',
  )
  assert.match(
    pageSource,
    /getElementById\('requirement-review-table-card'\)\?\.scrollIntoView/,
    '聚焦后必须滚动定位到主列表卡片',
  )
  assert.match(
    pageSource,
    /setExpandedGroups\(new Set\(getScoringFocusGroupLabels\(/,
    '必须展开含评分项的分组，否则默认折叠下点完看不到条目',
  )
  assert.match(pageSource, /setShowScoringFocusOnly\(true\)/, '聚焦必须打开评分项筛选')
  assert.match(pageSource, /setShowMandatoryOnly\(false\)/, '聚焦必须清掉否决项叠加筛选')
})

test('RequirementReview 主列表按评分项筛选过滤且与分类筛选正交', () => {
  assert.match(
    pageSource,
    /if \(showScoringFocusOnly && !isScoringFocusItem\(r\)\) return false/,
    '筛选必须落在 filteredList 上，与否决项/分类条件并列',
  )
})

// 「全部」默认折叠时，chip 只开筛选不展开 → 用户以为筛空；
// 开启路径必须展开含评分项分组，且不得顺手清掉分类/否决项正交叠加。
test('RequirementReview 评分项/重点 chip 开启时展开分组且不重置分类否决项', () => {
  assert.doesNotMatch(
    pageSource,
    /onToggleScoringFocusOnly=\{\(\) => setShowScoringFocusOnly\(\(v\) => !v\)\}/,
    '禁止仅 toggle 布尔而不展开分组',
  )
  assert.match(
    pageSource,
    /onToggleScoringFocusOnly=\{toggleScoringFocusOnly\}/,
    'chip 必须接到具名 toggle，便于锁定开启副作用',
  )
  const toggleIdx = pageSource.indexOf('const toggleScoringFocusOnly')
  assert.ok(toggleIdx !== -1, '必须存在 toggleScoringFocusOnly 回调')
  const focusIdx = pageSource.indexOf('const focusScoringItems', toggleIdx)
  assert.ok(focusIdx > toggleIdx, 'toggle 必须定义在 focusScoringItems 之前，便于隔离断言')
  const toggleSlice = pageSource.slice(toggleIdx, focusIdx)
  assert.match(
    toggleSlice,
    /getScoringFocusGroupLabels|expandScoringFocusGroups/,
    '开启时必须展开含评分核对项的分组，与聚焦评分项可见效果对齐',
  )
  assert.match(toggleSlice, /setShowScoringFocusOnly\(true\)/, '开启分支必须显式打开筛选')
  assert.doesNotMatch(
    toggleSlice,
    /setActiveTab\(['"]all['"]\)/,
    'chip 不得清分类筛选（正交语义保留）',
  )
  assert.doesNotMatch(
    toggleSlice,
    /setShowMandatoryOnly\(false\)/,
    'chip 不得清否决项筛选（正交语义保留）',
  )
})

test('RequirementReview 补录入口仍复用既有评分项弹窗，未新造入口', () => {
  assert.match(pageSource, /onAddScoringItem=\{\(\) => openAddDialog\('scoring_item'\)\}/)
  assert.match(pageSource, /onFocusScoringItems=\{focusScoringItems\}/)
  assert.match(pageSource, /focusSummary=\{scoringFocusSummary\}/)
})
