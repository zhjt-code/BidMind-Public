import assert from 'node:assert/strict'
import test from 'node:test'

import { isFirstComposeInteractionLocked } from '../src/pages/writing/writingComposeLock.ts'

// 目录结构: 1(父) -> 2,3(叶); 4(独立叶)
const outline = [
  { id: 1, parentId: null },
  { id: 2, parentId: 1 },
  { id: 3, parentId: 1 },
  { id: 4, parentId: null },
]

test('未在生成中时不锁定', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: false, outlineNodes: outline, draftedNodeIds: [] }),
    false,
  )
})

test('首次生成会话内(尚无草稿)锁定', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: true, outlineNodes: outline, draftedNodeIds: [] }),
    true,
  )
})

test('生成中途往返页面后部分草稿已入库仍必须锁定', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: true, outlineNodes: outline, draftedNodeIds: [2] }),
    true,
  )
})

test('全部叶子章节已有草稿(重新生成全文)不锁定', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: true, outlineNodes: outline, draftedNodeIds: [2, 3, 4] }),
    false,
  )
})

test('仅父节点有草稿不视为生成完成', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: true, outlineNodes: outline, draftedNodeIds: [1] }),
    true,
  )
})

test('目录尚未加载时生成中保守锁定', () => {
  assert.equal(
    isFirstComposeInteractionLocked({ composing: true, outlineNodes: [], draftedNodeIds: [] }),
    true,
  )
})
