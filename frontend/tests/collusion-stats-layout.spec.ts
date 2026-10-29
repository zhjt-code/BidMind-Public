import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  COLLUSION_STATS_SCROLL_BODY_CLASS,
  buildRiskChartTitle,
  buildSingleGroupRiskGaugeData,
  resolveRiskGaugeLevel,
  shouldUseRiskLevelGauge,
  shouldUseSingleGroupRiskGauge,
} from '../src/pages/collusionResult/collusionStatsLayout.ts'

const collusionStatsSource = readFileSync(
  new URL('../src/pages/collusionResult/CollusionStats.tsx', import.meta.url),
  'utf8',
)

test('串标统计卡片展开内容区应启用独立纵向滚动', () => {
  assert.equal(COLLUSION_STATS_SCROLL_BODY_CLASS.includes('overflow-y-auto'), true)
  assert.equal(COLLUSION_STATS_SCROLL_BODY_CLASS.includes('max-h-[45vh]'), true)
  assert.equal(COLLUSION_STATS_SCROLL_BODY_CLASS.includes('overscroll-contain'), true)
})

test('单一群组风险应用五段均分风险圆环', () => {
  assert.equal(shouldUseSingleGroupRiskGauge('groups', 1), true)
  assert.equal(shouldUseSingleGroupRiskGauge('groups', 2), false)
  assert.equal(shouldUseSingleGroupRiskGauge('pairs', 1), false)
  assert.equal(shouldUseSingleGroupRiskGauge('pairs', 0), false)

  const segments = buildSingleGroupRiskGaugeData('high')
  assert.equal(segments.length, 5)
  assert.equal(segments.every(segment => segment.value === 1), true)
  assert.equal(segments.find(segment => segment.key === 'high')?.active, true)
  assert.equal(segments.filter(segment => segment.active).length, 1)
})

test('分布仅一种风险等级时改用五段圆环，避免单色退化饼图', () => {
  assert.equal(shouldUseRiskLevelGauge({ groupCount: 0, distinctRiskCount: 1 }), true)
  assert.equal(shouldUseRiskLevelGauge({ groupCount: 0, distinctRiskCount: 0 }), true)
  assert.equal(shouldUseRiskLevelGauge({ groupCount: 1, distinctRiskCount: 3 }), true)
  assert.equal(shouldUseRiskLevelGauge({ groupCount: 2, distinctRiskCount: 2 }), false)

  assert.equal(buildRiskChartTitle({
    useGauge: true, groupCount: 1, riskDistributionBasis: 'groups',
  }), '群组风险判定')
  assert.equal(buildRiskChartTitle({
    useGauge: true, groupCount: 0, riskDistributionBasis: 'pairs',
  }), '项目风险判定')
  assert.equal(buildRiskChartTitle({
    useGauge: false, groupCount: 0, riskDistributionBasis: 'pairs',
  }), '文件对风险分布')

  assert.equal(resolveRiskGaugeLevel({
    groupCount: 0,
    groupRiskLevel: undefined,
    singleDistributionRiskKey: 'low',
    projectRiskLevel: 'none',
  }), 'low')
  assert.equal(resolveRiskGaugeLevel({
    groupCount: 1,
    groupRiskLevel: 'medium',
    singleDistributionRiskKey: 'low',
    projectRiskLevel: 'none',
  }), 'medium')
})

test('风险圆环组件应居中对齐且图例覆盖五档', () => {
  assert.match(collusionStatsSource, /const RiskLevelGauge/)
  assert.match(collusionStatsSource, /const activeGaugeSegment = gaugeData\.find\(entry => entry\.active\)/)
  assert.match(collusionStatsSource, /cx="50%" cy="50%"/)
  assert.match(collusionStatsSource, /<text[\s\S]*?x="50%"[\s\S]*?y="50%"/)
  assert.match(collusionStatsSource, /fill=\{activeGaugeSegment\?\.color/)
  assert.match(collusionStatsSource, /activeGaugeSegment\?\.name/)
  assert.match(collusionStatsSource, /gaugeData\.map\(entry => \(/)
  assert.match(collusionStatsSource, /backgroundColor: entry\.color/)
  assert.match(collusionStatsSource, /shouldUseRiskLevelGauge/)
  assert.doesNotMatch(collusionStatsSource, /第 \{group\.groupIndex\} 组/)
  assert.doesNotMatch(collusionStatsSource, /y="41%"/)
  assert.doesNotMatch(collusionStatsSource, /y="52%"/)
})

test('热力图色标应相对矩阵格子区域居中', () => {
  assert.match(collusionStatsSource, /style=\{\{ width: svgW \}\}/)
  assert.match(collusionStatsSource, /style=\{\{ marginLeft: gridX, width: gridSize \}\}/)
})

test('维度综合得分雷达图网格应使用五条淡彩虹分值环并保留放射线', () => {
  assert.match(collusionStatsSource, /const RADAR_GRID_RINGS = \[/)
  assert.match(collusionStatsSource, /const RADAR_RADIAL_LINE_COLOR = 'hsl\(25, 95%, 86%\)'/)
  assert.match(collusionStatsSource, /score: 20/)
  assert.match(collusionStatsSource, /score: 100/)
  assert.match(collusionStatsSource, /hsl\(210, 90%, 80%\)/)
  assert.match(collusionStatsSource, /hsl\(0, 85%, 78%\)/)
  assert.match(collusionStatsSource, /radialLines=\{true\}/)
  assert.match(collusionStatsSource, /polarRadius=\{\[\]\}/)
  assert.match(collusionStatsSource, /radialLines=\{false\}/)
  assert.match(collusionStatsSource, /polarRadius=\{\[ring\.radius\]\}/)
  assert.match(collusionStatsSource, /const RADAR_GRID_TICKS = RADAR_GRID_RINGS\.map\(ring => ring\.score\)/)
  assert.match(collusionStatsSource, /ticks=\{RADAR_GRID_TICKS\}/)
  assert.match(collusionStatsSource, /axisLine=\{false\}/)
  assert.doesNotMatch(collusionStatsSource, /<PolarGrid strokeDasharray="3 3" \/>/)
  assert.doesNotMatch(collusionStatsSource, /<PolarGrid stroke="hsl\(25, 95%, 88%\)"/)
})
