import assert from 'node:assert/strict'
import test from 'node:test'
import { getStatusConfig, getTargetRoute } from '../src/pages/projectList/types.ts'
import { getWritingProjectRoute, WRITING_PAGE_MIN_STATUS } from '../src/pages/writing/writingConstants.ts'

const baseProject = {
  id: 42,
  name: '测试项目',
  status: 'created',
  projectType: 'bid_writing',
  createdAt: '2026-04-01T00:00:00Z',
  updatedAt: '2026-04-01T00:00:00Z',
}

test('composed 状态应统一指向预览导出语义', () => {
  assert.equal(getWritingProjectRoute(42, 'composed'), '/project/42/writing/preview')
  assert.equal(getTargetRoute({ ...baseProject, status: 'composed' }), '/project/42/writing/preview')

  const composedConfig = getStatusConfig('composed')
  assert.equal(composedConfig.label, '预览就绪')
  assert.equal(composedConfig.actionLabel, '预览导出')
})

test('关键边界状态仍保持原有落点', () => {
  assert.equal(getWritingProjectRoute(42, 'extracted'), '/project/42/writing/clauses')
  assert.equal(getWritingProjectRoute(42, 'matched'), '/project/42/writing/binding')
  assert.equal(getWritingProjectRoute(42, 'writing'), '/project/42/writing/compose')
  assert.equal(getWritingProjectRoute(42, 'completed'), '/project/42/writing/preview')

  assert.equal(getTargetRoute({ ...baseProject, status: 'writing' }), '/project/42/writing/compose')
  assert.equal(getTargetRoute({ ...baseProject, status: 'completed' }), '/project/42/writing/preview')
})

test('parsed 就绪态指向上传页等待提取，列表文案不是上传中', () => {
  assert.equal(getWritingProjectRoute(42, 'parsed'), '/project/42/writing/upload')
  assert.equal(getTargetRoute({ ...baseProject, status: 'parsed' }), '/project/42/writing/upload')
  const parsedConfig = getStatusConfig('parsed', 'bid_writing')
  assert.equal(parsedConfig.label, '待提取')
  assert.equal(parsedConfig.actionLabel, '继续提取')
})

test('express 在确认闸阶段改走编写进度，guided 落点不变', () => {
  assert.equal(getWritingProjectRoute(42, 'extracted', 'express'), '/project/42/writing/progress')
  assert.equal(getWritingProjectRoute(42, 'matched', 'express'), '/project/42/writing/progress')
  assert.equal(getWritingProjectRoute(42, 'writing', 'express'), '/project/42/writing/progress')
  assert.equal(getWritingProjectRoute(42, 'composed', 'express'), '/project/42/writing/preview')
  assert.equal(getTargetRoute({ ...baseProject, status: 'extracted', runMode: 'express' }), '/project/42/writing/progress')
  assert.equal(getTargetRoute({ ...baseProject, status: 'extracted' }), '/project/42/writing/clauses')
})

test('素材与绑定页面共享 outlined 准入边界', () => {
  assert.equal(WRITING_PAGE_MIN_STATUS.materials, 'outlined')
  assert.equal(WRITING_PAGE_MIN_STATUS.binding, 'outlined')
  assert.equal(getWritingProjectRoute(42, 'outlining'), '/project/42/writing/outline')
})
