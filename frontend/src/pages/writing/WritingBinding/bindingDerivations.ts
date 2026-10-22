/**
 * WritingBinding 派生纯函数。
 *
 * 从 WritingBinding.tsx 抽出，拆分为 hook 内 useMemo 与外部单测共享的纯计算入口。
 *
 * 关键约束：
 *   - 输入仅依赖 bindings + outlineNodes（其余派生量从此延伸），保持 hook 内 useMemo 的 deps 集合不扩大。
 *   - 不引入 React 依赖，可在 node:test 内直接断言。
 *   - 冲突阈值 / 高置信阈值与原文件 byte-equal（>3 个节点视为冲突，confidence>=0.7 视为高置信）。
 */

import type { OutlineNode, WritingBinding } from '@/services/writingApi'
import type { OutlineTreeNode } from './types'

export const HIGH_CONFIDENCE_THRESHOLD = 0.7
export const MATERIAL_CONFLICT_NODE_THRESHOLD = 3

interface TreePartition {
  treeRoots: OutlineTreeNode[]
  unlinkedBindings: WritingBinding[]
  globalBindings: WritingBinding[]
  orphanBindings: WritingBinding[]
  unappliedMaterialBindings: WritingBinding[]
}

/**
 * 把 bindings + outlineNodes 切分为：
 *   - globalBindings: scope === 'global'（与节点无关）
 *   - orphanBindings: scope === 'orphan' && outlineNodeId === null && clauseId != null（待人工分配约束）
 *   - unappliedMaterialBindings: scope === 'orphan' && outlineNodeId === null && materialId != null（未应用素材留痕）
 *   - 章节树（含 unlinkedBindings：scope 非 global/orphan 但 outlineNodeId 为 null 的旧数据）
 */
export function buildTreeRoots(
  bindings: WritingBinding[],
  outlineNodes: OutlineNode[],
): TreePartition {
  const globalBindings: WritingBinding[] = []
  const orphanBindings: WritingBinding[] = []
  const unappliedMaterialBindings: WritingBinding[] = []
  const treeBindings: WritingBinding[] = []

  for (const b of bindings) {
    if (b.scope === 'global') {
      globalBindings.push(b)
    } else if (b.scope === 'orphan' && b.outlineNodeId == null && b.materialId != null) {
      unappliedMaterialBindings.push(b)
    } else if (b.scope === 'orphan' && b.outlineNodeId == null) {
      orphanBindings.push(b)
    } else {
      treeBindings.push(b)
    }
  }

  const bindingsByNodeId = new Map<number, WritingBinding[]>()
  const unlinkedBindings: WritingBinding[] = []
  for (const b of treeBindings) {
    if (b.outlineNodeId == null) {
      unlinkedBindings.push(b)
    } else {
      if (!bindingsByNodeId.has(b.outlineNodeId)) bindingsByNodeId.set(b.outlineNodeId, [])
      bindingsByNodeId.get(b.outlineNodeId)!.push(b)
    }
  }

  const childrenMap = new Map<number | null, OutlineNode[]>()
  for (const n of outlineNodes) {
    const pid = n.parentId
    if (!childrenMap.has(pid)) childrenMap.set(pid, [])
    childrenMap.get(pid)!.push(n)
  }

  function buildNode(n: OutlineNode): OutlineTreeNode {
    const kids = (childrenMap.get(n.id) || []).map(buildNode)
    const ownBindings = bindingsByNodeId.get(n.id) || []
    const childTotal = kids.reduce((s, k) => s + k.totalBindings, 0)
    const childSuggested = kids.reduce((s, k) => s + k.totalSuggested, 0)
    return {
      id: n.id,
      title: n.title,
      depth: n.depth,
      children: kids,
      bindings: ownBindings,
      totalBindings: ownBindings.length + childTotal,
      totalSuggested: ownBindings.filter((b) => b.status === 'suggested').length + childSuggested,
    }
  }

  const treeRoots = (childrenMap.get(null) || []).map(buildNode)
  return { treeRoots, unlinkedBindings, globalBindings, orphanBindings, unappliedMaterialBindings }
}

