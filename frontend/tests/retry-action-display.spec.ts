import assert from 'node:assert/strict'
import test from 'node:test'

import {
  UPLOAD_FAILURE_FALLBACK,
  formatFailureMessage,
  formatRetryAttempt,
  formatRetryScopeItem,
  formatRetryStageLabel,
  resolveUploadFailureMessage,
} from '../src/pages/writing/retryActionDisplay.ts'

test('formatRetryAttempt 将内部 attempt 字段转成中文尝试次数', () => {
  assert.equal(formatRetryAttempt(4), '第 4 次尝试')
  assert.equal(formatRetryAttempt(0), '')
})

test('formatRetryStageLabel 不裸出内部阶段码', () => {
  assert.equal(formatRetryStageLabel('comparing'), '重点文件对比')
  assert.equal(formatRetryStageLabel('unknown_stage'), '当前处理阶段')
})

test('formatRetryScopeItem 不裸出后端实体名和 heartbeat 等技术词', () => {
  assert.equal(formatRetryScopeItem('WritingRenderArtifact heartbeat'), '相关中间结果')
  assert.equal(formatRetryScopeItem('失败或缺失的文件对结果'), '失败或缺失的文件对结果')
})

test('formatFailureMessage 清洗自由文本技术参数', () => {
  const out = formatFailureMessage('pair failed, relevance_score=10')
  assert.ok(!out.toLowerCase().includes('relevance_score'))
  assert.ok(!out.toLowerCase().includes('failed'))
  assert.ok(out.includes('相关性评分10'))
  assert.equal(formatFailureMessage(''), '系统未返回具体原因')
})

// 文档解析各阶段此前全部落在 STAGE_LABELS 之外，一律退化为"当前处理阶段"
test('formatRetryStageLabel 覆盖写标与文档解析全部阶段码', () => {
  assert.equal(formatRetryStageLabel('parsing'), '文档解析')
  assert.equal(formatRetryStageLabel('vl_recognition'), '图片识别')
  assert.equal(formatRetryStageLabel('chunking'), '文本分块')
  assert.equal(formatRetryStageLabel('vectorizing'), '向量化')
  assert.equal(formatRetryStageLabel('queued'), '排队等待')
  assert.equal(formatRetryStageLabel('image_classify'), '图片分类')
  assert.equal(formatRetryStageLabel('extracting_hard_constraint'), '硬约束提取')
})

test('formatRetryStageLabel 对后端实际发出的 synthesizing 有映射', () => {
  assert.equal(formatRetryStageLabel('synthesizing'), '综合研判')
  assert.equal(formatRetryStageLabel('synthesis'), '综合研判')
})

test('formatRetryStageLabel 对无信息阶段返回空串以便调用方隐藏该行', () => {
  assert.equal(formatRetryStageLabel('failed'), '')
  assert.equal(formatRetryStageLabel('unknown'), '')
  assert.equal(formatRetryStageLabel(null), '')
})

// 上传页失败卡片原本硬编码文案，丢弃了 status.error_message
test('resolveUploadFailureMessage 有后端原因时直接呈现', () => {
  const reason = '未生成有效文本分块或向量，请检查文档是否为扫描件、空文件或已损坏'

  assert.equal(resolveUploadFailureMessage(reason), reason)
})

test('resolveUploadFailureMessage 仍走用户态出口闸门', () => {
  const out = resolveUploadFailureMessage('pair failed, relevance_score=10')

  assert.ok(!out.toLowerCase().includes('relevance_score'))
  assert.ok(!out.toLowerCase().includes('failed'))
})

test('resolveUploadFailureMessage 无原因时回落引导文案而非"系统未返回具体原因"', () => {
  assert.equal(resolveUploadFailureMessage(null), UPLOAD_FAILURE_FALLBACK)
  assert.equal(resolveUploadFailureMessage(''), UPLOAD_FAILURE_FALLBACK)
  assert.equal(resolveUploadFailureMessage('   '), UPLOAD_FAILURE_FALLBACK)
})
