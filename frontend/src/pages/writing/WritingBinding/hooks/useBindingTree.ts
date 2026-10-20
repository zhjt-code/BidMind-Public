/**
 * WritingBinding 树构造与派生统计 hook。
 *
 * 整合原 WritingBinding.tsx L361-547 的 9 个 useMemo + 7 个派生量到单一 hook：
 *   - treeRoots / unlinkedBindings / globalBindings / orphanBindings (来自 buildTreeRoots)
 *   - clauseMap / materialMap / leafNodeOptions / nodeParentMap (索引构造)
 *   - activeBindings / highConf / lowConf / globalCount / conflictCount / conflictMaterialIds / suggested /
 *     toConfirmBindings / toConfirmHighConf / toConfirmLowConf / hasEffectiveBindings (派生统计)
 *
 * 关键约束（破坏将引入 referential equality 退化）：
 *   - 每个 useMemo 的 deps 严格不扩大，与原文件 byte-equal
 *   - 不依赖外部派生量（如 conflictMaterialIds 由本 hook 自己派生，不接受外部传入）
 *   - 调用方仅消费返回对象，不允许在外部再 useMemo 包裹
 */

import { useMemo } from 'react'
import type { OutlineNode, WritingBinding, WritingMaterial } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import {
  buildNodeParentMap,
  buildTreeRoots,
  computeBindingStats,
  computeConflictMaterials,
  computeLeafNodeOptions,
} from '../bindingDerivations'
import type { OutlineTreeNode } from '../types'

interface UseBindingTreeInputs {
  bindings: WritingBinding[]
  outlineNodes: OutlineNode[]
  clauses: WritingClause[]
  materials: WritingMaterial[]
}

export interface UseBindingTreeResult {
  // 三类分流
  treeRoots: OutlineTreeNode[]
  unlinkedBindings: WritingBinding[]
  globalBindings: WritingBinding[]
  orphanBindings: WritingBinding[]
  unappliedMaterialBindings: WritingBinding[]
  // 索引
  clauseMap: Map<number, WritingClause>
  materialMap: Map<number, WritingMaterial>
  leafNodeOptions: { id: number; title: string }[]
  nodeParentMap: Map<number, number | null>
  // 派生统计
  activeBindings: WritingBinding[]
  highConf: number
  lowConf: number
  globalCount: number
  suggested: number
  toConfirmBindings: WritingBinding[]
  toConfirmHighConf: number
  toConfirmLowConf: number
  hasEffectiveBindings: boolean
  conflictCount: number
  conflictMaterialIds: Set<number>
}

export function useBindingTree(inputs: UseBindingTreeInputs): UseBindingTreeResult {
  const { bindings, outlineNodes, clauses, materials } = inputs

  const leafNodeOptions = useMemo(() => computeLeafNodeOptions(outlineNodes), [outlineNodes])

  const nodeParentMap = useMemo(() => buildNodeParentMap(outlineNodes), [outlineNodes])

  const clauseMap = useMemo(() => new Map(clauses.map((c) => [c.id, c])), [clauses])
  const materialMap = useMemo(() => new Map(materials.map((m) => [m.id, m])), [materials])

  const treePartition = useMemo(
    () => buildTreeRoots(bindings, outlineNodes),
    [bindings, outlineNodes],
  )

  const stats = useMemo(() => computeBindingStats(bindings), [bindings])

  const conflict = useMemo(
    () => computeConflictMaterials(stats.activeBindings),
    [stats.activeBindings],
  )

  return {
    treeRoots: treePartition.treeRoots,
    unlinkedBindings: treePartition.unlinkedBindings,
    globalBindings: treePartition.globalBindings,
    orphanBindings: treePartition.orphanBindings,
    unappliedMaterialBindings: treePartition.unappliedMaterialBindings,
    clauseMap,
    materialMap,
    leafNodeOptions,
    nodeParentMap,
    activeBindings: stats.activeBindings,
    highConf: stats.highConf,
    lowConf: stats.lowConf,
    globalCount: stats.globalCount,
    suggested: stats.suggested,
    toConfirmBindings: stats.toConfirmBindings,
    toConfirmHighConf: stats.toConfirmHighConf,
    toConfirmLowConf: stats.toConfirmLowConf,
    hasEffectiveBindings: stats.hasEffectiveBindings,
    conflictCount: conflict.conflictCount,
    conflictMaterialIds: conflict.conflictMaterialIds,
  }
}