interface ConflictResult {
  conflictCount: number
  conflictMaterialIds: Set<number>
}

/**
 * 计算"同一素材绑定到 > MATERIAL_CONFLICT_NODE_THRESHOLD 个章节"的冲突素材。
 * 入参应已过滤 status === 'rejected' 的绑定。
 */
export function computeConflictMaterials(activeBindings: WritingBinding[]): ConflictResult {
  const matNodeMap = new Map<number, Set<number>>()
  for (const b of activeBindings) {
    if (b.bindingType === 'material_to_node' && b.materialId && b.outlineNodeId) {
      if (!matNodeMap.has(b.materialId)) matNodeMap.set(b.materialId, new Set())
      matNodeMap.get(b.materialId)!.add(b.outlineNodeId)
    }
  }
  const ids = new Set<number>()
  let count = 0
  for (const [matId, nodes] of matNodeMap.entries()) {
    if (nodes.size > MATERIAL_CONFLICT_NODE_THRESHOLD) {
      count++
      ids.add(matId)
    }
  }
  return { conflictCount: count, conflictMaterialIds: ids }
}

interface BindingStats {
  activeBindings: WritingBinding[]
  highConf: number
  lowConf: number
  globalCount: number
  suggested: number
  toConfirmBindings: WritingBinding[]
  toConfirmHighConf: number
  toConfirmLowConf: number
  hasEffectiveBindings: boolean
}

/**
 * 一次性派生顶部统计卡片 + 批量确认对话框 + 主按钮所需的所有计数。
 * 与原文件 L383-414 byte-equal 行为。
 */
export function computeBindingStats(bindings: WritingBinding[]): BindingStats {
  const activeBindings = bindings.filter((b) => b.status !== 'rejected')
  const highConf = activeBindings.filter((b) => b.confidence >= HIGH_CONFIDENCE_THRESHOLD).length
  const lowConf = activeBindings.filter((b) => b.confidence < HIGH_CONFIDENCE_THRESHOLD).length
  const globalCount = bindings.filter((b) => b.scope === 'global').length
  const toConfirmBindings = bindings.filter(
    (b) => b.status === 'suggested' && b.outlineNodeId != null,
  )
  const toConfirmHighConf = toConfirmBindings.filter(
    (b) => b.confidence >= HIGH_CONFIDENCE_THRESHOLD,
  ).length
  const toConfirmLowConf = toConfirmBindings.length - toConfirmHighConf
  const hasEffectiveBindings = bindings.some(
    (b) => b.status !== 'rejected' && b.outlineNodeId != null,
  )
  return {
    activeBindings,
    highConf,
    lowConf,
    globalCount,
    suggested: toConfirmBindings.length,
    toConfirmBindings,
    toConfirmHighConf,
    toConfirmLowConf,
    hasEffectiveBindings,
  }
}

export type BindingFilter = 'high' | 'low' | 'conflict' | 'global'
export type BindingFocusTarget = 'orphan' | 'unmatched' | 'unappliedMaterial'

export function computeFocusCollapse(
  focus: BindingFocusTarget,
  outlineNodes: OutlineNode[],
): Record<string, boolean> {
  const collapsed: Record<string, boolean> = {}
  for (const n of outlineNodes) {
    collapsed[`node-${n.id}`] = true
  }
  collapsed['global-clauses'] = true
  collapsed['orphan'] = focus !== 'orphan'
  collapsed['orphan-reference'] = true
  collapsed['unapplied-materials'] = focus !== 'unappliedMaterial'
  collapsed['unmatched-clauses'] = focus !== 'unmatched'
  collapsed['unlinked'] = true
  return collapsed
}

interface CollapseInputs {
  filter: BindingFilter
  bindings: WritingBinding[]
  outlineNodes: OutlineNode[]
  conflictMaterialIds: Set<number>
  nodeParentMap: Map<number, number | null>
}

