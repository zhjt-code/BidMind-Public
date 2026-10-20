import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import {
  PROJECT_DASHBOARD_QUERY_KEY,
  normalizeProjectDashboardResponse,
  projectDashboardQueryKey,
} from '../src/services/projectDashboardApi.ts'

test('控制台响应从后端 snake_case 转为前端 camelCase', () => {
  const result = normalizeProjectDashboardResponse({
    scope: 'mine',
    generated_at: '2026-07-17T08:00:00Z',
    window_days: 7,
    health_window_days: 30,
    summary: {
      total: 3,
      pending: 1,
      processing: 1,
      completed: 1,
      failed: 0,
      this_month: 2,
    },
    type_distribution: [{ key: 'bid_review', label: '投标审查', count: 2 }],
    status_distribution: [{ key: 'processing', label: '处理中', count: 1 }],
    stage_distribution: [{ key: 'reviewing', label: 'AI分析中', count: 1 }],
    type_status_matrix: [{
      project_type: 'bid_review',
      label: '投标审查',
      pending: 0,
      processing: 1,
      completed: 1,
      failed: 0,
    }],
    daily_created: [{ date: '2026-07-17', count: 2 }],
    daily_completed: [{ date: '2026-07-17', count: 1 }],
    recent_projects: [{
      id: 7,
      name: '项目A',
      project_type: 'bid_review',
      status: 'reviewing',
      created_at: '2026-07-16T08:00:00Z',
      updated_at: '2026-07-17T08:00:00Z',
    }],
    health: {
      success_rate: 0.5,
      completion_by_type: [
        { project_type: 'bid_writing', label: '标书编写', avg_duration_ms: 456000, sample_count: 1 },
        { project_type: 'bid_review', label: '投标审查', avg_duration_ms: null, sample_count: 0 },
      ],
      backlog: 4,
      net_change: -1,
    },
    action_queue: [{
      id: 'awaiting_confirm',
      label: '待人工确认',
      count: 2,
      tone: 'amber',
      action_label: '去确认',
      filter: { status: null, stages: ['extracted', 'outlined', 'matched'], stale_hours: null },
    }],
    stage_groups: [{
      key: 'awaiting_confirm',
      label: '等待人工确认',
      count: 2,
      member_statuses: ['extracted', 'outlined', 'matched'],
    }],
  })

  assert.equal(result.scope, 'mine')
  assert.equal(result.generatedAt, '2026-07-17T08:00:00Z')
  assert.equal(result.windowDays, 7)
  assert.equal(result.healthWindowDays, 30)
  assert.deepEqual(result.summary, {
    total: 3,
    pending: 1,
    processing: 1,
    completed: 1,
    failed: 0,
    thisMonth: 2,
  })
  assert.deepEqual(result.typeStatusMatrix[0], {
    projectType: 'bid_review',
    label: '投标审查',
    pending: 0,
    processing: 1,
    completed: 1,
    failed: 0,
  })
  assert.deepEqual(result.recentProjects[0], {
    id: 7,
    name: '项目A',
    projectType: 'bid_review',
    status: 'reviewing',
    createdAt: '2026-07-16T08:00:00Z',
    updatedAt: '2026-07-17T08:00:00Z',
  })
  assert.equal(result.health.successRate, 0.5)
  assert.equal(result.health.backlog, 4)
  assert.deepEqual(result.health.completionByType[0], {
    projectType: 'bid_writing',
    label: '标书编写',
    avgDurationMs: 456000,
    sampleCount: 1,
  })
  assert.equal(result.health.completionByType[1].avgDurationMs, null)
  assert.equal(result.health.netChange, -1)
  assert.equal(result.dailyCompleted[0].count, 1)
  assert.deepEqual(result.actionQueue[0].filter.stages, ['extracted', 'outlined', 'matched'])
  assert.deepEqual(result.stageGroups[0].memberStatuses, ['extracted', 'outlined', 'matched'])
})

test('控制台查询 key 包含用户、角色和两套窗口, 避免跨账号或跨窗口缓存复用', () => {
  assert.deepEqual(projectDashboardQueryKey(12, 'user', 30, 7), [
    PROJECT_DASHBOARD_QUERY_KEY,
    12,
    'user',
    30,
    7,
  ])
  assert.notDeepEqual(
    projectDashboardQueryKey(12, 'user', 30, 7),
    projectDashboardQueryKey(12, 'admin', 30, 7),
  )
  assert.notDeepEqual(
    projectDashboardQueryKey(12, 'user', 30, 7),
    projectDashboardQueryKey(13, 'user', 30, 7),
  )
  assert.notDeepEqual(
    projectDashboardQueryKey(12, 'user', 30, 7),
    projectDashboardQueryKey(12, 'user', 7, 30),
  )
})

test('仪表盘客户端与其它 API 一样静态引用 httpClient', () => {
  const src = readFileSync(new URL('../src/services/projectDashboardService.ts', import.meta.url), 'utf8')
  assert.match(src, /import \{ api \} from '\.\/httpClient'/)
  assert.doesNotMatch(src, /import\('\.\/httpClient'\)/)
  const model = readFileSync(new URL('../src/services/projectDashboardApi.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(model, /httpClient/)
})
