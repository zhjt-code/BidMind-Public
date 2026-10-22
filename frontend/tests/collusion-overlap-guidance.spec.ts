import assert from 'node:assert/strict'
import test from 'node:test'

import { OVERLAP_GUIDANCE_TEXT, hasNavigableOverlapDetail } from '../src/pages/collusionResult/overlapGuidance.ts'

test('存在正式证据(findings)时应展示引导', () => {
  const pairs = [{ findings: [{}], evidenceChunks: [] }]
  assert.equal(hasNavigableOverlapDetail(pairs), true)
})

test('仅有高相似重合片段(evidenceChunks)时应展示引导', () => {
  const pairs = [{ findings: [], evidenceChunks: [{}] }]
  assert.equal(hasNavigableOverlapDetail(pairs), true)
})

test('findings 与 evidenceChunks 均为空/缺失时不展示引导', () => {
  assert.equal(hasNavigableOverlapDetail([{ findings: [], evidenceChunks: [] }]), false)
  assert.equal(hasNavigableOverlapDetail([{}]), false)
  assert.equal(hasNavigableOverlapDetail([]), false)
})

test('多对中任一对有内容即展示引导', () => {
  const pairs = [
    { findings: [], evidenceChunks: [] },
    { findings: [], evidenceChunks: [{}] },
  ]
  assert.equal(hasNavigableOverlapDetail(pairs), true)
})

test('引导文案应指向右侧详情面板的重合内容', () => {
  assert.match(OVERLAP_GUIDANCE_TEXT, /右侧详情面板/)
  assert.match(OVERLAP_GUIDANCE_TEXT, /重合/)
})
