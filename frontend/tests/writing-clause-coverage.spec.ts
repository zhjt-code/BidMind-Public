import assert from 'node:assert/strict'
import test from 'node:test'

import {
  COVERAGE_HIDDEN_WRITING_CLAUSE_SUB_TYPES,
  isCoverageCountedClause,
} from '../src/pages/writing/writingClauseTabs.ts'
import type { WritingClause } from '../src/services/writingClauseTransforms.ts'

function clause(subType: string): WritingClause {
  return {
    id: 1,
    projectId: 1,
    documentId: 1,
    layer: 'hard_constraint',
    subType,
    content: '示例约束',
    rawText: null,
    sourcePage: null,
    sourcePageStart: null,
    sourcePageEnd: null,
    scoreWeight: null,
    confirmed: 1,
    isManual: 0,
    sortOrder: 0,
  }
}

// 覆盖分母排除集与后端 material_matcher._EXCLUDED_SUB_TYPES 一致(6 类)。
test('覆盖分母排除 6 类非正文消费 sub_type', () => {
  const excluded = [
    'bid_structure_analysis',
    'bid_directory_spec',
    'render_profile',
    'industry_profile',
    'submission_notice',
    'submission_rule',
  ]
  for (const subType of excluded) {
    assert.equal(isCoverageCountedClause(clause(subType)), false, subType)
  }
})

test('正文来源类型计入覆盖分母', () => {
  const counted = [
    'scoring_criterion',
    'content_required',
    'material_required',
    'disqualification',
    'emphasis_point',
  ]
  for (const subType of counted) {
    assert.equal(isCoverageCountedClause(clause(subType)), true, subType)
  }
})

test('排除集恰为 6 类, 防口径漂移', () => {
  assert.equal(COVERAGE_HIDDEN_WRITING_CLAUSE_SUB_TYPES.size, 6)
})
