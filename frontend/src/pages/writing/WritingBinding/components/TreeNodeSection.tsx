/**
 * 章节树节点组件（递归渲染）。
 *
 * 从 WritingBinding.tsx L853-935 整段迁出（行为 byte-equal）。
 * - 顶级节点 isRoot 含 border-b last:border-b-0；子节点无
 * - 缩进按 depth * 6
 * - 折叠/展开通过 collapsedTypes[`node-${id}`] 控制
 */

import React from 'react'
import { ChevronDown } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { WritingMaterial } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import { type PendingBindingAction } from '../../writingBindingState'
import type { BindingFilter, OutlineTreeNode } from '../types'
import { BindingRow } from './BindingRow'

export interface TreeNodeSectionProps {
  node: OutlineTreeNode
  collapsedTypes: Record<string, boolean>
  toggleCollapse: (key: string) => void
  clauseMap: Map<number, WritingClause>
  materialMap: Map<number, WritingMaterial>
  onConfirm: (id: number) => void
  onReject: (id: number) => void
  pendingAction: PendingBindingAction | null
  confidenceFilter: BindingFilter
  conflictMaterialIds: Set<number>
}

export const TreeNodeSection: React.FC<TreeNodeSectionProps> = ({
  node, collapsedTypes, toggleCollapse, clauseMap, materialMap, onConfirm, onReject, pendingAction, confidenceFilter, conflictMaterialIds,
}) => {
  const collapseKey = `node-${node.id}`
  const isCollapsed = collapsedTypes[collapseKey]
  const isRoot = node.depth === 0
  const indent = node.depth * 6

  return (
    <div className={isRoot ? 'border-b last:border-b-0' : ''}>
      <button
        onClick={() => toggleCollapse(collapseKey)}
        className={`flex items-center gap-2 w-full text-left hover:bg-muted/30 transition ${
          isRoot ? 'p-3' : 'py-1.5 px-3'
        }`}
        style={{ paddingLeft: `${Math.max(12, indent * 4)}px` }}
      >
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${isCollapsed ? '-rotate-90' : ''}`} />
        <span className={`truncate ${isRoot ? 'font-semibold text-sm' : 'font-medium text-sm text-foreground/80'}`}>
          {node.title}
        </span>
        {node.totalBindings > 0 ? (
          <Badge variant="secondary" className="text-xs shrink-0">{node.totalBindings} 条</Badge>
        ) : (
          <span className="text-xs text-muted-foreground shrink-0">暂无绑定</span>
        )}
        {node.totalSuggested > 0 && (
          <span className="text-xs text-muted-foreground shrink-0">{node.totalSuggested} 待确认</span>
        )}
      </button>
      {!isCollapsed && (
        <div>
          {node.bindings.length > 0 && (
            <div className="divide-y">
              {node.bindings.map((b) => (
                <BindingRow
                  key={b.id}
                  b={b}
                  indent={(node.depth + 1) * 6}
                  clauseMap={clauseMap}
                  materialMap={materialMap}
                  onConfirm={onConfirm}
                  onReject={onReject}
                  pendingAction={pendingAction}
                  confidenceFilter={confidenceFilter}
                  conflictMaterialIds={conflictMaterialIds}
                />
              ))}
            </div>
          )}
          {node.children.map((child) => (
            <TreeNodeSection
              key={child.id}
              node={child}
              collapsedTypes={collapsedTypes}
              toggleCollapse={toggleCollapse}
              clauseMap={clauseMap}
              materialMap={materialMap}
              onConfirm={onConfirm}
              onReject={onReject}
              pendingAction={pendingAction}
              confidenceFilter={confidenceFilter}
              conflictMaterialIds={conflictMaterialIds}
            />
          ))}
        </div>
      )}
    </div>
  )
}
