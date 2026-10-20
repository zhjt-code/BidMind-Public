import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const bannerSource = readFileSync(
  new URL('../src/pages/writing/WritingScoringTotalsBanner.tsx', import.meta.url),
  'utf8',
)
const pageSource = readFileSync(
  new URL('../src/pages/writing/WritingClauses.tsx', import.meta.url),
  'utf8',
)

test('写标评分横幅在无权威来源时提示评分项来自 AI 自由提取', () => {
  assert.match(bannerSource, /authorityCount\s*===\s*0/)
  assert.match(bannerSource, /当前评分项来自 AI 自由提取/)
  assert.match(bannerSource, /分值未经评分表校验/)
})

test('写标评分横幅保留新增评分项入口但不提供聚焦评分项按钮', () => {
  assert.match(bannerSource, /onAddScoringItem\?:\s*\(\) => void/)
  assert.match(bannerSource, /addScoringItemVisible/)
  assert.match(bannerSource, /shouldShowAddScoringItem/)
  assert.match(bannerSource, /ScoringBannerChrome/)
  assert.match(bannerSource, /新增评分项/)
  assert.match(bannerSource, /手动新增一条评分项/)
  assert.doesNotMatch(bannerSource, /补充评分项/)
  assert.match(bannerSource, /attentionVisible=\{!isOk\}/)
  assert.doesNotMatch(bannerSource, /聚焦评分项/)
})

test('写标评分横幅复核文案说明分值异常而非仅说缺漏', () => {
  assert.match(bannerSource, /评分项分值需核对，请确认识别结果/)
  assert.match(bannerSource, /分值异常会影响后续章节响应重点/)
  assert.doesNotMatch(bannerSource, /缺漏会导致后续章节响应不全而失分。/)
})

test('写标评分横幅在通过态也展示疑似履约考核建议排除提示', () => {
  assert.match(bannerSource, /suspectedAssessmentCount/)
  assert.match(bannerSource, /疑似履约考核，建议排除/)
})

test('写标评分横幅补录入口复用现有评分标准新增逻辑', () => {
  assert.match(
    pageSource,
    /onAddScoringItem=\{\(\) => openAddDialog\('generation_guide', 'scoring_criterion'\)\}/,
    '补录入口必须复用写作引导下评分标准的新增约束逻辑',
  )
})
