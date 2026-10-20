/**
 * writingUploadHelpers.ts 纯函数边界测试。
 *
 * 抽自 WritingUpload.tsx 内部判定，按 R2-9 §1.1 决议（Q1=B）仅做辅助函数
 * 迁出，pollUploadStatus 主体保留在 WritingUpload，零行为变化。
 *
 * 覆盖（11 条）：
 *  - isWritingTenderProcessStage 5 个命中 + null/未知 兜底
 *  - isWritingDoneProjectStatus 全 9 状态命中 + 兜底
 *  - isResumableUploadProjectStatus uploading/parsing 命中
 *  - shouldAutoTriggerExtract 4 条主路径
 *  - isPollUploadExtracted / isPollUploadFailed 终态判定
 *  - isWritingUploadChooserStage 仅 idle 展示
 *  - 常量数组导出契约
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isWritingTenderProcessStage,
  isWritingDoneProjectStatus,
  isResumableUploadProjectStatus,
  shouldAutoTriggerExtract,
  isPollUploadExtracted,
  isPollUploadFailed,
  isWritingUploadChooserStage,
  WRITING_TENDER_PROCESS_STAGES,
  WRITING_DONE_PROJECT_STATUSES,
  RESUME_UPLOAD_PROJECT_STATUSES,
} from '../src/pages/writing/writingUploadHelpers.ts'

// isWritingTenderProcessStage ----------------------------------------------

test('isWritingTenderProcessStage: 5 个 stage 全部命中', () => {
  for (const stage of WRITING_TENDER_PROCESS_STAGES) {
    assert.equal(isWritingTenderProcessStage(stage), true, `${stage} should hit`)
  }
})

test('isWritingTenderProcessStage: null/undefined/空字符串/未知 stage 不命中', () => {
  assert.equal(isWritingTenderProcessStage(null), false)
  assert.equal(isWritingTenderProcessStage(undefined), false)
  assert.equal(isWritingTenderProcessStage(''), false)
  assert.equal(isWritingTenderProcessStage('unknown_stage'), false)
  assert.equal(isWritingTenderProcessStage('extracting'), false)
})

// isWritingDoneProjectStatus -----------------------------------------------

test('isWritingDoneProjectStatus: 9 个状态全部命中', () => {
  for (const status of WRITING_DONE_PROJECT_STATUSES) {
    assert.equal(isWritingDoneProjectStatus(status), true, `${status} should hit`)
  }
})

test('isWritingDoneProjectStatus: 上传/解析/失败/null 不命中', () => {
  assert.equal(isWritingDoneProjectStatus('uploading'), false)
  assert.equal(isWritingDoneProjectStatus('parsing'), false)
  assert.equal(isWritingDoneProjectStatus('extracting'), false)
  assert.equal(isWritingDoneProjectStatus('failed'), false)
  assert.equal(isWritingDoneProjectStatus(null), false)
})

// isResumableUploadProjectStatus -------------------------------------------

test('isResumableUploadProjectStatus: uploading + parsing 命中', () => {
  assert.equal(isResumableUploadProjectStatus('uploading'), true)
  assert.equal(isResumableUploadProjectStatus('parsing'), true)
  assert.equal(isResumableUploadProjectStatus('extracting'), false)
  assert.equal(isResumableUploadProjectStatus('extracted'), false)
  assert.equal(isResumableUploadProjectStatus(null), false)
})

// shouldAutoTriggerExtract -------------------------------------------------

test('shouldAutoTriggerExtract: status=uploading + currentStage=doc_completed → true', () => {
  assert.equal(
    shouldAutoTriggerExtract({ status: 'uploading', currentStage: 'doc_completed', overallProgress: 30 }),
    true,
  )
})

test('shouldAutoTriggerExtract: status=uploading + overallProgress=50 → true', () => {
  assert.equal(
    shouldAutoTriggerExtract({ status: 'uploading', currentStage: 'parsing', overallProgress: 50 }),
    true,
  )
  assert.equal(
    shouldAutoTriggerExtract({ status: 'uploading', currentStage: 'parsing', overallProgress: 100 }),
    true,
  )
})

test('shouldAutoTriggerExtract: status=uploading + progress<50 + stage≠doc_completed → false', () => {
  assert.equal(
    shouldAutoTriggerExtract({ status: 'uploading', currentStage: 'parsing', overallProgress: 49 }),
    false,
  )
  assert.equal(
    shouldAutoTriggerExtract({ status: 'uploading', currentStage: 'vl_recognition' }),
    false,
  )
})

test('shouldAutoTriggerExtract: status=parsed 视为文档就绪', () => {
  assert.equal(
    shouldAutoTriggerExtract({ status: 'parsed', currentStage: 'doc_completed' }),
    true,
  )
  assert.equal(
    shouldAutoTriggerExtract({ status: 'parsed' }, true),
    false,
  )
})

test('shouldAutoTriggerExtract: 提取已启动或完成后不再自动触发', () => {
  assert.equal(
    shouldAutoTriggerExtract({ status: 'extracting', currentStage: 'doc_completed', overallProgress: 80 }),
    false,
  )
  assert.equal(
    shouldAutoTriggerExtract({ status: 'extracted' }),
    false,
  )
})

// isPollUploadExtracted ----------------------------------------------------

test('isPollUploadExtracted: 仅 status=extracted 时 true', () => {
  assert.equal(isPollUploadExtracted({ status: 'extracted' }), true)
  assert.equal(isPollUploadExtracted({ status: 'uploading' }), false)
  assert.equal(isPollUploadExtracted({ status: 'failed' }), false)
  assert.equal(isPollUploadExtracted({}), false)
})

// isPollUploadFailed -------------------------------------------------------

test('isPollUploadFailed: status=failed → true', () => {
  assert.equal(isPollUploadFailed({ status: 'failed' }), true)
})

test('isPollUploadFailed: overallStatus=failed → true（兼容 status 未推进的场景）', () => {
  assert.equal(isPollUploadFailed({ status: 'uploading', overallStatus: 'failed' }), true)
})

test('isPollUploadFailed: currentStage=failed → true（兼容 step 级失败）', () => {
  assert.equal(isPollUploadFailed({ status: 'uploading', currentStage: 'failed' }), true)
})

test('isPollUploadFailed: 三者都非 failed → false', () => {
  assert.equal(isPollUploadFailed({ status: 'uploading', overallStatus: 'running', currentStage: 'parsing' }), false)
  assert.equal(isPollUploadFailed({}), false)
})

// 常量数组契约 --------------------------------------------------------------

test('isWritingUploadChooserStage: 仅 idle 为选择态，处理进度与完成回看隐藏', () => {
  assert.equal(isWritingUploadChooserStage('idle'), true)
  for (const stage of [
    'uploading',
    'processing',
    'extracting',
    'awaiting_extract',
    'done',
    'error',
    'already_done',
  ] as const) {
    assert.equal(isWritingUploadChooserStage(stage), false, `${stage} should hide chooser`)
  }
})

test('常量数组契约: 三个 readonly 数组按规范导出', () => {
  assert.deepEqual([...WRITING_TENDER_PROCESS_STAGES], [
    'parsing', 'vl_recognition', 'chunking', 'vectorizing', 'doc_completed',
  ])
  assert.deepEqual([...WRITING_DONE_PROJECT_STATUSES], [
    'extracted', 'outlining', 'outlined', 'matching', 'matched',
    'writing', 'composed', 'formatting', 'completed',
  ])
  assert.deepEqual([...RESUME_UPLOAD_PROJECT_STATUSES], ['uploading', 'parsing'])
})
