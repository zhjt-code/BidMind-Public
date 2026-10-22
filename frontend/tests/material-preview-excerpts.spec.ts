import assert from 'node:assert/strict'
import test from 'node:test'

import {
  materialPreviewExcerpts,
  type MaterialPreviewExcerptSource,
} from '../src/pages/writing/WritingMaterials/hooks/materialPreviewExcerpts.ts'

function material(overrides: Partial<MaterialPreviewExcerptSource>): MaterialPreviewExcerptSource {
  return {
    content: null,
    sourcePage: null,
    sourcePageStart: null,
    sourcePageEnd: null,
    ...overrides,
  }
}

test('子素材短页范围且有正文时产出摘录', () => {
  const excerpts = materialPreviewExcerpts(material({
    sourcePageStart: 9,
    sourcePageEnd: 10,
    content: '拟派项目经理持有一级建造师注册证书',
  }))
  assert.deepEqual(excerpts, ['拟派项目经理持有一级建造师注册证书'])
})

test('同形态第二例：跨很多页的父素材不产出摘录，避免整页假框', () => {
  const excerpts = materialPreviewExcerpts(material({
    sourcePageStart: 1,
    sourcePageEnd: 40,
    content: '很长的整份素材正文'.repeat(10),
  }))
  assert.deepEqual(excerpts, [])
})
