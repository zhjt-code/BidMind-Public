import type { WritingBinding } from '@/services/writingApi'

/** 章节树节点（含递归 children） */
export interface OutlineTreeNode {
  id: number
  title: string
  depth: number
  children: OutlineTreeNode[]
  bindings: WritingBinding[]
  totalBindings: number
  totalSuggested: number
}

/** 顶部 4 个统计卡片当前激活的筛选维度；null 表示未筛选 */
export type BindingFilter = 'high' | 'low' | 'conflict' | 'global' | null

/** 单条绑定的展示标签（约束/素材/混合） */
export interface BindingLabel {
  tag: string
  tagClass: string
  text: string
  fullText: string
  reason: string | null
}
