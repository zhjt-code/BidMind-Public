import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { shouldAutosaveDraftChange } from '../src/pages/writing/writingDraftAutosave.ts'

test('初始编辑器同步的等价内容不触发自动保存', () => {
  assert.equal(
    shouldAutosaveDraftChange(
      '<p>公司简介正文</p>',
      '公司简介正文',
      { initialEditorSync: true },
    ),
    false,
  )
})

test('真实内容变化触发自动保存', () => {
  assert.equal(
    shouldAutosaveDraftChange(
      '<p>公司简介正文已修改</p>',
      '<p>公司简介正文</p>',
      { initialEditorSync: true },
    ),
    true,
  )
})

test('完全相同内容不触发自动保存', () => {
  assert.equal(
    shouldAutosaveDraftChange(
      '<p>正文</p>',
      '<p>正文</p>',
      { initialEditorSync: false },
    ),
    false,
  )
})

test('WritingCompose 自动保存前必须进行变更判定', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /shouldAutosaveDraftChange/)
  assert.match(source, /initialEditorSyncRef/)
  assert.match(source, /writingService\.updateDraft/)
})
