/**
 * 职责: WritingCompose 侧边目录树的纯函数派生（层级、草稿状态、gate 徽标、算法生成图示标记）
 * 不做: React 状态、数据请求、门禁与重试逻辑、素材图识别
 *
 * 收敛期从 WritingCompose.tsx 原样迁出（复杂度护栏: 页面超 800 行）。
 */
import type { GateFlagEntry } from '@/services/writingApi'
import { gateFlagsToBadges } from './writingComposeGateFlags.ts'
import type { WritingComposeTreeNode } from './WritingComposeSidebar'

/** 与 figure_bridge 协议对齐：只认 figure 上的数字 data-diagram-id，排除素材图与非数字占位。 */
const GENERATED_DIAGRAM_FIGURE_RE =
  /<figure\b[^>]*\bdata-diagram-id\s*=\s*["'](\d+)["']/i

interface ComposeTreeOutlineNode {
  id: number
  parentId: number | null
  title: string
  depth: number
}

interface ComposeTreeDraft {
  outlineNodeId: number
  status?: string | null
  isLocked?: number | null
  gateFlags?: unknown
  content?: string | null
}

export function draftContentHasGeneratedDiagram(
  content: string | null | undefined,
): boolean {
  if (!content) return false
  return GENERATED_DIAGRAM_FIGURE_RE.test(content)
}

export interface ComposeTreeResult {
  treeRoots: WritingComposeTreeNode[]
  parentMap: Map<number, number | null>
}

export function buildComposeTree<
  N extends ComposeTreeOutlineNode,
  D extends ComposeTreeDraft,
>(outlineNodes: N[], drafts: D[]): ComposeTreeResult {
  const childrenMap = new Map<number | null, N[]>()
  const pMap = new Map<number, number | null>()
  for (const n of outlineNodes) {
    pMap.set(n.id, n.parentId)
    if (!childrenMap.has(n.parentId)) childrenMap.set(n.parentId, [])
    childrenMap.get(n.parentId)!.push(n)
  }
  const draftMap = new Map(drafts.map((d) => [d.outlineNodeId, d]))

  function buildNode(n: N): WritingComposeTreeNode {
    const kids = (childrenMap.get(n.id) || []).map(buildNode)
    const isLeaf = kids.length === 0
    const draft = draftMap.get(n.id)
    let childLeafCount = 0
    let childDraftCount = 0
    for (const k of kids) {
      if (k.isLeaf) {
        childLeafCount += 1
        if (k.draftStatus) childDraftCount += 1
      } else {
        childLeafCount += k.childLeafCount
        childDraftCount += k.childDraftCount
      }
    }
    const gateBadges = isLeaf
      ? gateFlagsToBadges(
          (draft?.gateFlags as Record<string, GateFlagEntry> | null | undefined),
        )
      : []
    const gateWarningCount = isLeaf
      ? (gateBadges.length > 0 ? 1 : 0)
      : kids.reduce((sum, child) => sum + child.gateWarningCount, 0)
    return {
      id: n.id,
      title: n.title,
      depth: n.depth,
      isLeaf,
      children: kids,
      draftStatus: draft?.status || null,
      isLocked: draft?.isLocked === 1,
      childDraftCount,
      childLeafCount,
      gateBadges,
      gateWarningCount,
      hasGeneratedDiagram: isLeaf && draftContentHasGeneratedDiagram(draft?.content),
    }
  }

  const roots = (childrenMap.get(null) || []).map(buildNode)
  return { treeRoots: roots, parentMap: pMap }
}
