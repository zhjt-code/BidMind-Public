import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY,
  clearProjectListPageMemory,
  getRememberedProjectListPage,
  readProjectListPageMemory,
  resetProjectListPage,
  setRememberedProjectListPage,
} from '../src/stores/projectListPageMemory.ts'

class MemoryStorage {
  private readonly values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }
}

test('项目列表页码按四个Tab独立记忆', () => {
  const storage = new MemoryStorage()

  setRememberedProjectListPage('collusion_check', 4, { storage })
  setRememberedProjectListPage('bid_review', 3, { storage })
  setRememberedProjectListPage('bid_writing', 2, { storage })

  assert.equal(getRememberedProjectListPage('collusion_check', { storage }), 4)
  assert.equal(getRememberedProjectListPage('bid_review', { storage }), 3)
  assert.equal(getRememberedProjectListPage('bid_writing', { storage }), 2)
  assert.equal(getRememberedProjectListPage('all', { storage }), 1)
})

test('筛选类操作只重置当前Tab页码,不影响其它Tab记忆', () => {
  const storage = new MemoryStorage()

  setRememberedProjectListPage('collusion_check', 4, { storage })
  setRememberedProjectListPage('bid_review', 3, { storage })
  resetProjectListPage('collusion_check', { storage })

  assert.equal(getRememberedProjectListPage('collusion_check', { storage }), 1)
  assert.equal(getRememberedProjectListPage('bid_review', { storage }), 3)
})

test('读取页码时会夹紧到当前数据集总页数范围内', () => {
  const storage = new MemoryStorage()

  setRememberedProjectListPage('collusion_check', 99, { storage })

  assert.equal(getRememberedProjectListPage('collusion_check', { storage, totalPages: 5 }), 5)
  assert.equal(getRememberedProjectListPage('collusion_check', { storage, totalPages: 0 }), 1)
})

test('退出登录清理后四个Tab重新回到第一页', () => {
  const storage = new MemoryStorage()

  setRememberedProjectListPage('all', 2, { storage })
  setRememberedProjectListPage('collusion_check', 4, { storage })
  clearProjectListPageMemory({ storage })

  assert.equal(storage.getItem(PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY), null)
  assert.deepEqual(readProjectListPageMemory({ storage }), {
    all: 1,
    bid_writing: 1,
    bid_review: 1,
    collusion_check: 1,
  })
})

