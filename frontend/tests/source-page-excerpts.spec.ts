import assert from 'node:assert/strict'
import test from 'node:test'

import { excerptsForPage } from '../src/pages/result/sourcePageExcerpts.ts'

test('分卷定位带 excerpt 时按文档+页取出', () => {
  const found = excerptsForPage({
    page: 3,
    documentId: 11,
    locations: [
      { documentId: 11, page: 3, excerpt: '安全生产许可证原件' },
      { documentId: 12, page: 3, excerpt: '技术标无关' },
    ],
  })
  assert.deepEqual(found, ['安全生产许可证原件'])
})

test('同形态第二例：整本 snippets 在无 locations 时仍能按页取出', () => {
  const found = excerptsForPage({
    page: 5,
    documentId: 21,
    locations: [],
    pageExcerpts: [
      { documentId: 21, page: 5, excerpt: '整本投标文件业绩一览表' },
    ],
  })
  assert.deepEqual(found, ['整本投标文件业绩一览表'])
})
