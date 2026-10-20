import assert from 'node:assert/strict'
import test from 'node:test'

import { transformRetryState } from '../src/services/writingRetryTransforms.ts'
import {
  getWritingFailedNodeRoute,
  isActionableRetryState,
} from '../src/pages/writing/writingFailureRecovery.ts'
import { getStatusConfig } from '../src/pages/projectList/types.ts'

test('transformRetryState 映射显式重试字段', () => {
  const state = transformRetryState({
    retryable: true,
    failed_node: 'writing_material_process',
    target_id: null,
    failed_stage: 'image_classify',
    retry_action_label: '从素材处理继续重试',
    preserved_outputs: ['已上传素材文件', '已确认目录'],
    reset_scope: ['失败素材'],
    last_attempt: 3,
    retry_running: false,
    last_error: 'boom',
  })

  assert.equal(state.retryable, true)
  assert.equal(state.failedNode, 'writing_material_process')
  assert.equal(state.targetId, null)
  assert.equal(state.failedStage, 'image_classify')
  assert.equal(state.retryActionLabel, '从素材处理继续重试')
  assert.deepEqual(state.preservedOutputs, ['已上传素材文件', '已确认目录'])
  assert.deepEqual(state.resetScope, ['失败素材'])
  assert.equal(state.lastAttempt, 3)
  assert.equal(state.retryRunning, false)
  assert.equal(state.lastError, 'boom')
})

test('transformRetryState 缺省时禁止重试并返回空数组', () => {
  const state = transformRetryState({})

  assert.equal(state.retryable, false)
  assert.equal(state.failedNode, null)
  assert.equal(state.targetId, null)
  assert.equal(state.failedStage, null)
  assert.equal(state.preservedOutputs.length, 0)
  assert.equal(state.resetScope.length, 0)
  assert.equal(state.lastAttempt, 0)
  assert.equal(state.retryRunning, false)
  assert.equal(state.lastError, null)
})

test('transformRetryState 映射细粒度章节目标', () => {
  const state = transformRetryState({
    retryable: true,
    failed_node: 'writing_compose_single',
    target_id: 99,
    retry_action_label: '重新生成当前章节',
  })

  assert.equal(state.failedNode, 'writing_compose_single')
  assert.equal(state.targetId, 99)
  assert.equal(state.retryActionLabel, '重新生成当前章节')
})

test('写标失败节点应路由到对应恢复页面', () => {
  assert.equal(getWritingFailedNodeRoute(7, 'writing_extract'), '/project/7/writing/upload')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_tender_process'), '/project/7/writing/upload')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_outline'), '/project/7/writing/outline')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_material_process'), '/project/7/writing/materials')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_match'), '/project/7/writing/binding')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_compose'), '/project/7/writing/compose')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_compose_single'), '/project/7/writing/compose')
  assert.equal(getWritingFailedNodeRoute(7, 'writing_render'), '/project/7/writing/preview')
})

test('失败项目按钮文案应提示恢复入口', () => {
  assert.equal(getStatusConfig('failed').actionLabel, '查看失败/恢复')
})

test('isActionableRetryState 识别可展示恢复面板的失败态', () => {
  assert.equal(isActionableRetryState(null), false)
  assert.equal(isActionableRetryState(transformRetryState({})), false)
  assert.equal(isActionableRetryState(transformRetryState({ failed_node: 'review_project' })), true)
  assert.equal(isActionableRetryState(transformRetryState({ failed_node: 'review_compare' })), true)
  assert.equal(isActionableRetryState(transformRetryState({ failed_node: 'collusion_compare' })), true)
  assert.equal(isActionableRetryState(transformRetryState({ retryable: true })), true)
})
