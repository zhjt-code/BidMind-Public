import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

import {
  FEEDBACK_FAB_DISMISSED_KEY,
  FEEDBACK_FAB_VISIBILITY_EVENT,
  dismissFeedbackFab,
  isFeedbackFabVisible,
  resolveBackToTopBottomClass,
} from '../src/lib/feedbackFabVisibility.ts'

function installWindowSessionMock() {
  const store = new Map<string, string>()
  const listeners = new Map<string, Set<EventListener>>()
  const originalWindow = (globalThis as { window?: unknown }).window

  const sessionStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value)
    },
    removeItem: (key: string) => {
      store.delete(key)
    },
    clear: () => {
      store.clear()
    },
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size
    },
  } as Storage

  ;(globalThis as { window?: unknown }).window = {
    sessionStorage,
    addEventListener: (type: string, listener: EventListener) => {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)!.add(listener)
    },
    removeEventListener: (type: string, listener: EventListener) => {
      listeners.get(type)?.delete(listener)
    },
    dispatchEvent: (event: Event) => {
      for (const listener of listeners.get(event.type) ?? []) {
        listener(event)
      }
      return true
    },
  }

  return {
    sessionStorage,
    restore: () => {
      if (originalWindow === undefined) {
        delete (globalThis as { window?: unknown }).window
        return
      }
      ;(globalThis as { window?: unknown }).window = originalWindow
    },
  }
}

test('反馈 FAB 占位时回到顶部抬高，关闭后落回底部槽位', () => {
  assert.equal(resolveBackToTopBottomClass(true), 'bottom-24')
  assert.equal(resolveBackToTopBottomClass(false), 'bottom-6')
})

test('dismissFeedbackFab 写入会话标记并广播显隐事件', () => {
  const { sessionStorage, restore } = installWindowSessionMock()
  try {
    assert.equal(isFeedbackFabVisible(), true)

    let eventCount = 0
    window.addEventListener(FEEDBACK_FAB_VISIBILITY_EVENT, () => {
      eventCount += 1
    })

    dismissFeedbackFab()
    assert.equal(sessionStorage.getItem(FEEDBACK_FAB_DISMISSED_KEY), '1')
    assert.equal(isFeedbackFabVisible(), false)
    assert.equal(eventCount, 1)
  } finally {
    restore()
  }
})

test('BackToTop 应消费反馈 FAB 显隐契约，禁止写死 bottom-24', () => {
  const source = readFileSync(resolve('src/components/BackToTop.tsx'), 'utf8')
  assert.match(source, /resolveBackToTopBottomClass/)
  assert.match(source, /FEEDBACK_FAB_VISIBILITY_EVENT/)
  assert.doesNotMatch(source, /fixed bottom-24 right-6/)
})

test('FeedbackPanel 关闭 FAB 应走 dismissFeedbackFab，禁止私写 sessionStorage', () => {
  const source = readFileSync(resolve('src/components/FeedbackPanel.tsx'), 'utf8')
  assert.match(source, /dismissFeedbackFab\(/)
  assert.doesNotMatch(
    source,
    /sessionStorage\.setItem\(\s*['"]feedback_btn_dismissed['"]/,
  )
})
