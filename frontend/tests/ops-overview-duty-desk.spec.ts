import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  OPS_OVERVIEW_PANE_KEY,
  findLayerMetric,
  isLayerCardMuted,
  llmFinalValidKpi,
  readOverviewPane,
  summarizeFlowFunnel,
  unresolvedErrorKpi,
  workerKpi,
  writeOverviewPane,
} from '../src/pages/admin/ops/dutyLogic.ts'

test('readOverviewPane 缺省与非法值回 duty，lab 才切调试', () => {
  assert.equal(readOverviewPane(null), 'duty')
  assert.equal(readOverviewPane({ getItem: () => null }), 'duty')
  assert.equal(readOverviewPane({ getItem: () => 'nope' }), 'duty')
  assert.equal(readOverviewPane({ getItem: () => 'lab' }), 'lab')
})

test('writeOverviewPane 写入固定键，storage 不可用时不抛', () => {
  const store: Record<string, string> = {}
  writeOverviewPane('lab', { setItem: (k, v) => { store[k] = v } })
  assert.equal(store[OPS_OVERVIEW_PANE_KEY], 'lab')
  writeOverviewPane('duty', { setItem: () => { throw new Error('blocked') } })
})

test('summarizeFlowFunnel 无启动无失败为无流量，不得平稳', () => {
  const s = summarizeFlowFunnel([])
  assert.equal(s.label, '无流量')
  assert.equal(s.verdict, 'neutral')
  assert.equal(s.started, 0)
  assert.equal(s.failed, 0)
  assert.equal(s.successRatio, null)
})

test('summarizeFlowFunnel 失败 1 预警、>=3 恶化、有启动零失败平稳', () => {
  const warn = summarizeFlowFunnel([
    { task_type: 'extract', started: 4, ended: 4, success: 3, failure: 1, other: 0, success_ratio: 0.75, avg_duration_ms: 1 },
  ])
  assert.equal(warn.label, '预警')
  assert.equal(warn.verdict, 'warn')
  assert.equal(warn.failed, 1)
  assert.equal(warn.started, 4)

  const bad = summarizeFlowFunnel([
    { task_type: 'extract', started: 5, ended: 5, success: 2, failure: 3, other: 0, success_ratio: 0.4, avg_duration_ms: 1 },
  ])
  assert.equal(bad.label, '恶化')
  assert.equal(bad.verdict, 'critical')

  const ok = summarizeFlowFunnel([
    { task_type: 'extract', started: 2, ended: 2, success: 2, failure: 0, other: 0, success_ratio: 1, avg_duration_ms: 1 },
  ])
  assert.equal(ok.label, '平稳')
  assert.equal(ok.verdict, 'stable')
  assert.equal(ok.successRatio, 1)
})

test('unresolvedErrorKpi 无卡不展示，0 为平稳，其余跟 metric.verdict', () => {
  assert.equal(unresolvedErrorKpi(undefined).visible, false)
  const zero = unresolvedErrorKpi({ name: 'unresolved_error_total', value: 0, verdict: 'critical' })
  assert.equal(zero.visible, true)
  assert.equal(zero.label, '平稳')
  const hot = unresolvedErrorKpi({ name: 'unresolved_error_total', value: 188, verdict: 'critical' })
  assert.equal(hot.label, '恶化')
})

test('workerKpi 与 llmFinalValidKpi 口径', () => {
  assert.equal(workerKpi(undefined, true).label, '加载失败')
  assert.equal(workerKpi('healthy', false).label, '平稳')
  assert.equal(workerKpi('degraded', false).label, '预警')
  assert.equal(workerKpi('offline', false).label, '恶化')
  const empty = llmFinalValidKpi(0, 0)
  assert.equal(empty.label, '无样本')
  assert.notEqual(empty.label, '平稳')
  assert.equal(llmFinalValidKpi(10, 0.96).label, '平稳')
  assert.equal(llmFinalValidKpi(10, 0.94).label, '恶化')
  assert.equal(llmFinalValidKpi(0, 0, true).label, '加载失败')
  assert.notEqual(llmFinalValidKpi(0, 0, true).label, '无样本')
})

test('findLayerMetric 与 isLayerCardMuted', () => {
  const layers = {
    platform: [{ name: 'unresolved_error_total', value: 1, verdict: 'warn', reason: 'x' }],
    quality: [{ name: 'gate_verdict_total', value: 0, verdict: 'neutral', reason: '当前时间范围内暂无 Critic 样本' }],
  }
  assert.equal(findLayerMetric(layers, 'platform', 'unresolved_error_total')?.value, 1)
  assert.equal(findLayerMetric(layers, 'platform', 'missing'), undefined)
  assert.equal(isLayerCardMuted({ verdict: 'neutral', reason: 'ok' }), true)
  assert.equal(isLayerCardMuted({ verdict: 'stable', reason: '当前时间范围内暂无稳定性样本' }), true)
  assert.equal(isLayerCardMuted({ verdict: 'critical', reason: 'error=1/total=2' }), false)
})

