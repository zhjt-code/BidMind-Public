/**
 * 写标素材 mapBatchStatusToSteps 防御测试。
 *
 * 该函数是素材页轮询的"状态机心脏"，从 WritingMaterials.tsx 抽出。
 * 历史教训：完成态必须依赖 phase==='done'；idle 不等于完成态；
 * image_classify / label_resolve 必须收敛到 tagging 步骤。
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { mapBatchStatusToSteps } from '../src/pages/writing/WritingMaterials/batchStatusMapper.ts'
import type { MaterialBatchStatus } from '../src/services/writingApi.ts'

function buildStatus(overrides: Partial<MaterialBatchStatus> = {}): MaterialBatchStatus {
  return {
    total: 0,
    completed: 0,
    failed: 0,
    processing: 0,
    phase: 'idle',
    vlProgress: '',
    taskActive: false,
    heartbeatPhase: null,
    retryable: false,
    failedNode: null,
    targetId: null,
    failedStage: null,
    retryActionLabel: '',
    preservedOutputs: [],
    resetScope: [],
    lastAttempt: 0,
    retryRunning: false,
    lastError: null,
    ...overrides,
  }
}

test('mapBatchStatusToSteps: phase=done 四步全部完成且 overallTarget=1', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'done' }))
  assert.equal(result.steps.length, 4)
  assert.ok(result.steps.every((s) => s.status === 'completed'))
  assert.equal(result.overallTarget, 1)
  assert.equal(result.stageText, '全部处理完成')
})

test('mapBatchStatusToSteps: phase=failed stageText 为处理失败且步骤全 pending', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'failed' }))
  assert.equal(result.stageText, '处理失败')
  assert.ok(result.steps.every((s) => s.status === 'pending'))
})

test('mapBatchStatusToSteps: phase=failed 有 lastError 时 stageText 带原因', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({
      phase: 'failed',
      lastError: '素材索引失败: 文档不可用',
    }),
  )
  assert.match(result.stageText, /^处理失败:/)
  assert.match(result.stageText, /素材索引失败|文档不可用/)
})

test('mapBatchStatusToSteps: phase=parsing 第一步 processing 其余 pending', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'parsing' }))
  assert.equal(result.steps[0].status, 'processing')
  assert.equal(result.steps[0].targetProgress, 0.2)
  assert.ok(result.steps.slice(1).every((s) => s.status === 'pending'))
  assert.equal(result.stageText, '步骤1 解析文档')
})

test('mapBatchStatusToSteps: phase=vl_recognition 含 vlProgress 时按比例推进且带 detail', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({ phase: 'vl_recognition', vlProgress: '3/10' }),
  )
  assert.equal(result.steps[0].status, 'completed')
  assert.equal(result.steps[1].status, 'processing')
  assert.equal(result.steps[1].targetProgress, 0.3)
  assert.equal(result.steps[1].detail, '已识别 3/10')
  assert.equal(result.stageText, '步骤2 VL图片识别 · 已识别 3/10')
})

test('mapBatchStatusToSteps: phase=vl_recognition 缺 vlProgress 时 targetProgress=0 不抛错', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'vl_recognition' }))
  assert.equal(result.steps[1].status, 'processing')
  assert.equal(result.steps[1].targetProgress, 0)
})

test('mapBatchStatusToSteps: phase=indexing 按 (completed+failed)/total 推进且上限 0.95', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({ phase: 'indexing', total: 10, completed: 3, failed: 1 }),
  )
  assert.equal(result.steps[2].status, 'processing')
  // total + completed + failed = 14, (completed+failed)=4 → 4/14 ≈ 0.2857
  assert.ok(result.steps[2].targetProgress > 0.28 && result.steps[2].targetProgress < 0.29)
})

test('mapBatchStatusToSteps: phase=indexing 进度接近 100% 时不会超过 0.95 上限', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({ phase: 'indexing', total: 0, completed: 100, failed: 0 }),
  )
  assert.equal(result.steps[2].targetProgress, 0.95)
})

test('mapBatchStatusToSteps: phase=indexing total+completed+failed=0 时使用 0.5 兜底', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({ phase: 'indexing', total: 0, completed: 0, failed: 0 }),
  )
  assert.equal(result.steps[2].targetProgress, 0.5)
})

test('mapBatchStatusToSteps: phase=image_classify 必须收敛到 tagging 步骤而非误判 done', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'image_classify' }))
  assert.equal(result.steps[3].status, 'processing')
  assert.equal(result.steps[3].targetProgress, 0.3)
  assert.equal(result.steps[3].detail, '图片分类与结构化')
  assert.ok(result.steps.every((s) => s.status !== 'completed' || s.id !== 'tagging'))
  assert.notEqual(result.stageText, '全部处理完成')
})

test('mapBatchStatusToSteps: phase=label_resolve 也收敛到 tagging', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'label_resolve' }))
  assert.equal(result.steps[3].status, 'processing')
  assert.equal(result.steps[3].targetProgress, 0.6)
  assert.equal(result.steps[3].detail, '标签交叉校验')
})

test('mapBatchStatusToSteps: heartbeatPhase=label_resolve 即便 phase=tagging 也使用 label_resolve 文案', () => {
  const result = mapBatchStatusToSteps(
    buildStatus({ phase: 'tagging', heartbeatPhase: 'label_resolve' }),
  )
  assert.equal(result.steps[3].status, 'processing')
  assert.equal(result.steps[3].targetProgress, 0.6)
  assert.equal(result.steps[3].detail, '标签交叉校验')
})

test('mapBatchStatusToSteps: phase=tagging 默认 detail=素材打标与分类', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'tagging' }))
  assert.equal(result.steps[3].targetProgress, 0.85)
  assert.equal(result.steps[3].detail, '素材打标与分类')
})

test('mapBatchStatusToSteps: phase=idle 走 parsing 兜底（PHASE_TO_STEP_ID 无 idle 映射）', () => {
  // 行为契约保护：phase=idle 时 activeStepId 通过 `|| 'parsing'` 兜底，
  // 因此始终展示"步骤1 解析文档"而非"任务恢复中"。stageText='任务恢复中'
  // 分支实际为 dead code（被 parsing 兜底拦截），保留本测试锁定历史行为，
  // 后续如需修正需同步更新本契约。
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'idle', taskActive: true }))
  assert.equal(result.steps[0].status, 'processing')
  assert.equal(result.stageText, '步骤1 解析文档')
})

test('mapBatchStatusToSteps: 未知 phase 默认回落 parsing 步骤防止 UI 空白', () => {
  const result = mapBatchStatusToSteps(buildStatus({ phase: 'totally_unknown_phase' }))
  assert.equal(result.steps[0].status, 'processing')
  assert.equal(result.steps[0].targetProgress, 0.2)
})
