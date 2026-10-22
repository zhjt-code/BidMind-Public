import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

/**
 * 源码契约测试: 批量删除对话框必须消费"打开瞬间冻结的快照"而非乐观更新的实时派生，
 * 且删除进行中文案/关闭防护符合两阶段语义。防止回归为"删除中数量归零 + 可中途关闭"。
 */

const projectListSource = readFileSync(
  new URL('../src/pages/ProjectList.tsx', import.meta.url),
  'utf8',
)
const managementHookSource = readFileSync(
  new URL('../src/pages/projectList/hooks/useProjectListManagement.ts', import.meta.url),
  'utf8',
)
const dialogSource = readFileSync(
  new URL('../src/pages/projectList/CreateProjectDialog.tsx', import.meta.url),
  'utf8',
)

test('ProjectList 打开批量删除时冻结目标快照，对话框不消费实时派生 selectedProjects', () => {
  // 快照 state 存在
  assert.match(managementHookSource, /batchDeleteTargets/)
  // 打开入口先快照再开框
  assert.match(
    managementHookSource,
    /setBatchDeleteTargets\(selectedProjects\)[\s\S]{0,80}setBatchDeleteConfirm\(true\)/,
  )
  // 对话框 props 使用快照
  assert.match(projectListSource, /selectedProjects=\{management\.batchDeleteTargets\}/)
  assert.doesNotMatch(projectListSource, /selectedProjects=\{selectedProjects\}/)
  // 删除 id 取自快照(与乐观更新解耦)
  assert.match(managementHookSource, /batchDeleteTargets\.map\(\(p\) => p\.id\)/)
})

test('BatchDeleteConfirmDialog 删除进行中使用进行时文案且标题切换', () => {
  assert.match(dialogSource, /正在删除选中的/)
  assert.match(dialogSource, /批量删除中/)
  // 确认态文案仍保留
  assert.match(dialogSource, /确定要删除以下/)
})

test('BatchDeleteConfirmDialog 删除进行中禁止经 onOpenChange 关闭(对齐单删对话框防护)', () => {
  assert.match(
    dialogSource,
    /open=\{open\} onOpenChange=\{\(v\) => \{ if \(!v && !loading\) onCancel\(\) \}\}/,
  )
})
