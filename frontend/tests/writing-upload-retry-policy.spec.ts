import assert from 'node:assert/strict'
import test from 'node:test'
import {
  POLL_MAX_NO_ADVANCE_MS,
  classifyPollError,
  computeProgressKey,
  decorateWaitingText,
  isRetryableNetworkError,
} from '../src/pages/writing/writingUploadRetryPolicy.ts'

// isRetryableNetworkError --------------------------------------------------

test('isRetryableNetworkError 识别 axios 默认超时消息', () => {
  assert.equal(isRetryableNetworkError(new Error('timeout of 30000ms exceeded')), true)
  assert.equal(isRetryableNetworkError(new Error('timeout of 60000ms exceeded')), true)
})

test('isRetryableNetworkError 识别 Network Error / 中文连接失败', () => {
  assert.equal(isRetryableNetworkError(new Error('Network Error')), true)
  assert.equal(isRetryableNetworkError(new Error('无法连接到服务器，请检查网络连接后重试')), true)
  assert.equal(isRetryableNetworkError(new Error('ECONNREFUSED 127.0.0.1:8000')), true)
})

test('isRetryableNetworkError 识别维护/升级提示', () => {
  assert.equal(isRetryableNetworkError(new Error('系统正在维护升级，请稍后再试')), true)
  assert.equal(isRetryableNetworkError(new Error('Bad Gateway')), true)
})

test('isRetryableNetworkError 对业务错误返回 false', () => {
  assert.equal(isRetryableNetworkError(new Error('余额不足')), false)
  assert.equal(isRetryableNetworkError(new Error('约束提取失败')), false)
  assert.equal(isRetryableNetworkError(new Error('权限不足')), false)
  assert.equal(isRetryableNetworkError(new Error('未找到招标文件')), false)
  assert.equal(isRetryableNetworkError(new Error('文档处理恢复失败')), false)
})

test('isRetryableNetworkError 对非 Error 输入安全兜底', () => {
  assert.equal(isRetryableNetworkError('timeout of 30000ms exceeded'), true)
  assert.equal(isRetryableNetworkError(null), false)
  assert.equal(isRetryableNetworkError(undefined), false)
  assert.equal(isRetryableNetworkError({}), false)
})

// classifyPollError --------------------------------------------------------

test('classifyPollError: 业务失败语义 → fatal, 不管是否有推进', () => {
  const v = classifyPollError(new Error('约束提取失败: LLM 返回为空'), 1_000)
  assert.equal(v.kind, 'fatal')
  if (v.kind === 'fatal') {
    assert.match(v.reason, /约束提取失败/)
  }
})

test('classifyPollError: timeout + 未超出无推进上限 → retry(isTimeout=true)', () => {
  const v = classifyPollError(new Error('timeout of 30000ms exceeded'), 60_000)
  assert.equal(v.kind, 'retry')
  if (v.kind === 'retry') {
    assert.equal(v.isTimeout, true)
  }
})

test('classifyPollError: timeout + 超出无推进上限 → fatal', () => {
  const noAdvance = POLL_MAX_NO_ADVANCE_MS + 10_000
  const v = classifyPollError(new Error('timeout of 30000ms exceeded'), noAdvance)
  assert.equal(v.kind, 'fatal')
  if (v.kind === 'fatal') {
    assert.match(v.reason, /连续 \d+s 无进展/)
    assert.match(v.reason, /timeout/)
  }
})

test('classifyPollError: Network Error 可重试门槛内视为 retry', () => {
  const v = classifyPollError(new Error('Network Error'), 30_000)
  assert.equal(v.kind, 'retry')
  if (v.kind === 'retry') {
    assert.equal(v.isTimeout, false)
  }
})

test('classifyPollError: 未知错误 在门槛内保守 retry, 超出门槛 fatal', () => {
  const within = classifyPollError(new Error('意外 JSON 解析错'), 10_000)
  assert.equal(within.kind, 'retry')
  const beyond = classifyPollError(new Error('意外 JSON 解析错'), POLL_MAX_NO_ADVANCE_MS + 5_000)
  assert.equal(beyond.kind, 'fatal')
})

test('classifyPollError: 默认门槛为 POLL_MAX_NO_ADVANCE_MS', () => {
  const v = classifyPollError(new Error('timeout of 30000ms exceeded'), POLL_MAX_NO_ADVANCE_MS + 1)
  assert.equal(v.kind, 'fatal')
})

// computeProgressKey -------------------------------------------------------

test('computeProgressKey: status/stage/progress 任一变化都产生不同 key', () => {
  const base = { status: 'parsing', currentStage: 'parse_tender', overallProgress: 40 }
  const k0 = computeProgressKey(base)
  assert.notEqual(k0, computeProgressKey({ ...base, status: 'extracting' }))
  assert.notEqual(k0, computeProgressKey({ ...base, currentStage: 'doc_completed' }))
  assert.notEqual(k0, computeProgressKey({ ...base, overallProgress: 41 }))
  assert.equal(k0, computeProgressKey({ ...base }))
})

test('computeProgressKey: 缺字段时安全兜底', () => {
  assert.equal(computeProgressKey({}), '||-1')
  assert.equal(computeProgressKey({ status: 'uploading' }), 'uploading||-1')
})

// decorateWaitingText ------------------------------------------------------

test('decorateWaitingText: POST 超时追加"等待后端确认"', () => {
  assert.equal(
    decorateWaitingText('正在解析招标文件', 'post_timeout'),
    '正在解析招标文件（等待后端确认）',
  )
})

test('decorateWaitingText: 轮询重试追加"自动重试中"', () => {
  assert.equal(
    decorateWaitingText('处理中', 'poll_retry'),
    '处理中（自动重试中）',
  )
})

test('decorateWaitingText: 已含全角括号时不重复追加', () => {
  assert.equal(
    decorateWaitingText('处理中（等待后端确认）', 'post_timeout'),
    '处理中（等待后端确认）',
  )
})

test('decorateWaitingText: 空字符串返回后缀', () => {
  assert.equal(decorateWaitingText('', 'post_timeout'), '（等待后端确认）')
})
