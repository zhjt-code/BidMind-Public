import assert from 'node:assert/strict'
import test from 'node:test'

/**
 * messageStore 纯逻辑单测
 *
 * 验证 store 初始状态与 reset 行为。
 * 网络相关功能（refreshUnread / markRead）依赖 api mock，
 * 在集成/E2E 层覆盖。
 */

test('useMessageStore 初始状态应为零未读、空列表', async () => {
  const { useMessageStore } = await import('../src/stores/messageStore.ts')
  const state = useMessageStore.getState()

  assert.equal(state.unreadCount, 0)
  assert.deepEqual(state.recent, [])
})

test('reset 应将状态归零', async () => {
  const { useMessageStore } = await import('../src/stores/messageStore.ts')

  useMessageStore.setState({ unreadCount: 5, recent: [] })
  assert.equal(useMessageStore.getState().unreadCount, 5)

  useMessageStore.getState().reset()
  assert.equal(useMessageStore.getState().unreadCount, 0)
  assert.deepEqual(useMessageStore.getState().recent, [])
})
