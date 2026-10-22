import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const pollingPath = 'frontend/src/pages/writing/progressPolling.ts'
const composePath = 'frontend/src/pages/writing/WritingCompose.tsx'

function readSource(path: string): string {
  return readFileSync(path, 'utf8')
}

const moduleUrl = pathToFileURL(resolve(pollingPath)).href
const polling = await import(moduleUrl)

function completedStatus(overrides: Record<string, unknown> = {}) {
  return {
    active: false,
    stage: 'completed',
    progressPct: 100,
    currentDetail: '100%|生成完成',
    ...overrides,
  }
}

test('pollWritingTask returns only when inactive and completed', async () => {
  const status = completedStatus()
  const result = await polling.pollWritingTask({
    fetchStatus: async () => status,
    timeoutMs: 1_000,
    visibleIntervalMs: 1,
  })
  assert.equal(result.stage, 'completed')
})

test('pollWritingTask throws on failed terminal with sanitized detail', async () => {
  await assert.rejects(
    () => polling.pollWritingTask({
      fetchStatus: async () => ({
        active: false,
        stage: 'failed',
        progressPct: 40,
        currentDetail: '失败|project_id=12 node_id=9 生成失败',
      }),
      timeoutMs: 1_000,
      visibleIntervalMs: 1,
    }),
    (error: unknown) => {
      assert.ok(error instanceof Error)
      assert.match(error.message, /生成失败|失败/)
      assert.doesNotMatch(error.message, /project_id=12/)
      return true
    },
  )
})

test('pollWritingTask throws on cancelled terminal', async () => {
  await assert.rejects(
    () => polling.pollWritingTask({
      fetchStatus: async () => ({
        active: false,
        stage: 'cancelled',
        progressPct: 10,
        currentDetail: '任务已取消',
      }),
      timeoutMs: 1_000,
      visibleIntervalMs: 1,
    }),
    /取消/,
  )
})

test('pollWritingTask throws on unknown inactive stage', async () => {
  await assert.rejects(
    () => polling.pollWritingTask({
      fetchStatus: async () => ({
        active: false,
        stage: 'weird',
        progressPct: 0,
        currentDetail: 'unknown-detail',
      }),
      timeoutMs: 1_000,
      visibleIntervalMs: 1,
    }),
    (error: unknown) => {
      assert.ok(error instanceof Error)
      assert.match(error.message, /unknown-detail|异常|未知/)
      return true
    },
  )
})

test('pollWritingTask times out by wall clock while still active', async () => {
  const started = Date.now()
  await assert.rejects(
    () => polling.pollWritingTask({
      fetchStatus: async () => ({
        active: true,
        stage: 'composing',
        progressPct: 10,
        currentDetail: '生成中',
      }),
      timeoutMs: 40,
      visibleIntervalMs: 5,
    }),
    /超时/,
  )
  assert.ok(Date.now() - started >= 40)
})

test('pollWritingTask stops immediately on abort', async () => {
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(
    () => polling.pollWritingTask({
      fetchStatus: async () => completedStatus(),
      timeoutMs: 5_000,
      signal: controller.signal,
      visibleIntervalMs: 20,
    }),
    (error: unknown) => {
      assert.ok(error instanceof Error)
      assert.equal(error.name, 'AbortError')
      return true
    },
  )
})

test('pollWritingTask slows down when document is hidden', async () => {
  const timestamps: number[] = []
  const previous = globalThis.document
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: { hidden: true },
  })
  try {
    await assert.rejects(
      () => polling.pollWritingTask({
        fetchStatus: async () => {
          timestamps.push(Date.now())
          return {
            active: true,
            stage: 'composing',
            progressPct: 1,
            currentDetail: 'hidden',
          }
        },
        timeoutMs: 80,
        visibleIntervalMs: 5,
        hiddenIntervalMs: 30,
      }),
      /超时/,
    )
  } finally {
    Object.defineProperty(globalThis, 'document', {
      configurable: true,
      value: previous,
    })
  }
  assert.ok(timestamps.length >= 2)
  assert.ok(timestamps[1] - timestamps[0] >= 25)
})

test('WritingCompose uses shared poller and a single AbortController', () => {
  const source = readSource(composePath)
  assert.match(source, /pollWritingTask/)
  assert.match(source, /AbortController/)
  assert.match(source, /abort\(/)
  assert.equal((source.match(/for \(let i = 0; i < MAX_ATTEMPTS/g) || []).length, 0)
  assert.doesNotMatch(source, /finalStage === 'failed'/)
})
