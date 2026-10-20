import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const outlinePage = readFileSync('frontend/src/pages/writing/WritingOutline.tsx', 'utf8')
const outlineTreeNode = readFileSync('frontend/src/pages/writing/WritingOutlineTreeNode.tsx', 'utf8')
const writingApi = readFileSync('frontend/src/services/writingApi.ts', 'utf8')
const writingStore = readFileSync('frontend/src/stores/writingStore.ts', 'utf8')

test('outline API exposes createNode for immediate persistence', () => {
  assert.match(writingApi, /createNode:\s*async\s*\(/)
  assert.match(writingApi, /api\.post\(`\/writing\/projects\/\$\{projectId\}\/outline`/)
  assert.match(writingStore, /createNode:\s*\(projectId: number, data: Record<string, unknown>\) => Promise<void>/)
  assert.match(writingStore, /await writingService\.createNode\(projectId, data\)/)
  assert.match(writingStore, /await writingService\.getOutline\(projectId\)/)
})

test('locked skeleton nodes can be explicitly unlocked with inherited editability', () => {
  assert.match(outlinePage, /unlockedNodeIds/)
  assert.match(outlineTreeNode, /ancestorUnlocked/)
  assert.match(outlineTreeNode, /effectiveUnlocked/)
  assert.match(outlineTreeNode, /onToggleUnlock/)
  assert.match(outlineTreeNode, /aria-label=\{`解除锁定：\$\{node\.title\}`\}/)
  assert.match(outlineTreeNode, /text-blue-600/)
  assert.match(outlineTreeNode, /bg-blue-50/)
  assert.match(outlineTreeNode, /-scale-x-100/)
})

test('outline page adds nodes through a persisted dialog and blocks dirty next step', () => {
  assert.match(outlinePage, /addDialogParentId/)
  assert.match(outlinePage, /addDialogInsertAfterId/)
  assert.match(outlinePage, /addDialogInsertPosition/)
  assert.match(outlinePage, /handleCreateNode/)
  assert.match(outlinePage, /insert_after_id/)
  assert.match(outlinePage, /insert_position/)
  assert.match(outlinePage, /openAddChildDialog/)
  assert.match(outlinePage, /openAddSiblingDialog/)
  assert.match(outlineTreeNode, /新增子目录/)
  assert.match(outlineTreeNode, /下方新增同级目录/)
  assert.match(outlinePage, /dirtyEditingNodeIds/)
  assert.match(outlinePage, /setPendingNextDialogOpen/)
  assert.match(outlinePage, /保存并继续/)
})

test('outline page supports same-parent drag sorting with persisted reorder', () => {
  assert.match(outlineTreeNode, /@dnd-kit\/core/)
  assert.match(outlineTreeNode, /@dnd-kit\/sortable/)
  assert.match(outlineTreeNode, /DndContext/)
  assert.match(outlineTreeNode, /SortableContext/)
  assert.match(outlineTreeNode, /useSortable/)
  assert.match(outlineTreeNode, /GripVertical/)
  assert.match(outlinePage, /handleReorderSiblings/)
  assert.match(outlinePage, /writingService\.reorderOutline/)
  assert.match(outlinePage, /调整目录顺序/)
  assert.match(outlinePage, /存在未保存的目录编辑/)
})

test('outline guidance copy reflects unlockable skeleton nodes', () => {
  assert.match(outlinePage, /默认仅查看；点击锁图标解除锁定后，可编辑、删除或新增子目录/)
  assert.doesNotMatch(outlinePage, /仅可查看，不可编辑或删除/)
  assert.doesNotMatch(outlinePage, /带锁章节来自招标文件固定结构，不可编辑/)
})

test('outline leading controls keep drag adjacent to lock and omit leaf expand spacer', () => {
  const leadingControls = outlineTreeNode.match(
    /flex shrink-0 items-center gap-0\.5[\s\S]*?\{editing && effectiveUnlocked/,
  )
  assert.ok(leadingControls, 'expected compact leading control group')
  const block = leadingControls[0]
  const dragIdx = block.indexOf('拖动排序')
  const lockIdx = block.indexOf('解除锁定')
  const expandIdx = block.indexOf('ChevronDown')
  assert.ok(dragIdx >= 0 && lockIdx >= 0 && expandIdx >= 0)
  assert.ok(dragIdx < lockIdx && lockIdx < expandIdx)
  assert.match(outlineTreeNode, /flex shrink-0 items-center gap-0\.5/)
  assert.match(outlineTreeNode, /node\.children\.length > 0 && \(/)
  assert.doesNotMatch(
    block,
    /node\.children\.length > 0 \?[\s\S]*?<span className="w-5" \/>/,
  )
})

test('outline node row collapses secondary actions into more menu', () => {
  assert.match(outlineTreeNode, /DropdownMenu/)
  assert.match(outlineTreeNode, /DropdownMenuTrigger/)
  assert.match(outlineTreeNode, /DropdownMenuContent/)
  assert.match(outlineTreeNode, /DropdownMenuItem/)
  assert.match(outlineTreeNode, /DropdownMenuSeparator/)
  assert.match(outlineTreeNode, /MoreHorizontal/)
  assert.match(outlineTreeNode, /const TreeNodeActions/)
  assert.match(outlineTreeNode, /<TreeNodeActions/)
  assert.match(outlineTreeNode, /aria-label=\{`更多操作：\$\{node\.title\}`\}/)
  assert.match(outlineTreeNode, /text-destructive focus:text-destructive/)
})
