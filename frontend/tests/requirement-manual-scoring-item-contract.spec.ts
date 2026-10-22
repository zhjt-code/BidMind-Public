import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const addDialogSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/RequirementAddDialog.tsx', import.meta.url),
  'utf8',
)
const requirementApiSource = readFileSync(
  new URL('../src/services/requirementApi.ts', import.meta.url),
  'utf8',
)
const requirementReviewSource = readFileSync(
  new URL('../src/pages/RequirementReview/index.tsx', import.meta.url),
  'utf8',
)

test('添加弹窗提供普通要求和评分项双模式', () => {
  assert.match(addDialogSource, /mode:\s*'requirement'\s*\|\s*'scoring_item'/)
  assert.match(addDialogSource, /普通要求/)
  assert.match(addDialogSource, /评分项/)
})

test('评分项补录表单包含结构化评分字段', () => {
  for (const label of [
    '评分项名称',
    '满分分值',
    '评分标准',
    '扣分/不得分规则',
    '所属卷',
    '卷原文标签',
    '采购包/标段',
    '来源页码',
  ]) {
    assert.match(addDialogSource, new RegExp(label))
  }
})

test('评分项补录表单区分必填核心字段和选填信息', () => {
  assert.match(addDialogSource, /必填/)
  assert.match(addDialogSource, /选填信息/)
  assert.match(addDialogSource, /<details/)
  assert.match(addDialogSource, /评分项名称（必填）/)
  assert.match(addDialogSource, /满分分值（必填）/)
  assert.match(addDialogSource, /评分标准描述（必填）/)
})

test('requirementService 暴露手动评分项专用 API', () => {
  assert.match(requirementApiSource, /createScoringItem/)
  assert.match(requirementApiSource, /\/projects\/\$\{projectId\}\/requirements\/scoring-items/)
})

test('确认页提交评分项后刷新评分项核计报告', () => {
  assert.match(requirementReviewSource, /createScoringItem/)
  assert.match(requirementReviewSource, /refreshScoringTotalsReport/)
  assert.match(requirementReviewSource, /setScoringTotalsReport/)
})
