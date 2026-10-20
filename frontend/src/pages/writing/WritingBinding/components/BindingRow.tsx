/**
 * 单条 binding 行展示组件。
 *
 * 从 WritingBinding.tsx L937-1058 整段迁出（行为 byte-equal）。
 * 包含：标签 + tooltip 描述 + 置信度 + 状态徽标 + 操作按钮组（确认/拒绝/分配到章节）。
 */

import React from 'react'
import { Check, Loader2, MoveRight, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { WritingBinding as WritingBindingType, WritingMaterial } from '@/services/writingApi'
import type { WritingClause } from '@/services/writingClauseTransforms'
import { isBindingRowBusy, type PendingBindingAction } from '../../writingBindingState'
import { getBindingLabel } from '../bindingLabel'
import type { BindingFilter } from '../types'

export interface BindingRowProps {
  b: WritingBindingType
  indent: number
  clauseMap: Map<number, WritingClause>
  materialMap: Map<number, WritingMaterial>
  onConfirm: (id: number) => void
  onReject: (id: number) => void
  onRestore?: (id: number) => void
  onAssignToChapter?: (bindingId: number, nodeId: number) => void
  leafNodes?: { id: number; title: string }[]
  pendingAction: PendingBindingAction | null
  confidenceFilter: BindingFilter
  conflictMaterialIds: Set<number>
}

export const BindingRow: React.FC<BindingRowProps> = ({
  b, indent, clauseMap, materialMap, onConfirm, onReject, onRestore, onAssignToChapter, leafNodes, pendingAction, confidenceFilter, conflictMaterialIds,
}) => {
  const label = getBindingLabel(b, clauseMap, materialMap)
  const isGlobal = b.scope === 'global'
  const isOrphan = b.scope === 'orphan'
  const rowBusy = isBindingRowBusy(b.id, pendingAction)
  const dimmed = (() => {
    if (confidenceFilter == null) return false
    switch (confidenceFilter) {
      case 'high': return b.confidence < 0.7
      case 'low': return b.confidence >= 0.7 || b.status === 'rejected'
      case 'conflict': return !(b.bindingType === 'material_to_node' && b.materialId != null && conflictMaterialIds.has(b.materialId))
      case 'global': return b.scope !== 'global'
    }
  })()
  return (
    <div
      className={`flex items-start gap-2 px-3 py-2 text-sm ${b.status === 'rejected' ? 'opacity-40' : dimmed ? 'opacity-25' : ''}`}
      style={{ paddingLeft: `${Math.max(12, indent * 4 + 16)}px` }}
    >
      <div className="flex-1 min-w-0 pt-0.5">
        <div className="flex items-center gap-1.5">
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium flex-shrink-0 ${label.tagClass}`}>
            {label.tag}
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="truncate cursor-default">{label.text}</span>
            </TooltipTrigger>
            {label.text.length > 40 && (
              <TooltipContent side="bottom" className="max-w-[400px] whitespace-normal text-sm">
                {label.fullText}
              </TooltipContent>
            )}
          </Tooltip>
        </div>
        {label.reason && (
          <Tooltip>
            <TooltipTrigger asChild>
              <p className="text-xs text-muted-foreground mt-0.5 truncate cursor-default">
                {label.reason}
              </p>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[400px] whitespace-normal text-sm">
              {label.reason}
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className={`text-xs font-mono ${b.confidence >= 0.7 ? 'text-green-600' : 'text-amber-600'}`}>
          {(b.confidence * 100).toFixed(0)}%
        </span>
        {isGlobal ? (
          <Badge variant="default" className="text-xs">全局</Badge>
        ) : (
          <>
            <Badge
              variant={b.status === 'confirmed' ? 'default' : b.status === 'rejected' ? 'destructive' : 'secondary'}
              className="text-xs"
            >
              {b.status === 'confirmed' ? '已确认' : b.status === 'rejected' ? '已拒绝' : '待确认'}
            </Badge>
            {b.status === 'suggested' && (
              <>
                <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onConfirm(b.id)} disabled={rowBusy}>
                  {rowBusy && pendingAction?.action === 'confirm'
                    ? <Loader2 className="h-3 w-3 animate-spin text-green-600" />
                    : <Check className="h-3 w-3 text-green-600" />}
                </Button>
                <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onReject(b.id)} disabled={rowBusy}>
                  {rowBusy && pendingAction?.action === 'reject'
                    ? <Loader2 className="h-3 w-3 animate-spin text-red-500" />
                    : <X className="h-3 w-3 text-red-500" />}
                </Button>
              </>
            )}
            {b.status === 'confirmed' && (
              <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onReject(b.id)} disabled={rowBusy}>
                {rowBusy && pendingAction?.action === 'reject'
                  ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                  : <X className="h-3 w-3 text-muted-foreground" />}
              </Button>
            )}
            {b.status === 'rejected' && isOrphan && onRestore && (
              <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs" onClick={() => onRestore(b.id)} disabled={rowBusy}>
                {rowBusy && pendingAction?.action === 'restore'
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : '恢复'}
              </Button>
            )}
          </>
        )}
        {isOrphan && onAssignToChapter && leafNodes && leafNodes.length > 0 && (
          <Select onValueChange={(val) => onAssignToChapter(b.id, Number(val))} disabled={rowBusy}>
            <SelectTrigger className="h-7 w-[140px] text-xs">
              {rowBusy && pendingAction?.action === 'assign'
                ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                : <MoveRight className="h-3 w-3 mr-1" />}
              <SelectValue placeholder="分配到章节" />
            </SelectTrigger>
            <SelectContent>
              {leafNodes.map((n) => (
                <SelectItem key={n.id} value={String(n.id)} className="text-xs">
                  {n.title.length > 15 ? n.title.slice(0, 15) + '...' : n.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  )
}
