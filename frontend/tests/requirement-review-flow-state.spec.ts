/**
 * 防御测试：投标审查已发起后，招标要求确认页不得回退到可重复启动入口。
 *
 * 职责: 锁定 RequirementReview 页头主按钮由服务端项目状态派生。
 * 不做: 不挂载 React、不调用真实 API。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getRequirementReviewPrimaryAction,
} from '../src/pages/RequirementReview/reviewFlowState.ts'

test('reviewing 状态应进入进度页下一步，不再显示确认启动', () => {
  const action = getRequirementReviewPrimaryAction(770, 'reviewing')

  assert.equal(action.kind, 'progress')
  assert.equal(action.label, '下一步')
  assert.equal(action.targetPath, '/project/770/progress')
})

test('completed 状态应进入结果页下一步，不再重新启动审查', () => {
  const action = getRequirementReviewPrimaryAction('770', 'completed')

  assert.equal(action.kind, 'result')
  assert.equal(action.label, '下一步')
  assert.equal(action.targetPath, '/project/770/result')
})

test('failed 状态应回到进度页查看失败与恢复入口', () => {
  const action = getRequirementReviewPrimaryAction(770, 'failed')

  assert.equal(action.kind, 'progress')
  assert.equal(action.label, '下一步')
  assert.equal(action.targetPath, '/project/770/progress')
})

test('重新提取后的 extracted 状态仍应显示确认启动入口', () => {
  const action = getRequirementReviewPrimaryAction(770, 'extracted')

  assert.equal(action.kind, 'confirm')
  assert.equal(action.label, '确认并开始审查')
  assert.equal(action.targetPath, null)
})
