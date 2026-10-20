import assert from 'node:assert/strict'
import test from 'node:test'

import { transformWritingClause } from '../src/services/writingClauseTransforms.ts'
import { getScoringRef, getScoringRefLabel } from '../src/pages/writing/writingScoringLink.ts'

function clauseWith(metadata: Record<string, unknown> | null) {
  return transformWritingClause({
    id: 2,
    project_id: 7,
    document_id: 31,
    layer: 'structure_constraint',
    sub_type: 'content_required',
    content: '技术方案完整性',
    raw_text: null,
    source_page: null,
    score_weight: null,
    confirmed: 0,
    is_manual: 0,
    sort_order: 1,
    metadata_json: metadata,
  })
}

test('getScoringRefLabel 在含分值时输出"已关联评分项：名称（N分）"', () => {
  const clause = clauseWith({
    scoring_ref: { clause_id: 1, score_item_name: '技术方案完整性', max_score: 10 },
  })
  assert.equal(getScoringRefLabel(clause), '已关联评分项：技术方案完整性（10分）')
  const ref = getScoringRef(clause)
  assert.equal(ref?.clauseId, 1)
  assert.equal(ref?.maxScore, 10)
})

test('getScoringRefLabel 在无分值时省略分值括号', () => {
  const clause = clauseWith({
    scoring_ref: { clause_id: 5, score_item_name: '资格性审查', max_score: null },
  })
  assert.equal(getScoringRefLabel(clause), '已关联评分项：资格性审查')
})

test('getScoringRefLabel 在无关联时返回 null', () => {
  assert.equal(getScoringRefLabel(clauseWith(null)), null)
  assert.equal(getScoringRefLabel(clauseWith({ other: 1 })), null)
  assert.equal(getScoringRefLabel(clauseWith({ scoring_ref: { clause_id: 1 } })), null)
})
