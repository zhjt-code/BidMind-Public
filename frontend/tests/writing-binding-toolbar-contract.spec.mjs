import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const bindingTreeView = readFileSync(
  'frontend/src/pages/writing/WritingBinding/components/BindingTreeView.tsx',
  'utf8',
)
const bindingFilterHook = readFileSync(
  'frontend/src/pages/writing/WritingBinding/hooks/useBindingFilter.ts',
  'utf8',
)

test('素材关联树工具栏使用单个展开收起切换按钮', () => {
  assert.match(bindingTreeView, /const \[allExpanded,\s*setAllExpanded\] = React\.useState\(true\)/)
  assert.match(bindingTreeView, /const handleToggleExpandAll = \(\) => \{/)
  assert.match(bindingTreeView, /onClick=\{handleToggleExpandAll\}/)
  assert.match(bindingTreeView, /allExpanded \? '全部收起' : '全部展开'/)
  assert.doesNotMatch(
    bindingTreeView,
    /onClick=\{expandAll\}[\s\S]*?全部展开[\s\S]*?onClick=\{collapseAll\}[\s\S]*?全部收起/,
  )
})

test('素材关联树首次加载默认全部展开', () => {
  assert.match(bindingFilterHook, /const initExpanded: Record<string, boolean> = \{\}/)
  assert.match(bindingFilterHook, /initExpanded\[`node-\$\{n\.id\}`\] = false/)
  assert.match(bindingFilterHook, /initExpanded\['global-clauses'\] = false/)
  assert.match(bindingFilterHook, /initExpanded\['unmatched-clauses'\] = false/)
  assert.doesNotMatch(bindingFilterHook, /if \(n\.depth > 0\) initCollapsed\[`node-\$\{n\.id\}`\] = true/)
})