/**
 * 根据当前 filter 计算章节树折叠/展开 map。
 * - filter='global': 全部章节收起，仅展开 'global-clauses'，收起 orphan/unlinked
 * - filter='conflict': 仅展开冲突素材链路上的章节祖先链
 * - filter='high'/'low': 展开命中绑定的章节祖先链
 *
 * 与原文件 L427-489 byte-equal 行为。
 */
export function computeFilterCollapse(inputs: CollapseInputs): Record<string, boolean> {
  const { filter, bindings, outlineNodes, conflictMaterialIds, nodeParentMap } = inputs

  if (filter === 'global') {
    const newCollapsed: Record<string, boolean> = {}
    for (const n of outlineNodes) {
      newCollapsed[`node-${n.id}`] = true
    }
    newCollapsed['global-clauses'] = false
    newCollapsed['orphan'] = true
    newCollapsed['orphan-reference'] = true
    newCollapsed['unapplied-materials'] = true
    newCollapsed['unmatched-clauses'] = true
    newCollapsed['unlinked'] = true
    return newCollapsed
  }

  if (filter === 'conflict') {
    const expandIds = new Set<number>()
    for (const b of bindings) {
      if (
        b.bindingType === 'material_to_node' &&
        b.materialId &&
        conflictMaterialIds.has(b.materialId) &&
        b.outlineNodeId != null
      ) {
        let cur: number | null | undefined = b.outlineNodeId
        while (cur != null) {
          if (expandIds.has(cur)) break
          expandIds.add(cur)
          cur = nodeParentMap.get(cur) ?? null
        }
      }
    }
    const newCollapsed: Record<string, boolean> = {}
    for (const n of outlineNodes) {
      newCollapsed[`node-${n.id}`] = !expandIds.has(n.id)
    }
    newCollapsed['global-clauses'] = true
    newCollapsed['orphan'] = true
    newCollapsed['orphan-reference'] = true
    newCollapsed['unapplied-materials'] = true
    newCollapsed['unmatched-clauses'] = true
    newCollapsed['unlinked'] = true
    return newCollapsed
  }

  const isMatch =
    filter === 'high'
      ? (b: WritingBinding) => b.confidence >= HIGH_CONFIDENCE_THRESHOLD
      : (b: WritingBinding) =>
          b.confidence < HIGH_CONFIDENCE_THRESHOLD && b.status !== 'rejected'
  const expandIds = new Set<number>()
  for (const b of bindings) {
    if (isMatch(b) && b.outlineNodeId != null) {
      let cur: number | null | undefined = b.outlineNodeId
      while (cur != null) {
        if (expandIds.has(cur)) break
        expandIds.add(cur)
        cur = nodeParentMap.get(cur) ?? null
      }
    }
  }
  const newCollapsed: Record<string, boolean> = {}
  for (const n of outlineNodes) {
    newCollapsed[`node-${n.id}`] = !expandIds.has(n.id)
  }
  return newCollapsed
}

/**
 * 根据 outlineNodes 构造 nodeId → parentId 索引。
 * computeFilterCollapse 'conflict' / 'high' / 'low' 路径需要逆向追溯祖先链。
 */
export function buildNodeParentMap(outlineNodes: OutlineNode[]): Map<number, number | null> {
  return new Map(outlineNodes.map((n) => [n.id, n.parentId]))
}

interface LeafNodeOption {
  id: number
  title: string
}

/**
 * 选出"叶子节点"（无子节点的章节）作为孤儿绑定可分配的目标。
 */
export function computeLeafNodeOptions(outlineNodes: OutlineNode[]): LeafNodeOption[] {
  const childIds = new Set(outlineNodes.map((n) => n.parentId).filter(Boolean))
  return outlineNodes
    .filter((n) => !childIds.has(n.id))
    .map((n) => ({ id: n.id, title: n.title }))
}
