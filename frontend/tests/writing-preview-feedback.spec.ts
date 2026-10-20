import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getReadableErrorMessage,
  isStatusBlockedMessage,
  parseErrorDetailPayload,
} from '../src/pages/writing/writingPreviewFeedback.ts'

test('状态阻断文案应识别为 warning 类型', () => {
  assert.equal(isStatusBlockedMessage('当前状态 writing 不允许预览或导出'), true)
  assert.equal(isStatusBlockedMessage('预览加载失败'), false)
})

test('错误体应优先提取 detail 文案', () => {
  const payload = JSON.stringify({
    code: 400,
    message: '请求处理失败',
    detail: '当前状态 writing 不允许预览或导出',
  })

  assert.equal(parseErrorDetailPayload(payload), '当前状态 writing 不允许预览或导出')
})

test('不可读错误消息应回退到兜底文案', () => {
  assert.equal(getReadableErrorMessage(new Error('{}'), '下载失败'), '下载失败')
  assert.equal(getReadableErrorMessage(new Error('自定义错误'), '下载失败'), '自定义错误')
})
