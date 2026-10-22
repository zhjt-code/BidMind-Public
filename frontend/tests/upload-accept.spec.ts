import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ALLOWED_UPLOAD_SUFFIXES,
  OFFICE_CONVERT_DURATION_HINT,
  UPLOAD_ACCEPT,
  UPLOAD_FORMAT_HINT,
  isAllowedUploadFile,
} from '../src/lib/uploadAccept.ts'

test('三大流程上传后缀：pdf/doc/docx 均通过', () => {
  assert.equal(isAllowedUploadFile('tender.pdf'), true)
  assert.equal(isAllowedUploadFile('投标.DOC'), true)
  assert.equal(isAllowedUploadFile('招标.Docx'), true)
})

test('拒绝非白名单实体 txt 与宏文档 docm', () => {
  assert.equal(isAllowedUploadFile('notes.txt'), false)
  assert.equal(isAllowedUploadFile('macro.docm'), false)
})

test('accept 常量与后缀表同源', () => {
  assert.equal(UPLOAD_ACCEPT, '.pdf,.doc,.docx')
  assert.deepEqual([...ALLOWED_UPLOAD_SUFFIXES], ['.pdf', '.doc', '.docx'])
})

test('槽位格式提示只声明格式且不含转换耗时', () => {
  assert.match(OFFICE_CONVERT_DURATION_HINT, /3 分钟/)
  assert.match(UPLOAD_FORMAT_HINT, /PDF/)
  assert.match(UPLOAD_FORMAT_HINT, /Word/)
  assert.doesNotMatch(UPLOAD_FORMAT_HINT, /3 分钟/)
  assert.doesNotMatch(UPLOAD_FORMAT_HINT, /100MB/)
})
