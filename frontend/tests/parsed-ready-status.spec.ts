import assert from 'node:assert/strict'
import test from 'node:test'

import { getStatusConfig, getTargetRoute } from '../src/pages/projectList/types.ts'
import { isAwaitingExtractProjectStatus } from '../src/pages/writing/writingUploadHelpers.ts'

function project(status: string, projectType: string) {
  return {
    id: 7,
    name: '就绪态项目',
    status,
    projectType,
    createdAt: '2026-09-08T00:00:00Z',
    updatedAt: '2026-09-08T00:00:00Z',
  }
}

test('投标审查 parsed：列表待提取，点进要求页', () => {
  const cfg = getStatusConfig('parsed', 'bid_review')
  assert.equal(cfg.label, '待提取')
  assert.equal(cfg.actionLabel, '继续提取')
  assert.equal(getTargetRoute(project('parsed', 'bid_review')), '/project/7/requirements')
})

test('投标审查 parsed + express：点进进度页而不是确认页', () => {
  assert.equal(
    getTargetRoute({ ...project('parsed', 'bid_review'), runMode: 'express' }),
    '/project/7/progress',
  )
  assert.equal(
    getTargetRoute({ ...project('extracted', 'bid_review'), runMode: 'express' }),
    '/project/7/progress',
  )
})

test('标书编写 parsed：列表待提取，点进上传页等待提取', () => {
  const cfg = getStatusConfig('parsed', 'bid_writing')
  assert.equal(cfg.label, '待提取')
  assert.equal(cfg.actionLabel, '继续提取')
  assert.equal(getTargetRoute(project('parsed', 'bid_writing')), '/project/7/writing/upload')
  assert.equal(isAwaitingExtractProjectStatus('parsed'), true)
  assert.equal(isAwaitingExtractProjectStatus('uploading'), false)
})

test('串标审查 parsed：列表待分析，点进上传页开始分析', () => {
  const cfg = getStatusConfig('parsed', 'collusion_check')
  assert.equal(cfg.label, '待分析')
  assert.equal(cfg.actionLabel, '开始分析')
  assert.equal(getTargetRoute(project('parsed', 'collusion_check')), '/project/7/collusion/upload')
})

test('parsed 默认文案不是上传中', () => {
  assert.notEqual(getStatusConfig('parsed').label, '上传中')
  assert.equal(getStatusConfig('uploading').label, '上传中')
})
