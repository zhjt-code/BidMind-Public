import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DOCUMENT_PROCESS_FAILURE_FALLBACK,
  OFFICE_CONVERT_TIMEOUT_MESSAGE,
  resolveDocumentProcessFailureMessage,
} from '../src/lib/formatFailureMessage.ts'
import {
  toExistingDocumentFailure,
} from '../src/pages/fileUploadParts/existingDocFailure.ts'
import { canSubmitReplaceUpload } from '../src/pages/fileUploadParts/replaceUploadGate.ts'

test('resolveDocumentProcessFailureMessage 透传后端可读原因', () => {
  const msg = '文档不可用: PDF所有页面均无文本、表格与图片，请更换文件后重试'
  assert.equal(resolveDocumentProcessFailureMessage(msg), msg)
})

test('resolveDocumentProcessFailureMessage 翻译历史转换 HTTP 503', () => {
  assert.equal(
    resolveDocumentProcessFailureMessage('Word 文档转换失败: HTTP 503'),
    OFFICE_CONVERT_TIMEOUT_MESSAGE,
  )
  assert.doesNotMatch(
    resolveDocumentProcessFailureMessage('Word 文档转换失败: HTTP 503'),
    /HTTP 503/,
  )
})

test('resolveDocumentProcessFailureMessage 不把其它 HTTP 失败收成超时', () => {
  const msg = 'Word 文档转换失败: HTTP 500'
  assert.equal(resolveDocumentProcessFailureMessage(msg), msg)
})

test('resolveDocumentProcessFailureMessage 空原因回落更换引导', () => {
  assert.equal(resolveDocumentProcessFailureMessage(''), DOCUMENT_PROCESS_FAILURE_FALLBACK)
  assert.equal(resolveDocumentProcessFailureMessage(null), DOCUMENT_PROCESS_FAILURE_FALLBACK)
})

test('toExistingDocumentFailure 写入 parsed=-1 与清洗后原因', () => {
  const patch = toExistingDocumentFailure('文档不可用: PDF损坏')
  assert.equal(patch.parsed, -1)
  assert.match(patch.lastError, /PDF损坏|文档不可用/)
})

test('canSubmitReplaceUpload 仅在更换槽位有 pending 文件时可提交', () => {
  assert.equal(
    canSubmitReplaceUpload('tender', { status: 'pending' }, null, false),
    true,
  )
  assert.equal(
    canSubmitReplaceUpload('tender', { status: 'processing' }, null, false),
    false,
  )
  assert.equal(
    canSubmitReplaceUpload(null, { status: 'pending' }, null, false),
    false,
  )
})
