/**
 * collusionUploadParts/skipVlState.ts 防御测试。
 *
 * 对应 ：串标上传页"跳过图片识别"复选框取消后，组件因自动 navigate
 * 到进度页后再返回（或刷新、或路由再次匹配）时被 useState 默认值覆盖。
 *
 * 测试要点：
 * 1. 未持久化时 read 返回 null（区别于布尔值，避免吞没"用户未表态"语义）
 * 2. tender/bid 两类 true/false 都能正确持久化与读取
 * 3. clear 后回到未持久化状态
 * 4. projectId + 文件类型隔离（projectA 的设置不会影响 projectB，tender 不影响 bid）
 * 5. 非法 projectId（NaN/0/负数）防御性 no-op
 * 6. 关键场景：组件重挂载后能分别恢复招标/投标偏好（默认：招标 true / 投标 false）
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clearPersistedCollusionSkipVl,
  getCollusionSkipVlStorageKey,
  persistCollusionSkipVl,
  readPersistedCollusionSkipVl,
} from '../src/pages/collusionUploadParts/skipVlState.ts'

function installSessionStorageMock() {
  const store = new Map<string, string>()
  const originalWindow = (globalThis as { window?: unknown }).window
  ;(globalThis as { window?: { sessionStorage: Storage } }).window = {
    sessionStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, value) },
      removeItem: (key: string) => { store.delete(key) },
      clear: () => { store.clear() },
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      get length() { return store.size },
    } as Storage,
  }

  return () => {
    if (originalWindow === undefined) {
      delete (globalThis as { window?: unknown }).window
      return
    }
    ;(globalThis as { window?: unknown }).window = originalWindow
  }
}

test('storage key 按 projectId 和文件类型隔离生成', () => {
  assert.equal(getCollusionSkipVlStorageKey(1399, 'tender'), 'collusion-upload-skip-vl:1399:tender')
  assert.equal(getCollusionSkipVlStorageKey(1399, 'bid'), 'collusion-upload-skip-vl:1399:bid')
  assert.notEqual(
    getCollusionSkipVlStorageKey(1, 'bid'),
    getCollusionSkipVlStorageKey(2, 'bid'),
  )
  assert.notEqual(
    getCollusionSkipVlStorageKey(1399, 'tender'),
    getCollusionSkipVlStorageKey(1399, 'bid'),
  )
})

test('未持久化时 read 返回 null（区别于布尔默认值）', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    assert.equal(readPersistedCollusionSkipVl(1399, 'tender'), null)
    assert.equal(readPersistedCollusionSkipVl(1399, 'bid'), null)
  } finally {
    restoreWindow()
  }
})

test('tender/bid true/false 可正确持久化并读回，clear 后回到 null', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistCollusionSkipVl(1399, 'tender', true)
    assert.equal(readPersistedCollusionSkipVl(1399, 'tender'), true)

    persistCollusionSkipVl(1399, 'bid', false)
    assert.equal(readPersistedCollusionSkipVl(1399, 'bid'), false)

    clearPersistedCollusionSkipVl(1399, 'tender')
    assert.equal(readPersistedCollusionSkipVl(1399, 'tender'), null)
    assert.equal(readPersistedCollusionSkipVl(1399, 'bid'), false)
  } finally {
    restoreWindow()
  }
})

test('projectId 和文件类型隔离：A 的设置不影响 B，tender 不影响 bid', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistCollusionSkipVl(101, 'tender', true)
    persistCollusionSkipVl(101, 'bid', false)
    persistCollusionSkipVl(202, 'bid', true)

    assert.equal(readPersistedCollusionSkipVl(101, 'tender'), true)
    assert.equal(readPersistedCollusionSkipVl(101, 'bid'), false)
    assert.equal(readPersistedCollusionSkipVl(202, 'bid'), true)

    clearPersistedCollusionSkipVl(101, 'tender')
    assert.equal(readPersistedCollusionSkipVl(101, 'tender'), null)
    assert.equal(readPersistedCollusionSkipVl(101, 'bid'), false)
    assert.equal(readPersistedCollusionSkipVl(202, 'bid'), true)
  } finally {
    restoreWindow()
  }
})

test('非法 projectId（NaN）防御性 no-op，不抛错', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistCollusionSkipVl(Number.NaN, 'bid', true)
    assert.equal(readPersistedCollusionSkipVl(Number.NaN, 'bid'), null)
    clearPersistedCollusionSkipVl(Number.NaN, 'bid')
  } finally {
    restoreWindow()
  }
})

test(' 核心场景：模拟组件重挂载，应分别恢复招标和投标偏好', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    const projectId = 1399

    // 模拟首次挂载：未持久化 → 招标默认跳过、投标默认不跳过（与 CollusionUpload 对齐）
    let mountedTenderSkipVl = readPersistedCollusionSkipVl(projectId, 'tender') ?? true
    let mountedBidSkipVl = readPersistedCollusionSkipVl(projectId, 'bid') ?? false
    assert.equal(mountedTenderSkipVl, true)
    assert.equal(mountedBidSkipVl, false)

    // 用户分别调整两个卡片内的偏好（招标取消勾选、投标保持不跳过）
    persistCollusionSkipVl(projectId, 'tender', false)
    persistCollusionSkipVl(projectId, 'bid', false)

    // 模拟组件因自动 navigate 后被卸载，用户返回上传页 → 重新挂载
    // 此时两个 useState lazy initializer 必须分别恢复各自偏好
    mountedTenderSkipVl = readPersistedCollusionSkipVl(projectId, 'tender') ?? true
    mountedBidSkipVl = readPersistedCollusionSkipVl(projectId, 'bid') ?? false
    assert.equal(mountedTenderSkipVl, false, '招标文件偏好必须在组件重挂载后保持')
    assert.equal(mountedBidSkipVl, false, '投标文件偏好必须在组件重挂载后保持')
  } finally {
    restoreWindow()
  }
})
