import assert from 'node:assert/strict'
import test from 'node:test'

import {
  normalizeFlowFunnelResponse,
  normalizeHotspotsResponse,
  normalizeOpsOverviewResponse,
  normalizeWorkerHealthResponse,
} from '../src/services/opsApiNormalize.ts'

test('normalizeWorkerHealthResponse 补齐 Worker 健康摘要数组字段', () => {
  const normalized = normalizeWorkerHealthResponse({
    health: 'healthy',
    worker_count: 1,
    workers: [
      {
        worker_id: 'worker-1',
        state: 'idle',
      },
    ],
    reasons: {},
  })

  assert.deepEqual(normalized.expected_functions, [])
  assert.deepEqual(normalized.missing_functions, [])
  assert.deepEqual(normalized.covered_functions, [])
  assert.deepEqual(normalized.resources, [])
  assert.deepEqual(normalized.recommended_actions, [])
  assert.deepEqual(normalized.action_items, [])
  assert.deepEqual(normalized.reasons.unhealthy, [])
  assert.deepEqual(normalized.reasons.degraded, [])
  assert.deepEqual(normalized.workers[0].functions, [])
})

test('normalizeHotspotsResponse 补齐热点详情相关数组字段', () => {
  const normalized = normalizeHotspotsResponse({
    window: '24h',
    generated_at: '2026-06-11T00:00:00Z',
    total: 1,
    items: [
      {
        name: 'metric-a',
        related: {},
      },
    ],
  })

  assert.equal(normalized.total, 1)
  assert.deepEqual(normalized.items[0].change_points, [])
  assert.deepEqual(normalized.items[0].related.llm_execution_trace, [])
  assert.deepEqual(normalized.items[0].related.system_error_log, [])
  assert.deepEqual(normalized.items[0].related.prompt_version_changes, [])
})

test('normalizeHotspotsResponse 规范化热点详情相关数组元素', () => {
  const normalized = normalizeHotspotsResponse({
    items: [
      {
        name: 'metric-a',
        related: {
          llm_execution_trace: [{}],
          system_error_log: [{}],
          prompt_version_changes: [{}],
        },
      },
    ],
  })

  assert.deepEqual(normalized.items[0].related.llm_execution_trace[0], {
    id: 0,
    created_at: null,
    project_id: null,
  })
  assert.deepEqual(normalized.items[0].related.system_error_log[0], {
    id: 0,
    created_at: null,
    level: '',
    module: null,
    message: '',
  })
  assert.deepEqual(normalized.items[0].related.prompt_version_changes[0], {
    id: 0,
    purpose: '',
    version: 0,
    name: null,
    is_active: false,
    updated_at: null,
  })
})

test('normalizeFlowFunnelResponse 补齐漏斗数组字段', () => {
  const normalized = normalizeFlowFunnelResponse({
    flow: 'writer',
    funnel: null,
    time_series: null,
    outcome_distribution: null,
  })

  assert.deepEqual(normalized.funnel, [])
  assert.deepEqual(normalized.time_series, [])
  assert.deepEqual(normalized.outcome_distribution, {})
})

test('normalizeOpsOverviewResponse 补齐总览对象字段和层级数组', () => {
  const normalized = normalizeOpsOverviewResponse({
    layers: {
      platform: null,
      model: [{ name: 'redis_ready' }],
    },
    pillar_summary: null,
    business_health: null,
    dispatcher: null,
    cost: null,
  })

  assert.deepEqual(normalized.layers.platform, [])
  assert.equal(normalized.layers.model[0].name, 'redis_ready')
  assert.deepEqual(normalized.pillar_summary, {})
  assert.deepEqual(normalized.business_health, {})
  assert.deepEqual(normalized.dispatcher, {})
  assert.equal(normalized.cost.page_count, 0)
  assert.equal(normalized.cost.total_credits, 0)
  assert.equal(normalized.cost.unknown_project_count, 0)
  assert.deepEqual(normalized.cost.by_flow.map((row) => row.flow), [
    'bid_writing',
    'bid_review',
    'collusion_check',
  ])
  assert.ok(normalized.cost.by_flow.every((row) => (
    row.project_count === 0
    && row.input_tokens === 0
    && row.output_tokens === 0
    && row.total_credits === 0
    && row.project_avg_credits === 0
  )))
})
