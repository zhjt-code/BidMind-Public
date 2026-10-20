import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  BUSINESS_HUB_ACCENTS,
  BUSINESS_HUB_CONFIGS,
  BUSINESS_HUB_TYPES,
  getBusinessHubConfig,
  resolveHubMetricValue,
} from '../src/pages/projectList/hub/businessHubConfig.ts'
import { pickRecentInProgressProject } from '../src/pages/projectList/hub/pickRecentInProgressProject.ts'
import type { Project } from '../src/stores/projectStore.ts'

const heroSource = readFileSync(
  new URL('../src/pages/projectList/hub/BusinessHubHero.tsx', import.meta.url),
  'utf8',
).replace(/\r\n/g, '\n')

const onboardingSource = readFileSync(
  new URL('../src/pages/projectList/hub/BusinessHubOnboarding.tsx', import.meta.url),
  'utf8',
).replace(/\r\n/g, '\n')

const VALID_STATUS_FILTERS = new Set(['pending', 'processing', 'completed', 'failed', 'all'])

test('业务域 Hub 配置恰好覆盖三类业务且 key 合法', () => {
  assert.deepEqual([...BUSINESS_HUB_TYPES].sort(), ['bid_review', 'bid_writing', 'collusion_check'])
  assert.equal(Object.keys(BUSINESS_HUB_CONFIGS).length, 3)

  for (const type of BUSINESS_HUB_TYPES) {
    const config = getBusinessHubConfig(type)
    assert.equal(config.type, type)
    assert.ok(config.title.trim().length > 0)
    assert.ok(config.tagline.trim().length > 0)
    assert.ok(config.flowSteps.length > 0)
    assert.equal(config.metrics.length, 5)
  }

  assert.equal(getBusinessHubConfig('bid_writing').iconName, 'PenTool')
  assert.equal(getBusinessHubConfig('bid_review').iconName, 'ClipboardCheck')
  assert.equal(getBusinessHubConfig('collusion_check').iconName, 'ShieldAlert')
})

test('指标覆盖全部与四个互斥状态桶，保留动作指引且按状态语义着色', () => {
  for (const type of BUSINESS_HUB_TYPES) {
    const metrics = getBusinessHubConfig(type).metrics
    assert.deepEqual(
      metrics.map((metric) => metric.key),
      ['all', 'pending', 'processing', 'completed', 'failed'],
    )
    assert.deepEqual(
      metrics.map((metric) => metric.label),
      ['全部', '待上传', '处理中', '已完成', '失败'],
    )
    assert.deepEqual(metrics.map((metric) => metric.tone), ['blue', 'slate', 'orange', 'green', 'red'])
    assert.deepEqual(
      metrics.map((metric) => metric.actionLabel),
      ['查看全部', '查看待上传', '查看进度', '查看结果', '查看失败'],
    )

    for (const metric of metrics) {
      assert.ok(VALID_STATUS_FILTERS.has(metric.statusFilter), metric.statusFilter)
      assert.equal(metric.statusFilter, metric.key === 'all' ? 'all' : metric.key)
      assert.ok(metric.label.trim().length > 0)
      assert.ok(metric.iconName.length > 0)
    }
  }
})

test('空态流程步数与标书编写文案锁定，标题步数可从配置推导', () => {
  assert.equal(getBusinessHubConfig('bid_writing').flowSteps.length, 4)
  assert.equal(getBusinessHubConfig('bid_review').flowSteps.length, 4)
  assert.equal(getBusinessHubConfig('collusion_check').flowSteps.length, 3)
  assert.deepEqual(
    getBusinessHubConfig('bid_writing').flowSteps.map((step) => step.label),
    ['上传招标文件', '确认约束与目录', '匹配素材并成稿', '预览并导出'],
  )
})

