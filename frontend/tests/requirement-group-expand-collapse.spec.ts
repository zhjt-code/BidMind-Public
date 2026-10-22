/**
 * 防御测试：招标要求确认页「全部展开 / 全部收起」分组切换。
 *
 * 覆盖的真实风险：
 *   - 默认折叠 + 聚焦/评分 chip 展开后，用户无法一键回到全部折叠
 *   - 按钮误放在非分组视图（单分类平铺）造成无效操作
 *   - 切换折叠时误清分类/否决项/评分项筛选
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const filterBarSource = readFileSync(
  new URL('../src/pages/RequirementReview/components/RequirementFilterBar.tsx', import.meta.url),
  'utf8',
)
const pageSource = readFileSync(
  new URL('../src/pages/RequirementReview/index.tsx', import.meta.url),
  'utf8',
)

test('RequirementFilterBar 提供全部展开/全部收起受控切换且靠右放置', () => {
  assert.match(filterBarSource, /anyGroupExpanded:\s*boolean/)
  assert.match(filterBarSource, /expandCollapseVisible:\s*boolean/)
  assert.match(filterBarSource, /onToggleExpandCollapse:\s*\(\) => void/)
  assert.match(filterBarSource, /expandCollapseVisible\s*&&/, '不可见时不渲染按钮')
  assert.match(
    filterBarSource,
    /anyGroupExpanded\s*\?\s*['"]全部收起['"]\s*:\s*['"]全部展开['"]/,
    '半展开亦显示全部收起，便于一键回默认折叠',
  )
  assert.match(filterBarSource, /ml-auto/, '按钮必须靠筛选栏右侧，与 chip 区拉开')
  assert.match(
    filterBarSource,
    /from 'lucide-react'[\s\S]*ChevronsDownUp[\s\S]*ChevronsUpDown|from 'lucide-react'[\s\S]*ChevronsUpDown[\s\S]*ChevronsDownUp/,
    '必须复用写标绑定同款 lucide 双箭头图标，禁止纯文案按钮',
  )
  assert.match(
    filterBarSource,
    /anyGroupExpanded\s*\?\s*\([\s\S]*?ChevronsDownUp[\s\S]*?\)\s*:\s*\([\s\S]*?ChevronsUpDown/,
    '收起态用 ChevronsDownUp，展开态用 ChevronsUpDown',
  )
})

test('RequirementReview 接线展开收起且不重置筛选状态', () => {
  assert.match(pageSource, /onToggleExpandCollapse=\{toggleExpandCollapseGroups\}/)
  assert.match(
    pageSource,
    /expandCollapseVisible=\{activeTab === ['"]all['"] && groupedMap\.size >= 2\}/,
    '仅全部分组视图且至少两组时显示',
  )
  assert.match(pageSource, /anyGroupExpanded=\{expandedGroups\.size > 0\}/)

  const toggleIdx = pageSource.indexOf('const toggleExpandCollapseGroups')
  assert.ok(toggleIdx !== -1, '必须存在具名 toggleExpandCollapseGroups')
  const nextConst = pageSource.indexOf('\n  const ', toggleIdx + 1)
  const toggleSlice = pageSource.slice(toggleIdx, nextConst === -1 ? toggleIdx + 500 : nextConst)
  assert.match(toggleSlice, /setExpandedGroups\(new Set\(\)\)/, '收起必须清空 expandedGroups')
  assert.match(
    toggleSlice,
    /setExpandedGroups\(new Set\(groupedMap\.keys\(\)\)\)|Array\.from\(groupedMap\.keys\(\)\)/,
    '展开必须覆盖当前可见分组（受筛选后的 groupedMap）',
  )
  assert.doesNotMatch(toggleSlice, /setActiveTab/, '不得改动分类筛选')
  assert.doesNotMatch(toggleSlice, /setShowMandatoryOnly/, '不得改动否决项筛选')
  assert.doesNotMatch(toggleSlice, /setShowScoringFocusOnly/, '不得改动评分项筛选')
})
