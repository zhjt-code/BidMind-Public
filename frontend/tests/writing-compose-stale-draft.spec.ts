import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('SectionDraft 暴露 failure 状态字段供内容页识别绑定变更', () => {
  const source = readFileSync(new URL('../src/services/writingApi.ts', import.meta.url), 'utf8')

  assert.match(source, /failureState:\s*string\s*\|\s*null/)
  assert.match(source, /failureReason:\s*string\s*\|\s*null/)
  assert.match(source, /reviewRequired:\s*number\s*\|\s*null/)
  assert.match(source, /failureState:\s*\(raw\.failure_state as string\) \|\| null/)
  assert.match(source, /reviewRequired:\s*\(raw\.review_required as number\) \?\? null/)
})

test('WritingCompose 对绑定变更草稿显示重新生成提示', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /绑定已变更，请重新生成章节/)
  assert.match(source, /selectedDraft\?\.failureState === 'needs_review'/)
  assert.match(source, /selectedDraft\?\.reviewRequired === 1/)
})

test('WritingCompose 恢复生成状态应优先使用已清洗 currentDetail', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /status\.currentDetail \|\| status\.progress/)
  assert.doesNotMatch(source, /setComposeDetail\(status\.progress \|\| '生成中\.\.\.'\)/)
})

test('RichTextEditor 必须消费生成完成后的外部草稿内容刷新', () => {
  const source = readFileSync(new URL('../src/components/RichTextEditor.tsx', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /isInternalUpdate/)
  assert.match(source, /editor\.commands\.setContent\(ensureBlockStructure\(content\), \{ emitUpdate: false \}\)/)
})

test('RichTextEditor setEditable 不得在挂载时触发 update 事件', () => {
  const source = readFileSync(new URL('../src/components/RichTextEditor.tsx', import.meta.url), 'utf8')

  assert.match(source, /editor\.isEditable !== editable/)
  assert.match(source, /setEditable\(editable, false\)/)
})

test('WritingCompose 章节切换必须在渲染期同步重置编辑内容', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /syncedEditorKey !== editorInstanceKey/)
  assert.match(source, /setEditContent\(selectedDraft\?\.content \|\| ''\)/)
})

test('WritingCompose 首次全文生成期间必须锁定章节导航交互', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /composeInteractionLocked\s*=/)
  assert.match(source, /<WritingComposeSidebar/)
  assert.match(source, /disabled=\{composeInteractionLocked\}/)
})

test('首次生成锁定必须由服务端可重建数据推导而非会话内草稿数', () => {
  const source = readFileSync(new URL('../src/pages/writing/WritingCompose.tsx', import.meta.url), 'utf8')

  assert.match(source, /isFirstComposeInteractionLocked/)
  assert.doesNotMatch(source, /composeInteractionLocked = composing && drafts\.length === 0/)
})
