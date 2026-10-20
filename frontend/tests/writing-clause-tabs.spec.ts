import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getClauseCreateTarget,
  getClauseTabDisplayLabel,
  getClauseTabCount,
  getVisibleClausesForTab,
  isCoverageCountedClause,
  shouldShowClauseGroupCount,
  shouldShowClauseGroupHeading,
} from '../src/pages/writing/writingClauseTabs.ts'
import type { WritingClause } from '../src/services/writingClauseTransforms.ts'

function clause(params: Partial<WritingClause>): WritingClause {
  return {
    id: params.id ?? 1,
    projectId: 1,
    documentId: 1,
    layer: params.layer ?? 'generation_guide',
    subType: params.subType ?? 'project_context',
    content: params.content ?? '示例',
    rawText: null,
    sourcePage: null,
    sourcePageStart: null,
    sourcePageEnd: null,
    scoreWeight: null,
    confirmed: 0,
    isManual: 0,
    sortOrder: 0,
    metadataJson: params.metadataJson ?? null,
  }
}

test('行业画像应从写作引导中拆出为独立 tab', () => {
  const clauses = [
    clause({ id: 1, subType: 'project_context' }),
    clause({ id: 2, subType: 'industry_profile' }),
    clause({ id: 3, layer: 'structure_constraint', subType: 'content_required' }),
  ]

  assert.equal(getClauseTabCount(clauses, 'generation_guide'), 1)
  assert.equal(getClauseTabCount(clauses, 'industry_profile'), 1)
  assert.deepEqual(
    getVisibleClausesForTab(clauses, 'generation_guide').map((item) => item.subType),
    ['project_context'],
  )
  assert.deepEqual(
    getVisibleClausesForTab(clauses, 'industry_profile').map((item) => item.subType),
    ['industry_profile'],
  )
  assert.equal(
    getClauseTabDisplayLabel(clauses, { value: 'generation_guide', label: '写作引导' }),
    '写作引导 (1)',
  )
  assert.equal(
    getClauseTabDisplayLabel(clauses, { value: 'industry_profile', label: '行业画像' }),
    '行业画像',
  )
  assert.equal(shouldShowClauseGroupCount('industry_profile'), false)
  assert.equal(shouldShowClauseGroupCount('project_context'), true)
  assert.equal(shouldShowClauseGroupHeading('industry_profile'), false)
  assert.equal(shouldShowClauseGroupHeading('project_context'), true)
})

test('行业画像 tab 新增时仍写入 generation_guide 层级', () => {
  assert.deepEqual(
    getClauseCreateTarget('industry_profile'),
    { layer: 'generation_guide', subType: 'industry_profile' },
  )
})

test('参考约束不计入正文覆盖统计分母', () => {
  assert.equal(isCoverageCountedClause(clause({
    subType: 'emphasis_point',
    metadataJson: {
      writing_relevance: 'reference',
      writing_relevance_source: 'matcher',
    },
  })), false)
  assert.equal(isCoverageCountedClause(clause({ subType: 'content_required' })), true)
})