test('LlmReliabilityPanel compact 不渲染分组与周建议', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/components/LlmReliabilityPanel.tsx', import.meta.url), 'utf8')
  assert.match(src, /isError/)
  assert.match(src, /加载失败/)
  assert.match(src, /compact\?: boolean/)
  const start = src.indexOf('if (compact)')
  assert.ok(start >= 0)
  const compactReturn = src.indexOf('return (', start + 1)
  const fullReturn = src.indexOf('return (', compactReturn + 1)
  const compactJsx = src.slice(start, fullReturn > compactReturn ? fullReturn : start + 900)
  assert.match(compactJsx, /最终有效率/)
  assert.doesNotMatch(compactJsx, /WeeklyAdviceCard/)
  assert.doesNotMatch(compactJsx, /LLM_GROUP_OPTIONS/)
  assert.doesNotMatch(compactJsx, /ReliabilityFailureDrawer/)
})

test('FlowHealthStrip 走 flow-funnel 且禁止 business_health', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/components/FlowHealthStrip.tsx', import.meta.url), 'utf8')
  assert.match(src, /getFlowFunnel/)
  assert.match(src, /summarizeFlowFunnel/)
  assert.match(src, /\/admin\/ops\/tender/)
  assert.match(src, /\/admin\/ops\/collusion/)
  assert.match(src, /\/admin\/ops\/writer/)
  assert.match(src, /加载失败/)
  assert.doesNotMatch(src, /business_health/)
  assert.doesNotMatch(src, /VectorJobState/)
})

test('DutyPane 只组装值班块，不含四维与假健康条', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/DutyPane.tsx', import.meta.url), 'utf8')
  assert.match(src, /FlowHealthStrip/)
  assert.match(src, /WorkerHealthPanel/)
  assert.match(src, /llmQuery\.isError/)
  assert.match(src, /compact/)
  assert.match(src, /unresolved_error_total/)
  assert.match(src, /task_failed_total/)
  assert.match(src, /\/admin\/error-logs/)
  assert.match(src, /project_avg_credits/)
  assert.match(src, /合计点数/)
  assert.match(src, /Token 入/)
  assert.match(src, />合计</)
  assert.match(src, /isBillingEnabled/)
  assert.doesNotMatch(src, /平均/)
  assert.doesNotMatch(src, /均花费/)
  assert.doesNotMatch(src, /pillar_summary/)
  assert.doesNotMatch(src, /business_health/)
  assert.doesNotMatch(src, /HotspotPanel/)
  assert.doesNotMatch(src, /CatalogPanel/)
  assert.doesNotMatch(src, /ScoringVerdictPanel/)
  assert.doesNotMatch(src, /dispatcher/)
  assert.doesNotMatch(src, /tokens_per_page/)
  assert.doesNotMatch(src, /Token \(入\+出\)/)
})

test('DutyPane 块顺序：KPI → 成本快照 → compact LLM → 链路 → Worker', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/DutyPane.tsx', import.meta.url), 'utf8')
  const gridAt = src.indexOf('grid grid-cols-4')
  const costAt = src.indexOf('成本快照')
  const llmAt = src.indexOf('<LlmReliabilityPanel')
  const flowAt = src.indexOf('<FlowHealthStrip')
  const workerAt = src.indexOf('<WorkerHealthPanel')
  assert.ok(
    gridAt >= 0 && costAt > gridAt && llmAt > costAt && flowAt > llmAt && workerAt > flowAt,
    `DutyPane JSX 顺序不符: grid=${gridAt} cost=${costAt} llm=${llmAt} flow=${flowAt} worker=${workerAt}`,
  )
})

test('WorkerHealthPanel 提供值班滚动锚点', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/components/WorkerHealthPanel.tsx', import.meta.url), 'utf8')
  assert.match(src, /id="ops-worker-health"/)
})

test('LabPane 顺序与降权', () => {
  const lab = readFileSync(new URL('../src/pages/admin/ops/LabPane.tsx', import.meta.url), 'utf8')
  const llmAt = lab.indexOf('<LlmReliabilityPanel')
  const adviceAt = lab.indexOf('<WeeklyAdviceCard')
  const wallAt = lab.indexOf('分层指标')
  const hotAt = lab.indexOf('<HotspotPanel')
  const catAt = lab.indexOf('<CatalogPanel')
  assert.ok(
    llmAt >= 0 && adviceAt > llmAt && wallAt > adviceAt && hotAt > wallAt && catAt > hotAt,
    `LabPane JSX 顺序不符: llm=${llmAt} advice=${adviceAt} wall=${wallAt} hot=${hotAt} cat=${catAt}`,
  )
  assert.match(lab, /isLayerCardMuted/)
  assert.match(lab, /muted/)
  assert.doesNotMatch(lab, /compact/)
  assert.doesNotMatch(lab, /ScoringVerdictPanel/)
})

