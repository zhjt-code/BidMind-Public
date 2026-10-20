/**
 * 防御测试：主列表"降为参考条款"契约（P2.3）。
 *
 * 锁定不变量（源码契约，参照 writing-clause-contract.spec.ts 模式）：
 * 红线1: RequirementTable 必须提供"降为参考"操作且对评分项/重点评审项隐藏（评分项铁律）；
 * 红线2: useRequirementList.demoteToReference 必须对评分项/重点评审项 no-op；
 * 红线3: 降级必须整组 fan-out（防 子面残留把条款拉回主列表）；
 * 红线4: 折叠区必须按 relevance_reason 渲染归因徽标（含人工降级）。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const read = (rel: string): string =>
  readFileSync(join(import.meta.dirname, rel), 'utf-8')

test('RequirementTable 提供降级操作且评分项不显示（评分项铁律）', () => {
  const source = read('../src/pages/RequirementReview/components/RequirementTable.tsx')
  assert.ok(source.includes('降为参考'), '必须渲染"降为参考"操作')
  assert.ok(source.includes('onDemote'), '必须通过 onDemote 回调触发降级')
  const guardPattern = /requirementKind !== 'scoring_item' && !req\.isKeyReview/
  assert.match(source, guardPattern, '降级按钮必须对评分项/重点评审项隐藏')
})

test('降为参考按钮使用 indigo 色（不与编辑蓝/删除红/分类色板冲突）', () => {
  const source = read('../src/pages/RequirementReview/components/RequirementTable.tsx')
  assert.ok(
    source.includes('text-indigo-600'),
    '降为参考必须使用 indigo 彩色（防回退为难以辨识的灰色）',
  )
})

test('否决项状态与切换操作分离（可发现性）', () => {
  const source = read('../src/pages/RequirementReview/components/RequirementTable.tsx')
  // 状态徽标为纯展示 span, 不再充当隐形切换按钮
  assert.doesNotMatch(
    source,
    /<button[^>]*onToggleMandatory[^>]*>\s*\{req\.isMandatory \? '是' : '否'\}/s,
    '状态徽标不得直接作为切换按钮（隐形交互不可发现）',
  )
  assert.ok(source.includes('改为否'), '否决项必须提供显式"改为否"动作')
  assert.ok(source.includes('改为是'), '非否决项必须提供显式"改为是"动作')
  assert.ok(
    source.includes('onToggleMandatory(req.id)'),
    '切换仍必须复用 onToggleMandatory 既有链路（整组 fan-out 语义不变）',
  )
})

test('useRequirementList.demoteToReference 评分项 no-op + 整组 fan-out', () => {
  const source = read('../src/pages/RequirementReview/hooks/useRequirementList.ts')
  assert.ok(source.includes('demoteToReference'), 'hook 必须暴露 demoteToReference')
  assert.ok(
    source.includes("req.requirementKind === 'scoring_item' || req.isKeyReview"),
    '降级必须先做评分项铁律守卫',
  )
  const demoteSection = source.slice(source.indexOf('const demoteToReference'))
  assert.ok(
    demoteSection.includes('req.memberIds.length > 0 ? req.memberIds : [id]'),
    '降级必须整组 fan-out（同理）',
  )
  assert.ok(
    demoteSection.includes("review_relevance: 'informational'"),
    '降级目标级别为 informational（参考条款）',
  )
})

test('RequirementReview 主页面已接线 onDemote', () => {
  const source = read('../src/pages/RequirementReview/index.tsx')
  assert.ok(source.includes('demoteToReference'), '主页面必须消费 demoteToReference')
  assert.ok(source.includes('onDemote={demoteToReference}'), '必须传给 RequirementTable')
})

test('折叠条款行按 relevance_reason 渲染归因徽标', () => {
  const source = read(
    '../src/pages/RequirementReview/components/FoldedRequirementRow.tsx',
  )
  assert.ok(source.includes('REASON_LABEL'), '必须存在归因文案映射')
  for (const label of ['AI判定', '规则命中', '合同区段', '行为性否决', '人工降级']) {
    assert.ok(source.includes(label), `归因映射必须覆盖: ${label}`)
  }
  assert.ok(source.includes('req.relevanceReason'), '必须消费 relevanceReason 字段')
})

test('结果页对参考条款明细行渲染不计分徽标', () => {
  const resultSource = read('../src/pages/Result.tsx')
  assert.ok(resultSource.includes('isNonScoredReference'), '必须消费不计分判定 helper')
  assert.ok(resultSource.includes('参考条款·不计分'), '必须渲染不计分徽标')

  const typesSource = read('../src/pages/result/types.ts')
  const helperSection = typesSource.slice(typesSource.indexOf('isNonScoredReference'))
  assert.ok(
    helperSection.includes("requirementKind === 'scoring_item' || result.isKeyReview"),
    '不计分判定必须先豁免评分项（评分项铁律）',
  )
})
