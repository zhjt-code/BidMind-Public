import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildClauseDisplayGroups,
  collectGroupUnconfirmedIds,
  countGroupConfirmed,
  isGroupCollapsedByDefault,
  isSubstantiveClause,
  isUnscoredScoringClause,
  SUBSTANTIVE_GROUP_KEY,
  SCORING_UNSCORED_GROUP_KEY,
  SCORING_UNSCORED_GROUP_TITLE,
  SCORING_ASSESSMENT_GROUP_KEY,
  SCORING_ASSESSMENT_GROUP_TITLE,
  WRITING_REFERENCE_GROUP_KEY,
  WRITING_REFERENCE_GROUP_TITLE,
  WRITING_CHECK_ONLY_GROUP_KEY,
  WRITING_CHECK_ONLY_GROUP_TITLE,
} from '../src/pages/writing/writingClauseGrouping.ts'
import type { WritingClause } from '../src/services/writingClauseTransforms.ts'

function clause(params: Partial<WritingClause>): WritingClause {
  return {
    id: params.id ?? 1,
    projectId: 1,
    documentId: 1,
    layer: params.layer ?? 'hard_constraint',
    subType: params.subType ?? 'disqualification',
    content: params.content ?? '示例约束',
    rawText: params.rawText ?? null,
    sourcePage: null,
    sourcePageStart: null,
    sourcePageEnd: null,
    scoreWeight: params.scoreWeight ?? null,
    confirmed: params.confirmed ?? 0,
    isManual: 0,
    sortOrder: params.sortOrder ?? 0,
    metadataJson: params.metadataJson ?? null,
  }
}

test('实质性判定: rawText/content 含★▲☆ 且限 disqualification', () => {
  assert.equal(isSubstantiveClause(clause({ rawText: '★投标人须具备一级资质' })), true)
  assert.equal(isSubstantiveClause(clause({ content: '▲报价不得超过最高限价' })), true)
  assert.equal(isSubstantiveClause(clause({ content: '☆须提供检测报告' })), true)
  assert.equal(isSubstantiveClause(clause({ content: '一般废标条款，无标记' })), false)
  // 子类型隔离: 非 disqualification 含★也不算实质性条款
  assert.equal(isSubstantiveClause(clause({ subType: 'material_required', content: '★材料' })), false)
})

test('分组切分: ★组置顶且仅含★, 一般废标次之, 其余按顺序', () => {
  const clauses = [
    clause({ id: 1, subType: 'fixed_template', content: '投标函模板' }),
    clause({ id: 2, subType: 'disqualification', content: '一般废标B' }),
    clause({ id: 3, subType: 'disqualification', content: '★实质性A', rawText: '★实质性A' }),
  ]
  const groups = buildClauseDisplayGroups(clauses)

  assert.equal(groups[0].key, SUBSTANTIVE_GROUP_KEY)
  assert.equal(groups[0].subType, 'disqualification')
  assert.deepEqual(groups[0].clauses.map((c) => c.id), [3])

  assert.equal(groups[1].key, 'disqualification')
  assert.deepEqual(groups[1].clauses.map((c) => c.id), [2])

  assert.equal(groups[2].key, 'fixed_template')
  assert.deepEqual(groups[2].clauses.map((c) => c.id), [1])
})

test('无★文件优雅降级: 不产出空的★组', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, subType: 'disqualification', content: '一般废标' }),
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, 'disqualification')
  assert.equal(groups.some((g) => g.key === SUBSTANTIVE_GROUP_KEY), false)
})

test('折叠默认: 低价值写作参考组折叠, 废标/评分/★组展开', () => {
  assert.equal(isGroupCollapsedByDefault('emphasis_point'), true)
  assert.equal(isGroupCollapsedByDefault('differentiation_hint'), true)
  assert.equal(isGroupCollapsedByDefault('project_context'), true)
  assert.equal(isGroupCollapsedByDefault('scoring_criterion'), false)
  assert.equal(isGroupCollapsedByDefault('disqualification'), false)
  assert.equal(isGroupCollapsedByDefault(SUBSTANTIVE_GROUP_KEY), false)

  const emphasisGroup = buildClauseDisplayGroups([
    clause({ layer: 'generation_guide', subType: 'emphasis_point', content: '重点' }),
  ])[0]
  assert.equal(emphasisGroup.collapsedByDefault, true)
})

test('组级 id 收集与计数: 仅未确认, 全确认返回空', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, subType: 'scoring_criterion', confirmed: 1 }),
    clause({ id: 2, subType: 'scoring_criterion', confirmed: 0 }),
    clause({ id: 3, subType: 'scoring_criterion', confirmed: 0 }),
  ])
  const group = groups[0]
  assert.deepEqual(collectGroupUnconfirmedIds(group), [2, 3])
  assert.equal(countGroupConfirmed(group), 1)

  const allConfirmed = buildClauseDisplayGroups([
    clause({ id: 5, subType: 'scoring_criterion', confirmed: 1 }),
  ])[0]
  assert.deepEqual(collectGroupUnconfirmedIds(allConfirmed), [])
  assert.equal(countGroupConfirmed(allConfirmed), 1)
})

test('无分值评分判定: 仅 scoring_criterion 且 score_weight 非正数', () => {
  assert.equal(
    isUnscoredScoringClause(clause({ subType: 'scoring_criterion', scoreWeight: null })),
    true,
  )
  assert.equal(
    isUnscoredScoringClause(clause({ subType: 'scoring_criterion', scoreWeight: 0 })),
    true,
  )
  assert.equal(
    isUnscoredScoringClause(clause({ subType: 'scoring_criterion', scoreWeight: 10 })),
    false,
  )
  // 子类型隔离: 非 scoring_criterion 无分值也不算
  assert.equal(
    isUnscoredScoringClause(clause({ subType: 'content_required', scoreWeight: null })),
    false,
  )
})

