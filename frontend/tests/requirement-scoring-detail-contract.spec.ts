import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const tableSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/RequirementTable.tsx', import.meta.url),
  'utf8',
)
const detailSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/ScoringItemDetail.tsx', import.meta.url),
  'utf8',
)

test('评分项徽标与满分修改入口必须共用同一展示谓词', () => {
  assert.match(tableSource, /const isScoringDisplayItem = /)
  assert.match(tableSource, /const hasEditableScoreWeight = /)
  assert.match(tableSource, /isScoringDisplayItem\(req\) && \(/)
  assert.match(tableSource, /editingId !== req\.id && hasEditableScoreWeight\(req\) && \(/)
  assert.doesNotMatch(
    tableSource,
    /editingId !== req\.id && req\.requirementKind === 'scoring_item' && req\.scoringMetadata/,
  )
})

test('满分修改入口不依赖 scoringMetadata 才渲染', () => {
  assert.doesNotMatch(detailSource, /!requirement\.scoringMetadata/)
  assert.match(detailSource, /!onUpdateScoringWeight/)
})

test('无有效满分的评分相关提示项不显示满分修改入口', () => {
  assert.match(tableSource, /Number\.isFinite\(scoreWeight\) && scoreWeight > 0/)
  assert.doesNotMatch(detailSource, /未设置/)
})
