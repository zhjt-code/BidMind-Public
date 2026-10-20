/**
 * useDocumentProcessPolling 行为契约测试。
 *
 * 因 frontend/tests 不引入 React 渲染基础设施，本 spec 直接测核心纯函数
 * runOneDocumentPollTick（独立模块 documentPollTick.ts，0 React/service 依赖）；
 * hook 模块本身（useDocumentProcessPolling.ts 含 React + documentService 链）
 * 通过字符串锚点确认 named exports 存在，与 review-progress-retry-panel 同款。
 *
 * 覆盖（10 条）：
 *  - completed 路径触发 onCompleted，不调度下一轮
 *  - failed 路径触发 onFailed + onRefreshRetryState，不调度下一轮
 *  - failed.error 缺失时使用兜底文案 "处理失败"
 *  - 进行中按 getInterval 调度下一轮
 *  - catch 路径走 3s 退避
 *  - 抛错且 unmounted 时不调度
 *  - 入口 isMounted=false 短路，不 fetch 不回调
 *  - fetch 后 isMounted=false 短路，不调用任何 callback
 *  - documentPollTick 模块导出契约
 *  - hook 模块 named exports 字符串锚点
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  runOneDocumentPollTick,
  type PollTickContext,
  type PollTickOutcome,
} from '../src/pages/upload/hooks/documentPollTick.ts'
import type { ProcessStatusData } from '../src/services/documentApi.ts'

function makeProcessStatus(overrides: Partial<ProcessStatusData> = {}): ProcessStatusData {
  return {
    status: 'processing',
    stage: 'parsing',
    stage_label: '解析中',
    progress: 30,
    total: 100,
    stages: [],
    ...overrides,
  }
}

interface RecordedCalls {
  ticks: ProcessStatusData[]
  completed: number
  failed: string[]
  refreshed: number
  scheduled: number[]
}

function makeContext(
  status: ProcessStatusData | Error,
  overrides: Partial<PollTickContext> = {},
): { ctx: PollTickContext; record: RecordedCalls } {
  const record: RecordedCalls = {
    ticks: [],
    completed: 0,
    failed: [],
    refreshed: 0,
    scheduled: [],
  }
  const ctx: PollTickContext = {
    projectId: 7,
    documentId: 42,
    fetchStatus: async () => {
      if (status instanceof Error) throw status
      return { data: status }
    },
    getInterval: (stage) => (stage === 'vl_recognition' ? 2000 : 5000),
    onTick: (data) => {
      record.ticks.push(data)
    },
    onCompleted: () => {
      record.completed += 1
    },
    onFailed: (msg) => {
      record.failed.push(msg)
    },
    onRefreshRetryState: async () => {
      record.refreshed += 1
    },
    scheduleNext: (delay) => {
      record.scheduled.push(delay)
    },
    isMounted: () => true,
    ...overrides,
  }
  return { ctx, record }
}

// completed -----------------------------------------------------------------

test('runOneDocumentPollTick: completed 触发 onCompleted, 不调度', async () => {
  const { ctx, record } = makeContext(makeProcessStatus({ status: 'completed', stage: 'completed' }))
  const outcome: PollTickOutcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'completed')
  assert.equal(record.completed, 1)
  assert.equal(record.failed.length, 0)
  assert.equal(record.scheduled.length, 0)
  assert.equal(record.refreshed, 0)
  assert.equal(record.ticks.length, 1)
})

// failed --------------------------------------------------------------------

test('runOneDocumentPollTick: failed 触发 onFailed + onRefreshRetryState, 不调度', async () => {
  const { ctx, record } = makeContext(
    makeProcessStatus({ status: 'failed', stage: 'failed', error: 'OCR 引擎崩溃' }),
  )
  const outcome = await runOneDocumentPollTick(ctx)
  await Promise.resolve()
  assert.equal(outcome, 'failed')
  assert.deepEqual(record.failed, ['OCR 引擎崩溃'])
  assert.equal(record.refreshed, 1)
  assert.equal(record.scheduled.length, 0)
  assert.equal(record.completed, 0)
})

test('runOneDocumentPollTick: failed.error 缺失时兜底为 "处理失败"', async () => {
  const { ctx, record } = makeContext(makeProcessStatus({ status: 'failed', stage: 'failed' }))
  await runOneDocumentPollTick(ctx)
  await Promise.resolve()
  assert.deepEqual(record.failed, ['处理失败'])
  assert.equal(record.refreshed, 1)
})

// continue ------------------------------------------------------------------

test('runOneDocumentPollTick: 进行中按 getInterval 调度下一轮', async () => {
  const { ctx, record } = makeContext(
    makeProcessStatus({ status: 'processing', stage: 'vl_recognition' }),
  )
  const outcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'continue')
  assert.deepEqual(record.scheduled, [2000])
  assert.equal(record.completed, 0)
  assert.equal(record.failed.length, 0)
})

// catch 退避 -----------------------------------------------------------------

test('runOneDocumentPollTick: 抛错时走 3s 退避', async () => {
  const { ctx, record } = makeContext(new Error('Network Error'))
  const outcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'continue')
  assert.deepEqual(record.scheduled, [3000])
  assert.equal(record.ticks.length, 0)
})

test('runOneDocumentPollTick: 抛错且 unmounted 时不调度', async () => {
  let mounted = true
  const { ctx, record } = makeContext(new Error('Network Error'), {
    isMounted: () => mounted,
  })
  const failingFetch = ctx.fetchStatus
  ctx.fetchStatus = async (...args) => {
    mounted = false
    return failingFetch(...args)
  }
  const outcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'continue')
  assert.equal(record.scheduled.length, 0)
})

// unmounted 短路 ------------------------------------------------------------

test('runOneDocumentPollTick: 入口 isMounted=false 短路, 不 fetch 不回调', async () => {
  let fetched = false
  const { ctx, record } = makeContext(makeProcessStatus(), {
    isMounted: () => false,
    fetchStatus: async () => {
      fetched = true
      return { data: makeProcessStatus() }
    },
  })
  const outcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'unmounted')
  assert.equal(fetched, false)
  assert.equal(record.ticks.length, 0)
  assert.equal(record.completed, 0)
  assert.equal(record.scheduled.length, 0)
})

test('runOneDocumentPollTick: fetch 后 isMounted=false 短路, 不调用任何 callback', async () => {
  let mounted = true
  const { ctx, record } = makeContext(makeProcessStatus({ status: 'completed' }), {
    isMounted: () => mounted,
  })
  const completedFetch = ctx.fetchStatus
  ctx.fetchStatus = async (...args) => {
    const r = await completedFetch(...args)
    mounted = false
    return r
  }
  const outcome = await runOneDocumentPollTick(ctx)
  assert.equal(outcome, 'unmounted')
  assert.equal(record.completed, 0)
  assert.equal(record.ticks.length, 0)
  assert.equal(record.scheduled.length, 0)
})

// 模块契约 ----------------------------------------------------------------

test('documentPollTick: 模块导出 runOneDocumentPollTick 纯函数', () => {
  assert.equal(typeof runOneDocumentPollTick, 'function')
})

test('useDocumentProcessPolling: hook 模块字符串包含 controller named exports', () => {
  const src = readFileSync('frontend/src/pages/upload/hooks/useDocumentProcessPolling.ts', 'utf8')
  assert.match(src, /export function useDocumentProcessPolling/)
  assert.match(src, /startPoll:/)
  assert.match(src, /stopPoll:/)
  assert.match(src, /hasActivePoll:/)
  assert.match(src, /export interface DocumentPollOptions/)
  assert.match(src, /export interface UseDocumentProcessPollingOptions/)
  assert.match(src, /export interface DocumentProcessPollingController/)
})
