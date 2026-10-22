import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isRunningOverallStatus,
  mapProgressStatusSteps,
  normalizeOverallStatus,
  normalizeStepStatus,
  sanitizeProgressText,
  sanitizeUserFacingText,
  unwrapProgressStatusPayload,
} from '../src/services/progressStatusTransforms.ts'
import { getProgressPageError } from '../src/components/progressPageState.ts'

test('unwrapProgressStatusPayload 应返回 ApiResponse.data 内层对象', () => {
  const payload = unwrapProgressStatusPayload({
    code: 200,
    message: 'success',
    data: {
      project_id: 539,
      overall_status: 'running',
      steps: [],
    },
  }, '串标状态')

  assert.equal(payload.project_id, 539)
  assert.equal(payload.overall_status, 'running')
})

test('unwrapProgressStatusPayload 在缺失 data 时应抛出可读错误', () => {
  assert.throws(
    () => unwrapProgressStatusPayload({ code: 200, message: 'success' }, '审查状态'),
    /审查状态响应格式异常/,
  )
})

test('mapProgressStatusSteps 应对非数组输入返回空数组', () => {
  assert.deepEqual(mapProgressStatusSteps(undefined), [])
})

test('sanitizeProgressText 应将阶段码翻译为中文并移除技术参数', () => {
  assert.equal(
    sanitizeProgressText('hard_constraint: 2/2'),
    '合规要求 2/2',
  )

  assert.equal(
    sanitizeProgressText('597:598 analyze_dimensions'),
    '多维度并行分析',
  )

  assert.equal(
    sanitizeProgressText('pair_id=597:598 retrieve_context'),
    '检索文档内容',
  )

  assert.equal(
    sanitizeProgressText('等待technical章节画像模型返回'),
    '等待技术章节画像模型返回',
  )

  assert.equal(
    sanitizeProgressText('等待commercial章节画像模型返回'),
    '等待商务章节画像模型返回',
  )
})

test('mapProgressStatusSteps 应清洗步骤标签与明细', () => {
  assert.deepEqual(
    mapProgressStatusSteps([
      {
        label: 'retrieve_context',
        status: 'running',
        progress: 35,
        detail: 'hard_constraint: 2/2',
      },
    ]),
    [
      {
        label: '检索文档内容',
        status: 'running',
        progress: 35,
        detail: '合规要求 2/2',
      },
    ],
  )
})

test('mapProgressStatusSteps 应保留后端 progress 原值供投影层直接消费', () => {
  assert.deepEqual(
    mapProgressStatusSteps([
      {
        label: '目录生成',
        status: 'running',
        progress: 1,
        detail: '处理中',
      },
      {
        label: '章节画像',
        status: 'waiting',
        progress: 0,
      },
    ]),
    [
      {
        label: '目录生成',
        status: 'running',
        progress: 1,
        detail: '处理中',
      },
      {
        label: '章节画像',
        status: 'waiting',
        progress: 0,
        detail: undefined,
      },
    ],
  )
})

test('getProgressPageError 在请求恢复成功后应清空瞬时错误', () => {
  assert.equal(
    getProgressPageError({
      overallStatus: 'running',
      errorMessage: null,
      failedText: '分析失败',
    }),
    null,
  )

  assert.equal(
    getProgressPageError({
      overallStatus: 'completed',
      errorMessage: null,
      failedText: '分析失败',
    }),
    null,
  )
})

test('getProgressPageError 应保留业务失败态并优先显示 queryError', () => {
  assert.equal(
    getProgressPageError({
      overallStatus: 'failed',
      errorMessage: '后端失败原因',
      failedText: '分析失败',
    }),
    '后端失败原因',
  )

  assert.match(
    getProgressPageError({
      overallStatus: 'failed',
      errorMessage: '后端失败原因',
      queryError: new Error('无效的访问令牌'),
      failedText: '分析失败',
    }) || '',
    /获取状态失败: 无效的访问令牌/,
  )
})

// ── 新增: 状态标准化统一出口 ─────────────────────────────────────────

test('normalizeStepStatus 同时接受 processing/running 并映射为 processing', () => {
  assert.equal(normalizeStepStatus('processing'), 'processing')
  assert.equal(normalizeStepStatus('running'), 'processing')
  assert.equal(normalizeStepStatus('RUNNING'), 'processing')
  assert.equal(normalizeStepStatus('in_progress'), 'processing')
})