test('主题色板锁定在 teal/blue/iris 三族，防止漂移', () => {
  assert.deepEqual([...BUSINESS_HUB_ACCENTS].sort(), ['blue', 'iris', 'teal'])

  assert.equal(getBusinessHubConfig('bid_writing').theme.accent, 'teal')
  assert.equal(getBusinessHubConfig('bid_review').theme.accent, 'blue')
  assert.equal(getBusinessHubConfig('collusion_check').theme.accent, 'iris')

  for (const type of BUSINESS_HUB_TYPES) {
    const { theme } = getBusinessHubConfig(type)
    const joined = [
      theme.cardClass,
      theme.glowClass,
      theme.iconWrapClass,
      theme.primaryButtonClass,
      theme.stepDotClass,
      theme.stepCardClass,
      theme.stepConnectorClass,
    ].join(' ')
    if (theme.accent === 'iris') {
      assert.match(joined, /#9056D8/)
      assert.match(joined, /#7165EA/)
      assert.doesNotMatch(joined, /orange-/)
    } else {
      assert.match(joined, new RegExp(theme.accent))
    }
    for (const foreign of BUSINESS_HUB_ACCENTS.filter((accent) => accent !== theme.accent)) {
      assert.doesNotMatch(joined, new RegExp(`\\b${foreign}-`))
    }
  }
})

test('指标数值直接映射 stats 字段', () => {
  const stats = { total: 10, pending: 2, processing: 3, completed: 4, failed: 1 }
  assert.equal(resolveHubMetricValue('all', stats), 10)
  assert.equal(resolveHubMetricValue('pending', stats), 2)
  assert.equal(resolveHubMetricValue('processing', stats), 3)
  assert.equal(resolveHubMetricValue('completed', stats), 4)
  assert.equal(resolveHubMetricValue('failed', stats), 1)
  assert.equal(stats.pending + stats.processing + stats.completed + stats.failed, stats.total)
})

test('有数据态 Hero 不渲染营销 tagline，空态 Onboarding 仍保留', () => {
  assert.equal(heroSource.includes('config.tagline'), false)
  assert.equal(heroSource.includes('tagline'), false)
  assert.equal(onboardingSource.includes('{config.tagline}'), true)
})

test('有数据态 Hero 采用紧凑间距，避免营销展示高度', () => {
  assert.equal(heroSource.includes('gap-2.5 p-3.5 sm:p-4'), true)
  assert.equal(heroSource.includes('gap-4 p-5 sm:p-6'), false)
  assert.equal(heroSource.includes('sm:items-center'), true)
  assert.equal(heroSource.includes('useOneShotOverviewCollapse'), false)
})

test('继续 CTA 优先选择最近更新且未完成的项目', () => {
  const projects: Project[] = [
    {
      id: 1,
      name: '已完成',
      projectType: 'bid_review',
      status: 'completed',
      createdAt: '2026-07-01T08:00:00.000Z',
      updatedAt: '2026-07-12T08:00:00.000Z',
      ownerId: 1,
    },
    {
      id: 2,
      name: '较旧进行中',
      projectType: 'bid_review',
      status: 'reviewing',
      createdAt: '2026-07-02T08:00:00.000Z',
      updatedAt: '2026-07-10T08:00:00.000Z',
      ownerId: 1,
    },
    {
      id: 3,
      name: '最新进行中',
      projectType: 'bid_review',
      status: 'extracted',
      createdAt: '2026-07-03T08:00:00.000Z',
      updatedAt: '2026-07-11T08:00:00.000Z',
      ownerId: 1,
    },
  ]

  assert.equal(pickRecentInProgressProject(projects)?.id, 3)
  assert.equal(pickRecentInProgressProject(projects.filter((p) => p.status === 'completed')), null)
})

test('继续 CTA 披露项目名并提供全名 title，不再使用模糊「上次」文案', () => {
  assert.equal(heroSource.includes('继续上次项目'), false)
  assert.equal(heroSource.includes('继续“{recentProject.name}”'), true)
  assert.equal(heroSource.includes('继续 ·'), false)
  assert.equal(heroSource.includes('title={recentProject.name}'), true)
  assert.equal(heroSource.includes('min-w-0 truncate'), true)
})