test('评分链路裁决决策面不得回流', () => {
  const lab = readFileSync(new URL('../src/pages/admin/ops/LabPane.tsx', import.meta.url), 'utf8')
  const opsApi = readFileSync(new URL('../src/services/opsApi.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(lab, /ScoringVerdictPanel/)
  assert.doesNotMatch(opsApi, /getScoringVerdictStats/)
  assert.doesNotMatch(opsApi, /ScoringVerdictStats/)
  assert.doesNotMatch(opsApi, /scoring-verdict-stats/)
})

test('LlmReliabilityPanel 完整态不再内嵌周建议', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/components/LlmReliabilityPanel.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(src, /WeeklyAdviceCard/)
})

test('MetricCard 支持 muted 降权', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/components/MetricCard.tsx', import.meta.url), 'utf8')
  assert.match(src, /muted\?: boolean/)
  assert.match(src, /opacity-60/)
})

test('Overview 默认值班、按档挂载、不渲染已删除块', () => {
  const src = readFileSync(new URL('../src/pages/admin/ops/Overview.tsx', import.meta.url), 'utf8')
  assert.match(src, /readOverviewPane/)
  assert.match(src, /DutyPane/)
  assert.match(src, /LabPane/)
  assert.match(src, /pane === 'duty'/)
  assert.match(src, /pane === 'lab'/)
  assert.match(src, /值班/)
  assert.match(src, /指标调试/)
  assert.match(src, /timeWindow/)
  assert.match(src, /globalThis\.sessionStorage/)
  assert.doesNotMatch(src, /const \[window/)
  assert.doesNotMatch(src, /pillar_summary/)
  assert.doesNotMatch(src, /business_health/)
  assert.doesNotMatch(src, /ScoringVerdictPanel/)
  assert.doesNotMatch(src, /HotspotPanel/)
  assert.doesNotMatch(src, /CatalogPanel/)
  assert.doesNotMatch(src, /dispatcher/)
  const lines = src.split(/\r?\n/).length
  assert.ok(lines < 160, `Overview 壳过长: ${lines}`)
})

test('dutyVisual 状态条与数字着色', async () => {
  const visual = await import('../src/pages/admin/ops/dutyVisual.ts')
  assert.equal(visual.verdictBarClass('critical'), 'border-l-rose-500')
  assert.equal(visual.verdictBarClass('warn'), 'border-l-amber-500')
  assert.equal(visual.verdictBarClass('improving'), 'border-l-emerald-500')
  assert.equal(visual.verdictBarClass('stable'), 'border-l-slate-300')
  assert.equal(visual.verdictBarClass('neutral'), 'border-l-slate-300')
  assert.equal(visual.verdictLabelClass('warn'), 'text-amber-700')
  assert.equal(visual.verdictLabelClass('critical'), 'text-rose-700')
  assert.equal(visual.verdictValueClass('critical'), 'text-rose-700')
  assert.equal(visual.verdictValueClass('warn'), 'text-amber-700')
  assert.equal(visual.verdictValueClass('stable'), 'text-gray-900')
  assert.equal(visual.verdictValueClass('improving'), 'text-gray-900')
  assert.match(visual.FLOW_IDENTITY.tender.well, /bg-blue-50/)
  assert.match(visual.FLOW_IDENTITY.collusion.well, /bg-\[#F6F1FC\]/)
  assert.match(visual.FLOW_IDENTITY.writer.well, /bg-teal-50/)
})

test('值班卡用左边条，链路用身份井', () => {
  const duty = readFileSync(new URL('../src/pages/admin/ops/DutyPane.tsx', import.meta.url), 'utf8')
  const flow = readFileSync(new URL('../src/pages/admin/ops/components/FlowHealthStrip.tsx', import.meta.url), 'utf8')
  const lab = readFileSync(new URL('../src/pages/admin/ops/LabPane.tsx', import.meta.url), 'utf8')
  const overview = readFileSync(new URL('../src/pages/admin/ops/Overview.tsx', import.meta.url), 'utf8')
  const compact = readFileSync(new URL('../src/pages/admin/ops/components/LlmReliabilityPanel.tsx', import.meta.url), 'utf8')
  assert.match(duty, /KPI_FRAME/)
  assert.match(duty, /verdictBarClass/)
  assert.match(duty, /Coins/)
  assert.doesNotMatch(duty, /from-indigo-50/)
  assert.match(flow, /FLOW_IDENTITY/)
  assert.match(flow, /FileSearch/)
  assert.match(flow, /ShieldAlert/)
  assert.match(flow, /PenLine/)
  assert.doesNotMatch(flow, /border-l-4/)
  assert.match(lab, /分层指标/)
  assert.match(overview, /bg-white px-4 py-3/)
  assert.match(compact, /verdictBarClass/)
})
