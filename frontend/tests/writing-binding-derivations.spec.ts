/**
 * R2-11 Phase 1 契约测试 -- WritingBinding 派生纯函数。
 *
 * 锚点策略（Plan §4.2，4 类共 13 用例）：
 *   (a) 树构造 4 个 -- buildTreeRoots
 *   (b) 筛选展开收起 4 个 -- computeFilterCollapse
 *   (c) 冲突派生 2 个 -- computeConflictMaterials
 *   (d) 待确认派生 3 个 -- computeBindingStats
 *
 * 这些用例在 R2-11 拆分前后必须 byte-equal 通过，作为重构防御网。
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildNodeParentMap,
  buildTreeRoots,
  computeBindingStats,
  computeConflictMaterials,
  computeFilterCollapse,
  computeFocusCollapse,
  computeLeafNodeOptions,
  HIGH_CONFIDENCE_THRESHOLD,
  MATERIAL_CONFLICT_NODE_THRESHOLD,
} from '../src/pages/writing/WritingBinding/bindingDerivations.ts'

import type { OutlineNode, WritingBinding } from '../src/services/writingApi.ts'

function mkNode(overrides: Partial<OutlineNode> & Pick<OutlineNode, 'id'>): OutlineNode {
  return {
    id: overrides.id,
    projectId: 1,
    parentId: null,
    title: `节点${overrides.id}`,
    depth: 0,
    sortOrder: 0,
    nodeType: 'section',
    isRequired: 0,
    scoreWeight: null,
    targetWordCount: null,
    status: 'pending',
    volumeType: null,
    ...overrides,
  } as OutlineNode
}

function mkBinding(overrides: Partial<WritingBinding> & Pick<WritingBinding, 'id'>): WritingBinding {
  return {
    id: overrides.id,
    projectId: 1,
    outlineNodeId: null,
    clauseId: null,
    materialId: null,
    bindingType: 'material_to_node',
    confidence: 0.8,
    source: 'auto',
    status: 'suggested',
    reason: null,
    scope: null,
    ...overrides,
  } as WritingBinding
}

// ============================================================================
// (a) 树构造 -- buildTreeRoots（4 用例）
// ============================================================================

test('(a1) buildTreeRoots：scope=global 进入 globalBindings，不进入章节树', () => {
  const nodes = [mkNode({ id: 10 })]
  const bindings = [
    mkBinding({ id: 1, scope: 'global', outlineNodeId: null }),
    mkBinding({ id: 2, scope: null, outlineNodeId: 10 }),
  ]
  const result = buildTreeRoots(bindings, nodes)
  assert.deepEqual(result.globalBindings.map((b) => b.id), [1])
  assert.equal(result.treeRoots.length, 1)
  assert.equal(result.treeRoots[0]!.bindings.length, 1)
  assert.equal(result.treeRoots[0]!.bindings[0]!.id, 2)
})

test('(a2) buildTreeRoots：scope=orphan + outlineNodeId=null 进入 orphanBindings，不进入章节树', () => {
  const nodes = [mkNode({ id: 10 })]
  const bindings = [
    mkBinding({ id: 1, scope: 'orphan', outlineNodeId: null, clauseId: 100 }),
    mkBinding({ id: 2, scope: null, outlineNodeId: 10 }),
  ]
  const result = buildTreeRoots(bindings, nodes)
  assert.deepEqual(result.orphanBindings.map((b) => b.id), [1])
  assert.equal(result.unappliedMaterialBindings.length, 0)
  assert.equal(result.unlinkedBindings.length, 0)
  assert.equal(result.treeRoots[0]!.bindings.length, 1)
})

test('(a2b) buildTreeRoots：scope=orphan + materialId 进入未应用素材，不混入孤儿约束', () => {
  const nodes = [mkNode({ id: 10 })]
  const bindings = [
    mkBinding({ id: 1, scope: 'orphan', outlineNodeId: null, materialId: 701 }),
    mkBinding({ id: 2, scope: 'orphan', outlineNodeId: null, clauseId: 100 }),
  ]
  const result = buildTreeRoots(bindings, nodes)
  assert.deepEqual(result.unappliedMaterialBindings.map((b) => b.id), [1])
  assert.deepEqual(result.orphanBindings.map((b) => b.id), [2])
  assert.equal(result.treeRoots[0]!.bindings.length, 0)
})

test('(a3) buildTreeRoots：scope!=global/orphan 但 outlineNodeId=null 视为 unlinkedBindings（旧逻辑兼容）', () => {
  const nodes = [mkNode({ id: 10 })]
  const bindings = [
    mkBinding({ id: 1, scope: null, outlineNodeId: null }),
    mkBinding({ id: 2, scope: 'orphan', outlineNodeId: 10 }),
  ]
  const result = buildTreeRoots(bindings, nodes)
  assert.deepEqual(result.unlinkedBindings.map((b) => b.id), [1])
  assert.equal(result.orphanBindings.length, 0)
  assert.equal(result.treeRoots[0]!.bindings.length, 1)
  assert.equal(result.treeRoots[0]!.bindings[0]!.id, 2)
})

test('(a4) buildTreeRoots：嵌套树 totalBindings/totalSuggested 累加子树', () => {
  const nodes = [
    mkNode({ id: 1, parentId: null, depth: 0 }),
    mkNode({ id: 2, parentId: 1, depth: 1 }),
    mkNode({ id: 3, parentId: 2, depth: 2 }),
  ]
  const bindings = [
    mkBinding({ id: 100, outlineNodeId: 1, status: 'suggested' }),
    mkBinding({ id: 101, outlineNodeId: 2, status: 'confirmed' }),
    mkBinding({ id: 102, outlineNodeId: 3, status: 'suggested' }),
    mkBinding({ id: 103, outlineNodeId: 3, status: 'suggested' }),
  ]
  const result = buildTreeRoots(bindings, nodes)
  const root = result.treeRoots[0]!
  assert.equal(root.id, 1)
  assert.equal(root.totalBindings, 4) // 1 + 1 + 2
  assert.equal(root.totalSuggested, 3) // 1 (id=100) + 0 (id=101 confirmed) + 2 (id=102/103)
  const child = root.children[0]!
  assert.equal(child.id, 2)
  assert.equal(child.totalBindings, 3) // 1 + 2
  assert.equal(child.totalSuggested, 2) // 0 (id=101 confirmed) + 2 (id=102/103)
  const grandchild = child.children[0]!
  assert.equal(grandchild.id, 3)
  assert.equal(grandchild.totalBindings, 2)
  assert.equal(grandchild.totalSuggested, 2)
})

// ============================================================================
// (b) 筛选展开收起 -- computeFilterCollapse（4 用例）
// ============================================================================

test('(b1) computeFilterCollapse：filter=global 仅展开 global-clauses，全部章节/orphan/unlinked 收起', () => {
  const nodes = [mkNode({ id: 1 }), mkNode({ id: 2 })]
  const result = computeFilterCollapse({
    filter: 'global',
    bindings: [],
    outlineNodes: nodes,
    conflictMaterialIds: new Set(),
    nodeParentMap: buildNodeParentMap(nodes),
  })
  assert.equal(result['node-1'], true)
  assert.equal(result['node-2'], true)
  assert.equal(result['global-clauses'], false)
  assert.equal(result['orphan'], true)
  assert.equal(result['orphan-reference'], true)
  assert.equal(result['unapplied-materials'], true)
  assert.equal(result['unmatched-clauses'], true)
  assert.equal(result['unlinked'], true)
})

test('(b2) computeFilterCollapse：filter=conflict 仅展开冲突素材链路上的祖先', () => {
  const nodes = [
    mkNode({ id: 1, parentId: null }),
    mkNode({ id: 2, parentId: 1 }),
    mkNode({ id: 3, parentId: 2 }),
    mkNode({ id: 99, parentId: null }),
  ]
  const bindings = [
    mkBinding({
      id: 100,
      bindingType: 'material_to_node',
      materialId: 500,
      outlineNodeId: 3,
    }),
  ]
  const result = computeFilterCollapse({
    filter: 'conflict',
    bindings,
    outlineNodes: nodes,
    conflictMaterialIds: new Set([500]),
    nodeParentMap: buildNodeParentMap(nodes),
  })
  // 节点 3 的祖先链 3 → 2 → 1 应展开（false=展开）；节点 99 不在链路上应收起
  assert.equal(result['node-1'], false)
  assert.equal(result['node-2'], false)
  assert.equal(result['node-3'], false)
  assert.equal(result['node-99'], true)
  assert.equal(result['global-clauses'], true)
  assert.equal(result['orphan'], true)
  assert.equal(result['orphan-reference'], true)
  assert.equal(result['unapplied-materials'], true)
  assert.equal(result['unmatched-clauses'], true)
  assert.equal(result['unlinked'], true)
})

test('(b2b) computeFocusCollapse：focus=orphan 仅展开待人工分配区块', () => {
  const nodes = [mkNode({ id: 1 }), mkNode({ id: 2 })]
  const result = computeFocusCollapse('orphan', nodes)
  assert.equal(result['node-1'], true)
  assert.equal(result['node-2'], true)
  assert.equal(result['orphan'], false)
  assert.equal(result['orphan-reference'], true)
  assert.equal(result['unapplied-materials'], true)
  assert.equal(result['unmatched-clauses'], true)
  assert.equal(result['unlinked'], true)
})

test('(b2c) computeFocusCollapse：focus=unmatched 仅展开未匹配约束区块', () => {
  const nodes = [mkNode({ id: 1 }), mkNode({ id: 2 })]
  const result = computeFocusCollapse('unmatched', nodes)
  assert.equal(result['orphan'], true)
  assert.equal(result['unmatched-clauses'], false)
  assert.equal(result['unapplied-materials'], true)
})

test('(b3) computeFilterCollapse：filter=high 仅展开高置信链路（>=0.7）', () => {
  const nodes = [
    mkNode({ id: 1 }),
    mkNode({ id: 2 }),
  ]
  const bindings = [
    mkBinding({ id: 100, outlineNodeId: 1, confidence: 0.9 }),
    mkBinding({ id: 101, outlineNodeId: 2, confidence: 0.5 }),
  ]
  const result = computeFilterCollapse({
    filter: 'high',
    bindings,
    outlineNodes: nodes,
    conflictMaterialIds: new Set(),
    nodeParentMap: buildNodeParentMap(nodes),
  })
  assert.equal(result['node-1'], false) // 高置信，展开
  assert.equal(result['node-2'], true) // 低置信，收起
})

test('(b4) computeFilterCollapse：filter=low 仅展开低置信非 rejected 链路', () => {
  const nodes = [
    mkNode({ id: 1 }),
    mkNode({ id: 2 }),
    mkNode({ id: 3 }),
  ]
  const bindings = [
    mkBinding({ id: 100, outlineNodeId: 1, confidence: 0.9 }),
    mkBinding({ id: 101, outlineNodeId: 2, confidence: 0.5 }),
    mkBinding({ id: 102, outlineNodeId: 3, confidence: 0.4, status: 'rejected' }),
  ]
  const result = computeFilterCollapse({
    filter: 'low',
    bindings,
    outlineNodes: nodes,
    conflictMaterialIds: new Set(),
    nodeParentMap: buildNodeParentMap(nodes),
  })
  assert.equal(result['node-1'], true) // 高置信，收起
  assert.equal(result['node-2'], false) // 低置信非 rejected，展开
  assert.equal(result['node-3'], true) // rejected 排除，收起
})

// ============================================================================
// (c) 冲突派生 -- computeConflictMaterials（2 用例 + 阈值常量）
// ============================================================================

test('(c1) computeConflictMaterials：素材绑定 > 3 节点（即 >= 4 节点）视为冲突', () => {
  const bindings = [
    mkBinding({ id: 1, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 1 }),
    mkBinding({ id: 2, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 2 }),
    mkBinding({ id: 3, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 3 }),
    mkBinding({ id: 4, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 4 }),
  ]
  const result = computeConflictMaterials(bindings)
  assert.equal(result.conflictCount, 1)
  assert.deepEqual([...result.conflictMaterialIds], [500])
})

test('(c2) computeConflictMaterials：等于 3 节点不视为冲突（边界，且常量 MATERIAL_CONFLICT_NODE_THRESHOLD=3）', () => {
  const bindings = [
    mkBinding({ id: 1, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 1 }),
    mkBinding({ id: 2, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 2 }),
    mkBinding({ id: 3, bindingType: 'material_to_node', materialId: 500, outlineNodeId: 3 }),
  ]
  const result = computeConflictMaterials(bindings)
  assert.equal(result.conflictCount, 0)
  assert.equal(result.conflictMaterialIds.size, 0)
  assert.equal(MATERIAL_CONFLICT_NODE_THRESHOLD, 3)
})

// ============================================================================
// (d) 待确认派生 -- computeBindingStats（3 用例）
// ============================================================================

test('(d1) computeBindingStats：toConfirmBindings 仅含 status=suggested && outlineNodeId!=null', () => {
  const bindings = [
    mkBinding({ id: 1, status: 'suggested', outlineNodeId: 10 }),
    mkBinding({ id: 2, status: 'suggested', outlineNodeId: null }), // 缺章节
    mkBinding({ id: 3, status: 'confirmed', outlineNodeId: 10 }), // 非 suggested
    mkBinding({ id: 4, status: 'rejected', outlineNodeId: 10 }), // 非 suggested
  ]
  const stats = computeBindingStats(bindings)
  assert.deepEqual(stats.toConfirmBindings.map((b) => b.id), [1])
  assert.equal(stats.suggested, 1)
})

test('(d2) computeBindingStats：toConfirmHighConf 阈值 HIGH_CONFIDENCE_THRESHOLD=0.7', () => {
  const bindings = [
    mkBinding({ id: 1, status: 'suggested', outlineNodeId: 10, confidence: 0.7 }),
    mkBinding({ id: 2, status: 'suggested', outlineNodeId: 10, confidence: 0.69 }),
    mkBinding({ id: 3, status: 'suggested', outlineNodeId: 10, confidence: 0.95 }),
  ]
  const stats = computeBindingStats(bindings)
  assert.equal(stats.toConfirmHighConf, 2) // id=1 (0.7) + id=3 (0.95)
  assert.equal(stats.toConfirmLowConf, 1) // id=2 (0.69)
  assert.equal(HIGH_CONFIDENCE_THRESHOLD, 0.7)
})

test('(d3) computeBindingStats：activeBindings 排除 rejected；hasEffectiveBindings 排除 rejected + outlineNodeId=null', () => {
  const bindings = [
    mkBinding({ id: 1, status: 'suggested', outlineNodeId: 10, confidence: 0.9 }),
    mkBinding({ id: 2, status: 'rejected', outlineNodeId: 10 }),
    mkBinding({ id: 3, status: 'confirmed', outlineNodeId: null }), // 非 rejected 但缺章节
    mkBinding({ id: 4, status: 'suggested', outlineNodeId: 20, confidence: 0.4 }),
    mkBinding({ id: 5, scope: 'global', status: 'confirmed', outlineNodeId: null }),
  ]
  const stats = computeBindingStats(bindings)
  // activeBindings: 排除 rejected -> id 1/3/4/5
  assert.deepEqual(stats.activeBindings.map((b) => b.id).sort((a, b) => a - b), [1, 3, 4, 5])
  // highConf: confidence >= 0.7 在 active 中: id=1 (0.9)，id=3/5 默认 confidence=0.8 也算
  assert.equal(stats.highConf, 3) // id=1 (0.9), id=3 (0.8 默认), id=5 (0.8 默认)
  assert.equal(stats.lowConf, 1) // id=4 (0.4)
  assert.equal(stats.globalCount, 1) // id=5
  // hasEffectiveBindings: 非 rejected 且 outlineNodeId!=null -> id=1, id=4
  assert.equal(stats.hasEffectiveBindings, true)
})

// ============================================================================
// 辅助纯函数（不计入 13 用例核心，但作为附加防御）
// ============================================================================

test('(e1) computeLeafNodeOptions：仅返回叶子节点（无子节点）', () => {
  const nodes = [
    mkNode({ id: 1, parentId: null }),
    mkNode({ id: 2, parentId: 1 }),
    mkNode({ id: 3, parentId: 2 }),
  ]
  const options = computeLeafNodeOptions(nodes)
  assert.equal(options.length, 1)
  assert.equal(options[0]!.id, 3)
})

test('(e2) buildNodeParentMap：返回 nodeId → parentId 索引', () => {
  const nodes = [
    mkNode({ id: 1, parentId: null }),
    mkNode({ id: 2, parentId: 1 }),
  ]
  const map = buildNodeParentMap(nodes)
  assert.equal(map.get(1), null)
  assert.equal(map.get(2), 1)
})