test('评分标准拆分: 有分值入"评分标准", 无分值入折叠"评分说明"参考组', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, layer: 'generation_guide', subType: 'scoring_criterion', content: '技术方案10分', scoreWeight: 10 }),
    clause({ id: 2, layer: 'generation_guide', subType: 'scoring_criterion', content: '评标办法综合评分法总述', scoreWeight: null }),
    clause({ id: 3, layer: 'generation_guide', subType: 'scoring_criterion', content: '保证金要求', scoreWeight: 0 }),
  ])
  const scored = groups.find((g) => g.key === 'scoring_criterion')
  const unscored = groups.find((g) => g.key === SCORING_UNSCORED_GROUP_KEY)
  assert.ok(scored, '应有有分值评分标准组')
  assert.equal(scored?.clauses.length, 1)
  assert.equal(scored?.clauses[0].id, 1)
  assert.ok(unscored, '应有无分值评分说明组')
  assert.equal(unscored?.clauses.length, 2)
  assert.equal(unscored?.subType, 'scoring_criterion') // 归属不变(编辑/新增仍为评分标准)
  assert.equal(unscored?.titleOverride, SCORING_UNSCORED_GROUP_TITLE)
  assert.equal(unscored?.collapsedByDefault, true) // 参考组默认折叠
  // 展示顺序: 有分值评分标准在无分值参考组之前
  const scoredIdx = groups.findIndex((g) => g.key === 'scoring_criterion')
  const unscoredIdx = groups.findIndex((g) => g.key === SCORING_UNSCORED_GROUP_KEY)
  assert.ok(scoredIdx < unscoredIdx)
})

test('未知 sub_type 兜底: 不丢条目', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, subType: 'unknown_future_type', content: '未来类型' }),
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, 'unknown_future_type')
  assert.deepEqual(groups[0].clauses.map((c) => c.id), [1])
})

test('AI判定参考约束单独折叠置底且保留原始subType', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, subType: 'content_required', content: '正文必写项' }),
    clause({
      id: 2,
      layer: 'generation_guide',
      subType: 'emphasis_point',
      content: '参考提示',
      metadataJson: {
        writing_relevance: 'reference',
        writing_relevance_source: 'matcher',
      },
    }),
  ])
  const reference = groups.find((g) => g.key === WRITING_REFERENCE_GROUP_KEY)
  assert.ok(reference, '应产出参考内容折叠组')
  assert.equal(reference?.titleOverride, WRITING_REFERENCE_GROUP_TITLE)
  assert.equal(reference?.subType, 'emphasis_point')
  assert.equal(reference?.collapsedByDefault, true)
  assert.deepEqual(reference?.clauses.map((c) => c.id), [2])
  assert.equal(groups[groups.length - 1].key, WRITING_REFERENCE_GROUP_KEY)
})

test('检查型红线单独折叠展示且不混入一般废标组', () => {
  const groups = buildClauseDisplayGroups([
    clause({ id: 1, subType: 'disqualification', content: '一般废标' }),
    clause({
      id: 2,
      subType: 'disqualification',
      content: '未逐页签章',
      metadataJson: {
        writing_relevance: 'check_only',
        writing_relevance_source: 'extract_pass',
      },
    }),
  ])
  const disq = groups.find((g) => g.key === 'disqualification')
  const checkOnly = groups.find((g) => g.key === WRITING_CHECK_ONLY_GROUP_KEY)
  assert.deepEqual(disq?.clauses.map((c) => c.id), [1])
  assert.ok(checkOnly, '应产出检查型红线组')
  assert.equal(checkOnly?.titleOverride, WRITING_CHECK_ONLY_GROUP_TITLE)
  assert.deepEqual(checkOnly?.clauses.map((c) => c.id), [2])
})

test('疑似履约考核评分项单独成组且不默认折叠', () => {
  const groups = buildClauseDisplayGroups([
    clause({
      id: 1,
      layer: 'generation_guide',
      subType: 'scoring_criterion',
      content: '类似业绩15分',
      scoreWeight: 15,
    }),
    clause({
      id: 2,
      layer: 'generation_guide',
      subType: 'scoring_criterion',
      content: '健康证缺一人扣5分',
      scoreWeight: 25,
      metadataJson: { suspected_assessment_form: true },
    }),
  ])
  const scored = groups.find((g) => g.key === 'scoring_criterion')
  const assessment = groups.find((g) => g.key === SCORING_ASSESSMENT_GROUP_KEY)
  assert.equal(scored?.clauses.length, 1)
  assert.equal(scored?.clauses[0].id, 1)
  assert.ok(assessment, '应产出疑似履约考核组')
  assert.equal(assessment?.titleOverride, SCORING_ASSESSMENT_GROUP_TITLE)
  assert.equal(assessment?.titleOverride, '疑似履约考核，建议排除')
  assert.equal(assessment?.subType, 'scoring_criterion')
  assert.equal(assessment?.clauses.length, 1)
  assert.equal(assessment?.clauses[0].id, 2)
  assert.equal(isGroupCollapsedByDefault(SCORING_ASSESSMENT_GROUP_KEY), false)
  const scoredIdx = groups.findIndex((g) => g.key === 'scoring_criterion')
  const assessmentIdx = groups.findIndex((g) => g.key === SCORING_ASSESSMENT_GROUP_KEY)
  assert.ok(scoredIdx < assessmentIdx)
})
