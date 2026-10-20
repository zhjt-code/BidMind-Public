/**
 * WritingBinding 筛选 + 折叠/展开 hook。
 *
 * 从 WritingBinding.tsx L52-54 + L104-115 + L177-179 + L285-347 + 全展开/全收起按钮整段迁出。
 *
 * 内聚：
 *   - collapsedTypes / confidenceFilter state
 *   - initialCollapseAppliedRef 初始展开守卫（首次加载后默认全部展开，便于定位）
 *   - handleFilterClick 4 路径展开/收起（high/low/conflict/global）
 *   - toggleTypeCollapse 单项切换
 *   - clearFilter 清除筛选
 *   - expandAll / collapseAll 全展开/全收起
 *   - resetForPid pid 切换时重置守卫
 *
 * Plan §三 Q5 决议：filter 展开/收起计算复用 bindingDerivations.computeFilterCollapse 纯函数，
 * 但 collapsedTypes state 与 setCollapsedTypes 留 hook 内部，外部不暴露 setter，避免破坏 referential equality。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { OutlineNode, WritingBinding } from '@/services/writingApi'
import { computeFilterCollapse, computeFocusCollapse, type BindingFocusTarget } from '../bindingDerivations'
import type { BindingFilter } from '../types'

interface UseBindingFilterInputs {
  pid: number
  initialLoaded: boolean
  outlineNodes: OutlineNode[]
  bindings: WritingBinding[]
  conflictMaterialIds: Set<number>
  nodeParentMap: Map<number, number | null>
}

export interface UseBindingFilterResult {
  collapsedTypes: Record<string, boolean>
  confidenceFilter: BindingFilter
  toggleTypeCollapse: (key: string) => void
  handleFilterClick: (filter: NonNullable<BindingFilter>) => void
  clearFilter: () => void
  expandAll: () => void
  collapseAll: () => void
  focusSection: (focus: BindingFocusTarget) => void
}

export function useBindingFilter({
  pid,
  initialLoaded,
  outlineNodes,
  bindings,
  conflictMaterialIds,
  nodeParentMap,
}: UseBindingFilterInputs): UseBindingFilterResult {
  const [collapsedTypes, setCollapsedTypes] = useState<Record<string, boolean>>({})
  const [confidenceFilter, setConfidenceFilter] = useState<BindingFilter>(null)
  const initialCollapseAppliedRef = useRef(false)

  // pid 切换时重置守卫：必须在 hook 内部维护守卫 ref（不跨 hook 共享）。
  useEffect(() => {
    initialCollapseAppliedRef.current = false
    setConfidenceFilter(null)
    setCollapsedTypes({})
  }, [pid])

  // 初始加载完成 + 有节点 + 首次进入：默认全部展开，便于用户定位绑定项。
  useEffect(() => {
    if (initialLoaded && outlineNodes.length > 0 && !initialCollapseAppliedRef.current) {
      initialCollapseAppliedRef.current = true
      const initExpanded: Record<string, boolean> = {}
      for (const n of outlineNodes) {
        initExpanded[`node-${n.id}`] = false
      }
      initExpanded['global-clauses'] = false
      initExpanded['unapplied-materials'] = false
      initExpanded['unmatched-clauses'] = false
      initExpanded['orphan-reference'] = false
      initExpanded['orphan'] = false
      initExpanded['unlinked'] = false
      setCollapsedTypes(initExpanded)
    }
  }, [initialLoaded, outlineNodes])

  const toggleTypeCollapse = useCallback((type: string) => {
    setCollapsedTypes((prev) => ({ ...prev, [type]: !prev[type] }))
  }, [])

  const handleFilterClick = useCallback((filter: NonNullable<BindingFilter>) => {
    if (confidenceFilter === filter) {
      setConfidenceFilter(null)
      setCollapsedTypes({})
      return
    }
    setConfidenceFilter(filter)
    const newCollapsed = computeFilterCollapse({
      filter,
      bindings,
      outlineNodes,
      conflictMaterialIds,
      nodeParentMap,
    })
    setCollapsedTypes(newCollapsed)
  }, [confidenceFilter, bindings, outlineNodes, nodeParentMap, conflictMaterialIds])

  const clearFilter = useCallback(() => {
    setConfidenceFilter(null)
    setCollapsedTypes({})
  }, [])

  const expandAll = useCallback(() => {
    const expanded: Record<string, boolean> = {}
    for (const n of outlineNodes) expanded[`node-${n.id}`] = false
    expanded['global-clauses'] = false
    expanded['orphan'] = false
    expanded['orphan-reference'] = false
    expanded['unapplied-materials'] = false
    expanded['unmatched-clauses'] = false
    expanded['unlinked'] = false
    setCollapsedTypes(expanded)
  }, [outlineNodes])

  const collapseAll = useCallback(() => {
    const collapsed: Record<string, boolean> = {}
    for (const n of outlineNodes) collapsed[`node-${n.id}`] = true
    collapsed['global-clauses'] = true
    collapsed['orphan'] = true
    collapsed['orphan-reference'] = true
    collapsed['unapplied-materials'] = true
    collapsed['unmatched-clauses'] = true
    collapsed['unlinked'] = true
    setCollapsedTypes(collapsed)
  }, [outlineNodes])

  const focusSection = useCallback((focus: BindingFocusTarget) => {
    setConfidenceFilter(null)
    setCollapsedTypes(computeFocusCollapse(focus, outlineNodes))
  }, [outlineNodes])

  return {
    collapsedTypes,
    confidenceFilter,
    toggleTypeCollapse,
    handleFilterClick,
    clearFilter,
    expandAll,
    collapseAll,
    focusSection,
  }
}
