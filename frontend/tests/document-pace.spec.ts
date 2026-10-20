import assert from 'node:assert/strict'
import test from 'node:test'

import {
  documentPaceFromSkipVl,
  skipVlFromDocumentPace,
  IMAGE_PACE_LABEL,
  TENDER_IMAGE_PACE_HINT,
  BID_IMAGE_PACE_HINT,
} from '../src/components/upload/documentPace.ts'

test('skip_vl=true 映射为加速（招标默认）', () => {
  assert.equal(documentPaceFromSkipVl(true), 'fast')
  assert.equal(skipVlFromDocumentPace('fast'), true)
})

test('skip_vl=false 映射为完整识别（同形态：投标侧默认不跳过）', () => {
  assert.equal(documentPaceFromSkipVl(false), 'full')
  assert.equal(skipVlFromDocumentPace('full'), false)
})

test('三页共用图片识别文案：区块内只写图片识别', () => {
  assert.equal(IMAGE_PACE_LABEL, '图片识别')
  assert.match(TENDER_IMAGE_PACE_HINT, /扫描件/)
  assert.match(BID_IMAGE_PACE_HINT, /证书/)
})