test('normalizeStepStatus 同时接受 pending/waiting/queued 并映射为 pending', () => {
  assert.equal(normalizeStepStatus('pending'), 'pending')
  assert.equal(normalizeStepStatus('waiting'), 'pending')
  assert.equal(normalizeStepStatus('queued'), 'pending')
  assert.equal(normalizeStepStatus(undefined), 'pending')
  assert.equal(normalizeStepStatus(''), 'pending')
  assert.equal(normalizeStepStatus('something_unknown'), 'pending')
})

test('normalizeStepStatus 同时接受 failed/error 并映射为 error', () => {
  assert.equal(normalizeStepStatus('failed'), 'error')
  assert.equal(normalizeStepStatus('error'), 'error')
  assert.equal(normalizeStepStatus('failure'), 'error')
})

test('normalizeStepStatus completed 直接透传', () => {
  assert.equal(normalizeStepStatus('completed'), 'completed')
  assert.equal(normalizeStepStatus('COMPLETED'), 'completed')
  assert.equal(normalizeStepStatus('done'), 'completed')
  assert.equal(normalizeStepStatus('success'), 'completed')
})

test('normalizeOverallStatus 接受 processing/running 并映射为 running', () => {
  assert.equal(normalizeOverallStatus('processing'), 'running')
  assert.equal(normalizeOverallStatus('running'), 'running')
  assert.equal(normalizeOverallStatus('IN_PROGRESS'), 'running')
})

test('normalizeOverallStatus 接受 idle/pending/空值 映射为 idle', () => {
  assert.equal(normalizeOverallStatus('idle'), 'idle')
  assert.equal(normalizeOverallStatus('pending'), 'idle')
  assert.equal(normalizeOverallStatus(undefined), 'idle')
  assert.equal(normalizeOverallStatus(''), 'idle')
})

test('normalizeOverallStatus 保留 completed 与 failed', () => {
  assert.equal(normalizeOverallStatus('completed'), 'completed')
  assert.equal(normalizeOverallStatus('failed'), 'failed')
  assert.equal(normalizeOverallStatus('error'), 'failed')
})

test('isRunningOverallStatus 对 processing/running 都返回 true', () => {
  assert.equal(isRunningOverallStatus('running'), true)
  assert.equal(isRunningOverallStatus('processing'), true)
  assert.equal(isRunningOverallStatus('completed'), false)
  assert.equal(isRunningOverallStatus('failed'), false)
  assert.equal(isRunningOverallStatus('idle'), false)
  assert.equal(isRunningOverallStatus(undefined), false)
})

// review comment / evidence_anchor 的 LLM 自由文本英文污染清洗（与后端 internal_to_display 同义）

test('sanitizeUserFacingText 移除内部规则引用 Golden Rule', () => {
  const out = sanitizeUserFacingText('根据Golden Rule6相关性审查，响应相关性不足')
  assert.ok(!out.includes('Golden Rule'))
  assert.ok(!out.includes('Rule'))
  assert.ok(out.includes('相关性审查'))
})

test('sanitizeUserFacingText 将 score=N 转为中文评分', () => {
  assert.ok(sanitizeUserFacingText('故判定为不符合，score=30。').includes('评分30'))
  assert.ok(!sanitizeUserFacingText('score: 85').toLowerCase().includes('score'))
})

test('sanitizeUserFacingText 将 relevance_score=N 转为中文相关性评分', () => {
  const out = sanitizeUserFacingText('该要求与投标响应领域不相关，relevance_score=10。')
  assert.ok(out.includes('相关性评分10'))
  assert.ok(!out.toLowerCase().includes('relevance_score'))
})

test('sanitizeUserFacingText 将证据锚点 pages:X-Y 转为中文页码', () => {
  assert.equal(sanitizeUserFacingText('pages:5-10-11-16-82'), '第5、10、11、16、82页')
  assert.equal(sanitizeUserFacingText('pages:7'), '第7页')
})

test('sanitizeUserFacingText 对已清洗的污染文本幂等', () => {
  const text = '根据Golden Rule6相关性审查，score=30，pages:5-10'
  const once = sanitizeUserFacingText(text)
  assert.equal(sanitizeUserFacingText(once), once)
  assert.ok(!once.includes('Golden Rule'))
  assert.ok(!once.toLowerCase().includes('score'))
  assert.ok(!once.toLowerCase().includes('pages'))
})

test('sanitizeUserFacingText 将 source_clause_ids 转为中文来源约束', () => {
  const text =
    '约束ID 18279 尚未出现在任何当前章节草稿的 source_clause_ids 中，请在绑定或章节内容中确认是否需要补充响应。'
  const out = sanitizeUserFacingText(text)
  assert.ok(!out.includes('source_clause_ids'))
  assert.ok(out.includes('来源约束'))
  assert.ok(!sanitizeUserFacingText('检查 source_material_ids').includes('source_material_ids'))
})
