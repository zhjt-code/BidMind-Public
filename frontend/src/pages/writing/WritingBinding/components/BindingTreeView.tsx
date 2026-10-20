/**
 * 章节树视图（含工具栏 + 全局约束 + 章节树 + 孤儿约束 + 未关联约束 4 个面板）。
 *
 * 从 WritingBinding.tsx L273-431 整段迁出（行为 byte-equal）。
 * - 顶部工具栏：treeRoots / bindings 计数 + 清除筛选 / 全部展开收起切换按钮
 * - 4 个面板：globalBindings / treeRoots / orphanBindings / unlinkedBindings
 *
 * 不直接消费 useBindingTree / useBindingFilter，所有数据通过 props 注入；
 * 主编排器负责装配，避免 hook 跨组件耦合。
 */

import React from 'react'
import { AlertTriangle, ChevronDown, ChevronsDownUp, ChevronsUpDown, Globe, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { WritingBinding as WritingBindingType, WritingMaterial } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import { type PendingBindingAction } from '../../writingBindingState'
import { shortUserFacingClauseText } from '../../writingClauseDisplay'
import type { BindingFilter, OutlineTreeNode } from '../types'
import { BindingRow } from './BindingRow'
import { TreeNodeSection } from './TreeNodeSection'

export interface BindingTreeViewProps {
  treeRoots: OutlineTreeNode[]
  globalBindings: WritingBindingType[]
  orphanBindings: WritingBindingType[]
  unappliedMaterialBindings: WritingBindingType[]
  unlinkedBindings: WritingBindingType[]
  unmatchedClauses: WritingClause[]
  bindingsCount: number
  clauseMap: Map<number, WritingClause>
  materialMap: Map<number, WritingMaterial>
  leafNodeOptions: { id: number; title: string }[]
  collapsedTypes: Record<string, boolean>
  confidenceFilter: BindingFilter
  conflictMaterialIds: Set<number>
  pendingBindingAction: PendingBindingAction | null
  toggleTypeCollapse: (key: string) => void
  clearFilter: () => void
  expandAll: () => void
  collapseAll: () => void
  onConfirm: (id: number) => void
  onReject: (id: number) => void
  onRestore: (id: number) => void
  onAssignToChapter: (bindingId: number, nodeId: number) => Promise<void>
}

export const BindingTreeView: React.FC<BindingTreeViewProps> = ({
  treeRoots,
  globalBindings,
  orphanBindings,
  unappliedMaterialBindings,
  unlinkedBindings,
  unmatchedClauses,
  bindingsCount,
  clauseMap,
  materialMap,
  leafNodeOptions,
  collapsedTypes,
  confidenceFilter,
  conflictMaterialIds,
  pendingBindingAction,
  toggleTypeCollapse,
  clearFilter,
  expandAll,
  collapseAll,
  onConfirm,
  onReject,
  onRestore,
  onAssignToChapter,
}) => {
  const [allExpanded, setAllExpanded] = React.useState(true)

  const handleToggleExpandAll = () => {
    if (allExpanded) {
      collapseAll()
    } else {
      expandAll()
    }
    setAllExpanded((prev) => !prev)
  }

  const isReferenceOrCheckOnly = (binding: WritingBindingType) => {
    const clause = binding.clauseId ? clauseMap.get(binding.clauseId) : null
    const relevance = clause?.metadataJson?.writing_relevance
    return relevance === 'reference' || relevance === 'check_only'
  }
  const actionableOrphanBindings = orphanBindings.filter((binding) => !isReferenceOrCheckOnly(binding))
  const referenceOrphanBindings = orphanBindings.filter(isReferenceOrCheckOnly)

  return (
    <TooltipProvider delayDuration={300}>
      <div>
        <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/20">
          <span className="text-xs text-muted-foreground">
            {treeRoots.length} 个顶级章节 / {bindingsCount} 条绑定
          </span>
          <div className="flex items-center gap-1">
            {confidenceFilter && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-muted-foreground"
                onClick={clearFilter}
              >
                <X className="h-3 w-3 mr-1" />
                清除筛选
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
                onClick={handleToggleExpandAll}
            >
              {allExpanded ? (
                <ChevronsDownUp className="h-3.5 w-3.5 mr-1" />
              ) : (
                <ChevronsUpDown className="h-3.5 w-3.5 mr-1" />
              )}
              {allExpanded ? '全部收起' : '全部展开'}
            </Button>
          </div>
        </div>

        {/* 全局约束面板 */}
        {globalBindings.length > 0 && (
          <div className="border-b bg-slate-50/50">
            <button
              onClick={() => toggleTypeCollapse('global-clauses')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-slate-100/50 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['global-clauses'] ? '-rotate-90' : ''}`} />
              <Globe className="h-4 w-4 text-slate-500" />
              <span className="font-medium text-sm text-slate-700">全局约束</span>
              <Badge variant="secondary" className="text-xs">{globalBindings.length} 条</Badge>
              <span className="text-xs text-muted-foreground">适用于所有章节，无需分配</span>
            </button>
            {!collapsedTypes['global-clauses'] && (
              <div className="divide-y">
                {globalBindings.map((b) => (
                  <BindingRow
                    key={b.id}
                    b={b}
                    indent={6}
                    clauseMap={clauseMap}
                    materialMap={materialMap}
                    onConfirm={onConfirm}
                    onReject={onReject}
                    onRestore={onRestore}
                    pendingAction={pendingBindingAction}
                    confidenceFilter={confidenceFilter}
                    conflictMaterialIds={conflictMaterialIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* 章节树 */}
        {treeRoots.map((root) => (
          <TreeNodeSection
            key={root.id}
            node={root}
            collapsedTypes={collapsedTypes}
            toggleCollapse={toggleTypeCollapse}
            clauseMap={clauseMap}
            materialMap={materialMap}
            onConfirm={onConfirm}
            onReject={onReject}
            pendingAction={pendingBindingAction}
            confidenceFilter={confidenceFilter}
            conflictMaterialIds={conflictMaterialIds}
          />
        ))}

        {/* 未应用素材(质控否决/召回未命中的素材留痕) */}
        {unappliedMaterialBindings.length > 0 && (
          <div className="border-t bg-amber-50/50">
            <button
              onClick={() => toggleTypeCollapse('unapplied-materials')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-amber-100/50 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['unapplied-materials'] ? '-rotate-90' : ''}`} />
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              <span className="font-medium text-sm text-amber-700">未应用素材</span>
              <Badge variant="secondary" className="text-xs bg-amber-100 text-amber-700">{unappliedMaterialBindings.length} 条</Badge>
              <span className="text-xs text-amber-600">质控判定未自动应用，可人工分配</span>
            </button>
            {!collapsedTypes['unapplied-materials'] && (
              <div className="divide-y">
                {unappliedMaterialBindings.map((b) => (
                  <BindingRow
                    key={b.id}
                    b={b}
                    indent={6}
                    clauseMap={clauseMap}
                    materialMap={materialMap}
                    onConfirm={onConfirm}
                    onReject={onReject}
                    onRestore={onRestore}
                    onAssignToChapter={onAssignToChapter}
                    leafNodes={leafNodeOptions}
                    pendingAction={pendingBindingAction}
                    confidenceFilter={confidenceFilter}
                    conflictMaterialIds={conflictMaterialIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* 孤儿约束(待人工分配) */}
        {actionableOrphanBindings.length > 0 && (
          <div className="border-t bg-orange-50/50">
            <button
              onClick={() => toggleTypeCollapse('orphan')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-orange-100/50 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['orphan'] ? '-rotate-90' : ''}`} />
              <AlertTriangle className="h-4 w-4 text-orange-500" />
              <span className="font-medium text-sm text-orange-700">待人工分配</span>
              <Badge variant="secondary" className="text-xs bg-orange-100 text-orange-700">{actionableOrphanBindings.length} 条</Badge>
              <span className="text-xs text-orange-600">未被任何章节匹配的约束</span>
            </button>
            {!collapsedTypes['orphan'] && (
              <div className="divide-y">
                {actionableOrphanBindings.map((b) => (
                  <BindingRow
                    key={b.id}
                    b={b}
                    indent={6}
                    clauseMap={clauseMap}
                    materialMap={materialMap}
                    onConfirm={onConfirm}
                    onReject={onReject}
                    onRestore={onRestore}
                    onAssignToChapter={onAssignToChapter}
                    leafNodes={leafNodeOptions}
                    pendingAction={pendingBindingAction}
                    confidenceFilter={confidenceFilter}
                    conflictMaterialIds={conflictMaterialIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* 告知/检查类约束: 默认不要求编入正文 */}
        {referenceOrphanBindings.length > 0 && (
          <div className="border-t bg-slate-50/50">
            <button
              onClick={() => toggleTypeCollapse('orphan-reference')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-slate-100/50 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['orphan-reference'] ? '-rotate-90' : ''}`} />
              <AlertTriangle className="h-4 w-4 text-slate-500" />
              <span className="font-medium text-sm text-slate-700">告知类约束</span>
              <Badge variant="secondary" className="text-xs">{referenceOrphanBindings.length} 条</Badge>
              <span className="text-xs text-muted-foreground">默认不编入正文，可展开检查</span>
            </button>
            {!collapsedTypes['orphan-reference'] && (
              <div className="divide-y">
                {referenceOrphanBindings.map((b) => (
                  <BindingRow
                    key={b.id}
                    b={b}
                    indent={6}
                    clauseMap={clauseMap}
                    materialMap={materialMap}
                    onConfirm={onConfirm}
                    onReject={onReject}
                    onRestore={onRestore}
                    onAssignToChapter={onAssignToChapter}
                    leafNodes={leafNodeOptions}
                    pendingAction={pendingBindingAction}
                    confidenceFilter={confidenceFilter}
                    conflictMaterialIds={conflictMaterialIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* 未匹配约束(无任何绑定记录, 只读定位) */}
        {unmatchedClauses.length > 0 && (
          <div className="border-t bg-rose-50/50">
            <button
              onClick={() => toggleTypeCollapse('unmatched-clauses')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-rose-100/50 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['unmatched-clauses'] ? '-rotate-90' : ''}`} />
              <AlertTriangle className="h-4 w-4 text-rose-500" />
              <span className="font-medium text-sm text-rose-700">未匹配约束</span>
              <Badge variant="secondary" className="text-xs bg-rose-100 text-rose-700">{unmatchedClauses.length} 条</Badge>
              <span className="text-xs text-rose-600">未建立章节绑定, 需检查或重新匹配</span>
            </button>
            {!collapsedTypes['unmatched-clauses'] && (
              <div className="divide-y">
                {unmatchedClauses.map((clause) => (
                  <div key={clause.id} className="px-6 py-2 text-sm">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">约束</Badge>
                      <span className="min-w-0 flex-1 truncate">{shortUserFacingClauseText(clause.content, 120)}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      当前未生成任何章节绑定。可返回约束确认页修正表述，或重新执行匹配。
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 未关联章节(旧逻辑兼容) */}
        {unlinkedBindings.length > 0 && (
          <div className="border-t">
            <button
              onClick={() => toggleTypeCollapse('unlinked')}
              className="flex items-center gap-2 w-full p-3 text-left hover:bg-muted/30 transition"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${collapsedTypes['unlinked'] ? '-rotate-90' : ''}`} />
              <span className="font-medium text-sm text-muted-foreground">其他未关联</span>
              <Badge variant="secondary" className="text-xs">{unlinkedBindings.length} 条</Badge>
            </button>
            {!collapsedTypes['unlinked'] && (
              <div className="divide-y">
                {unlinkedBindings.map((b) => (
                  <BindingRow
                    key={b.id}
                    b={b}
                    indent={6}
                    clauseMap={clauseMap}
                    materialMap={materialMap}
                    onConfirm={onConfirm}
                    onReject={onReject}
                    onRestore={onRestore}
                    pendingAction={pendingBindingAction}
                    confidenceFilter={confidenceFilter}
                    conflictMaterialIds={conflictMaterialIds}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </TooltipProvider>
  )
}
