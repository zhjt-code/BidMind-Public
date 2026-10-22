/**
 * fileUploadParts/skipVlState.ts 防御测试。
 *
 * 对应 同源扩散：投标审查上传页"跳过图片识别"复选框取消后，
 * 组件因自动 navigate 到 /requirements 后返回上传页时被 useState(true)
 * 默认值覆盖。
 *
 * 与 collusion-skip-vl-state.spec.ts 同源覆盖：
 * 1. 未持久化时 read 返回 null
 * 2. true/false 都能正确持久化与读取
 * 3. clear 后回到未持久化状态
 * 4. projectId 隔离
 * 5. 非法 projectId 防御性 no-op
 * 6. 同源核心场景：取消勾选后模拟组件重挂载，应恢复 false
 * 7. 三流程 sessionStorage key 互不冲突（防 collusion/writing 串扰）
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clearPersistedFileUploadSkipVl,
  getFileUploadSkipVlStorageKey,
  persistFileUploadSkipVl,
  readPersistedFileUploadSkipVl,
} from '../src/pages/fileUploadParts/skipVlState.ts'
import {
  getCollusionSkipVlStorageKey,
} from '../src/pages/collusionUploadParts/skipVlState.ts'
import {
  getWritingSkipVlStorageKey,
} from '../src/pages/writing/writingUploadVlState.ts'

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

test('storage key 按 projectId 隔离生成', () => {
  assert.equal(getFileUploadSkipVlStorageKey(1399), 'file-upload-skip-vl:1399')
  assert.notEqual(
    getFileUploadSkipVlStorageKey(1),
    getFileUploadSkipVlStorageKey(2),
  )
})

test('未持久化时 read 返回 null（区别于布尔默认值）', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    assert.equal(readPersistedFileUploadSkipVl(1399), null)
  } finally {
    restoreWindow()
  }
})

test('true/false 可正确持久化并读回，clear 后回到 null', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistFileUploadSkipVl(1399, true)
    assert.equal(readPersistedFileUploadSkipVl(1399), true)

    persistFileUploadSkipVl(1399, false)
    assert.equal(readPersistedFileUploadSkipVl(1399), false)

    clearPersistedFileUploadSkipVl(1399)
    assert.equal(readPersistedFileUploadSkipVl(1399), null)
  } finally {
    restoreWindow()
  }
})

test('projectId 隔离：A 的设置不影响 B', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistFileUploadSkipVl(101, true)
    persistFileUploadSkipVl(202, false)

    assert.equal(readPersistedFileUploadSkipVl(101), true)
    assert.equal(readPersistedFileUploadSkipVl(202), false)

    clearPersistedFileUploadSkipVl(101)
    assert.equal(readPersistedFileUploadSkipVl(101), null)
    assert.equal(readPersistedFileUploadSkipVl(202), false)
  } finally {
    restoreWindow()
  }
})

test('非法 projectId（NaN）防御性 no-op，不抛错', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    persistFileUploadSkipVl(Number.NaN, true)
    assert.equal(readPersistedFileUploadSkipVl(Number.NaN), null)
    clearPersistedFileUploadSkipVl(Number.NaN)
  } finally {
    restoreWindow()
  }
})

test(' 同源场景：取消勾选后模拟组件重挂载，应恢复 false 而非默认 true', () => {
  const restoreWindow = installSessionStorageMock()
  try {
    const projectId = 1399

    let mountedSkipVl = readPersistedFileUploadSkipVl(projectId) ?? true
    assert.equal(mountedSkipVl, true)

    persistFileUploadSkipVl(projectId, false)

    mountedSkipVl = readPersistedFileUploadSkipVl(projectId) ?? true
    assert.equal(mountedSkipVl, false, '用户取消的勾选必须在组件重挂载后保持取消')
  } finally {
    restoreWindow()
  }
})

test('三流程 sessionStorage key 互不冲突（防止跨流程串扰）', () => {
  const pid = 1399
  const keys = new Set([
    getFileUploadSkipVlStorageKey(pid),
    getCollusionSkipVlStorageKey(pid, 'bid'),
    getWritingSkipVlStorageKey(pid),
  ])
  assert.equal(keys.size, 3, '三个流程的 sessionStorage key 必须互相隔离')
})
