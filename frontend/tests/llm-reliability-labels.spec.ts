import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import {
  formatAdviceActionLabel,
  formatAdviceRuleLabel,
  formatAdviceStatusLabel,
  formatCoveragePct,
  formatLlmOutcomeLabel,
  formatOpsWindowLabel,
  isFailureOutcome,
  LLM_OUTCOME_LABELS,
  LLM_PURPOSE_POOL_ORDER,
  purposePoolSortIndex,
} from '../src/pages/admin/ops/reliabilityLabels.ts'

const __dirname = dirname(fileURLToPath(import.meta.url))

test('formatLlmOutcomeLabel 映射已知 outcome，未知值原样返回', () => {
  assert.equal(formatLlmOutcomeLabel('auth_error'), '鉴权失败')
  assert.equal(formatLlmOutcomeLabel('valid'), '有效')
  assert.equal(formatLlmOutcomeLabel('something_new'), 'something_new')
  assert.ok(Object.keys(LLM_OUTCOME_LABELS).length >= 10)
})

test('isFailureOutcome 仅把 valid 视为非失败', () => {
  assert.equal(isFailureOutcome('valid'), false)
  assert.equal(isFailureOutcome('timeout'), true)
  assert.equal(isFailureOutcome('auth_error'), true)
})

test('formatCoveragePct 输出一位小数百分比', () => {
  assert.equal(formatCoveragePct(0.956), '95.6%')
  assert.equal(formatCoveragePct(1), '100.0%')
})

test('LlmReliabilityPanel 不得直接渲染裸 row.key 作为主文案', () => {
  const panelPath = join(
    __dirname,
    '../src/pages/admin/ops/components/LlmReliabilityPanel.tsx',
  )
  const source = readFileSync(panelPath, 'utf8')
  assert.doesNotMatch(
    source,
    />\{\s*row\.key\s*\}</,
    '面板不应再把 row.key 作为单元格主文案',
  )
  assert.match(source, /display_name/)
  assert.match(source, /ReliabilityFailureDrawer/)
})

test('opsApi 暴露 getLlmFailures', () => {
  const apiPath = join(__dirname, '../src/services/opsApi.ts')
  const source = readFileSync(apiPath, 'utf8')
  assert.match(source, /getLlmFailures/)
  assert.match(source, /\/admin\/ops\/llm\/failures/)
})

test('周建议规则/状态/动作与窗口标签有中文映射', () => {
  assert.equal(formatAdviceRuleLabel('no_candidates'), '无可用配置候选')
  assert.equal(formatAdviceRuleLabel('best_already_preferred'), '最优已是首选，保持')
  assert.equal(formatAdviceStatusLabel('pending'), '待确认')
  assert.equal(formatAdviceStatusLabel('rolled_back'), '已回滚')
  assert.equal(formatAdviceActionLabel('promote'), '提升为主用')
  assert.equal(formatOpsWindowLabel('24h'), '近24小时')
  assert.equal(formatAdviceRuleLabel('totally_unknown_rule'), '未知规则')
})

test('用途池排序与 Settings AI 池一致：视觉→提取→审查', () => {
  assert.deepEqual([...LLM_PURPOSE_POOL_ORDER], ['vision', 'extract', 'review'])
  const shuffled = ['review', 'extract', 'vision']
  const sorted = [...shuffled].sort(
    (a, b) => purposePoolSortIndex(a) - purposePoolSortIndex(b),
  )
  assert.deepEqual(sorted, ['vision', 'extract', 'review'])
})

test('WeeklyAdviceCard 不得把 rule_id/status/config_id 技术码当主文案', () => {
  const cardPath = join(
    __dirname,
    '../src/pages/admin/ops/components/WeeklyAdviceCard.tsx',
  )
  const source = readFileSync(cardPath, 'utf8')
  assert.doesNotMatch(source, /规则 \{item\.rule_id\}/)
  assert.doesNotMatch(source, /状态 \{item\.status\}/)
  assert.match(source, /formatAdviceRuleLabel|rule_label/)
  assert.match(source, /target_display|formatPurposeLabel/)
  assert.match(source, /boardPurposeItems/)
  assert.match(source, /boardNodeItems/)
  assert.match(source, /adviceBoardWeekLabel/)
})

test('ReliabilityFailureDrawer 样本区不得裸渲 node_key/config_id', () => {
  const drawerPath = join(
    __dirname,
    '../src/pages/admin/ops/components/ReliabilityFailureDrawer.tsx',
  )
  const source = readFileSync(drawerPath, 'utf8')
  assert.doesNotMatch(source, /节点: \{sample\.node_key/)
  assert.doesNotMatch(source, /配置: \{sample\.config_id/)
  assert.match(source, /node_display|config_display/)
  assert.doesNotMatch(
    source,
    /row\.node_key && row\.node_display !== row\.node_key/,
    '聚合表不应再把 node_key 作为可见副行',
  )
})
