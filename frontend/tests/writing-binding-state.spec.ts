import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyRejectedBindingDecision,
  canProceedWithoutBindings,
  composeNavigationState,
  getEmptyBindingNotice,
  getMatchStartErrorMessage,
  isBindingNotFoundError,
  isBindingRowBusy,
  removeBindingById,
  shouldShowBindingPageLoading,
} from '../src/pages/writing/writingBindingState.ts'
import { ApiRequestError } from '../src/services/apiError.ts'
import type { WritingBinding } from '../src/services/writingApi.ts'

function mkBinding(overrides: Partial<WritingBinding> & Pick<WritingBinding, 'id'>): WritingBinding {
  return {
    id: overrides.id,
    projectId: 1,
    outlineNodeId: 10,
    clauseId: null,
    materialId: 100,
    bindingType: 'material_to_node',
    confidence: 0.8,
    source: 'auto',
    status: 'suggested',
    reason: null,
    scope: null,
    ...overrides,
  } as WritingBinding
}

test('match 启动超时时应给出更具体的错误提示', () => {
  assert.equal(
    getMatchStartErrorMessage('timeout of 30000ms exceeded'),
    '启动匹配超时，请稍后重试',
  )
})

test('普通错误信息应原样透传，避免统一渲染成触发匹配失败', () => {
  assert.equal(
    getMatchStartErrorMessage('当前状态 outlined 不允许重复启动'),
    '当前状态 outlined 不允许重复启动',
  )
})

test('空绑定场景在非加载非匹配状态下允许进入内容编辑', () => {
  assert.equal(canProceedWithoutBindings({ matching: false, loading: false }), true)
  assert.equal(canProceedWithoutBindings({ matching: true, loading: false }), false)
  assert.equal(canProceedWithoutBindings({ matching: false, loading: true }), false)
})

test('空绑定提示文案应明确可先写后补素材', () => {
  assert.match(getEmptyBindingNotice(), /可直接进入内容编辑/)
  assert.match(getEmptyBindingNotice(), /后续可补充素材后重新匹配/)
})

test('确认不消费后进入内容编辑应携带coverage刷新标记', () => {
  assert.deepEqual(composeNavigationState(true), { refreshCoverage: true })
  assert.equal(composeNavigationState(false), undefined)
})

test('素材关联页初始加载时应显示整页加载态', () => {
  assert.equal(
    shouldShowBindingPageLoading({
      initialLoaded: false,
      loading: true,
      hasBindings: false,
      hasLocalAction: false,
    }),
    true,
  )
})

test('素材关联页已有绑定且局部操作中不应显示整页加载态', () => {
  assert.equal(
    shouldShowBindingPageLoading({
      initialLoaded: true,
      loading: true,
      hasBindings: true,
      hasLocalAction: true,
    }),
    false,
  )
})

test('素材关联页非局部操作的空列表加载仍保留整页加载态', () => {
  assert.equal(
    shouldShowBindingPageLoading({
      initialLoaded: true,
      loading: true,
      hasBindings: false,
      hasLocalAction: false,
    }),
    true,
  )
})

test('行级处理中状态只命中当前绑定', () => {
  const pendingAction = { bindingId: 12, action: 'confirm' as const }
  assert.equal(isBindingRowBusy(12, pendingAction), true)
  assert.equal(isBindingRowBusy(13, pendingAction), false)
  assert.equal(isBindingRowBusy(12, null), false)
})

test('拒绝 material_to_node 绑定后目标边 rejected 但同素材 suggested 自动兄弟仍保留', () => {
  // GREEN: 拒绝操作仅更新目标边状态，同素材 suggested auto 兄弟保持不变。
  // applyRejectedBindingDecision 不过滤兄弟，只将 id=1 的 status 更新为 rejected。
  const bindings = [
    mkBinding({ id: 1, materialId: 100, status: 'suggested', source: 'auto' }),
    mkBinding({ id: 2, materialId: 100, status: 'suggested', source: 'auto' }),
    mkBinding({ id: 3, materialId: 100, status: 'confirmed', source: 'auto' }),
    mkBinding({ id: 4, materialId: 100, status: 'suggested', source: 'manual' }),
    mkBinding({ id: 5, materialId: 200, status: 'suggested', source: 'auto' }),
  ]

  const result = applyRejectedBindingDecision(bindings, 1)

  // 回归保护：列表保留全部 5 条，且 sibling id=2 未被移除。
  assert.deepEqual(result.map((b) => b.id), [1, 2, 3, 4, 5])
  assert.equal(result.find((b) => b.id === 1)?.status, 'rejected')
})

test('拒绝非素材章节绑定时只更新当前绑定状态', () => {
  const bindings = [
    mkBinding({ id: 1, materialId: null, clauseId: 10, bindingType: 'clause_to_node' }),
    mkBinding({ id: 2, materialId: 100 }),
  ]

  const result = applyRejectedBindingDecision(bindings, 1)

  assert.deepEqual(result.map((b) => b.id), [1, 2])
  assert.equal(result.find((b) => b.id === 1)?.status, 'rejected')
})

test('绑定不存在时可移除本地过期残留行', () => {
  const bindings = [mkBinding({ id: 1 }), mkBinding({ id: 2 })]

  const result = removeBindingById(bindings, 2)

  assert.deepEqual(result.map((b) => b.id), [1])
})

test('绑定不存在错误应优先识别状态码，兼容后端文案', () => {
  assert.equal(isBindingNotFoundError({ statusCode: 404, message: 'Not Found' }), true)
  assert.equal(isBindingNotFoundError(new ApiRequestError('请求失败', 404)), true)
  assert.equal(isBindingNotFoundError(new ApiRequestError('绑定不存在', 500)), false)
  assert.equal(isBindingNotFoundError(new Error('绑定不存在')), true)
  assert.equal(isBindingNotFoundError(new Error('绑定已更新')), true)
  assert.equal(isBindingNotFoundError({ statusCode: 500, message: '绑定不存在' }), false)
  assert.equal(isBindingNotFoundError({ statusCode: '404', message: 'Not Found' }), false)
  assert.equal(isBindingNotFoundError(new Error('请求失败')), false)
  assert.equal(isBindingNotFoundError(null), false)
  assert.equal(isBindingNotFoundError(undefined), false)
})

