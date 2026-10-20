import React from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import SourcePageBadge from '@/components/SourcePageBadge'
import type { WritingMaterial } from '@/services/writingApi'
import {
  formatMaterialTypeLabel,
  formatPageRange,
} from '../materialGrouping.ts'
import { CHILD_GROUP_META, CHILD_GROUP_ORDER, PROCESSING_STATUS_BADGE } from '../constants.ts'
import type { ChildGroupKey } from '../types'
import { ChildMaterialItem } from './ChildMaterialItem.tsx'

interface MaterialRowProps {
  material: WritingMaterial
  children: WritingMaterial[]
  childGroups: Record<ChildGroupKey, WritingMaterial[]> | undefined
  isProcessing: boolean
  savingPendingTitles: boolean
  editingMaterialId: number | null
  editingTitleValue: string
  pendingTitleEdits: Record<number, string>
  getDisplayedTitle: (material: WritingMaterial) => string
  onChangeTitleValue: (value: string) => void
  onCommitTitleEdit: () => void
  onCancelTitleEdit: () => void
  onStartEditTitle: (material: WritingMaterial) => void
  onOpenPdfPreview: (material: WritingMaterial) => void
  onMaterialAction: (materialId: number, action: string, successMessage: string) => void
  onDelete: (materialId: number) => void
}

export const MaterialRow: React.FC<MaterialRowProps> = ({
  material,
  children,
  childGroups,
  isProcessing,
  savingPendingTitles,
  editingMaterialId,
  editingTitleValue,
  pendingTitleEdits,
  getDisplayedTitle,
  onChangeTitleValue,
  onCommitTitleEdit,
  onCancelTitleEdit,
  onStartEditTitle,
  onOpenPdfPreview,
  onMaterialAction,
  onDelete,
}) => {
  const statusInfo = PROCESSING_STATUS_BADGE[material.processingStatus]
  const pageRange = formatPageRange(material)
  const usingWhole = material.materialRole === 'normal' && children.length > 0

  return (
    <div className="p-4 hover:bg-muted/30 transition">
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1 min-w-0">
          <p className="font-medium text-sm truncate">{getDisplayedTitle(material)}</p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {!children.length && (
              <Badge variant="outline" className="text-xs">
                {formatMaterialTypeLabel(material)}
              </Badge>
            )}
            {pageRange && (
              <SourcePageBadge
                label={pageRange}
                onClick={() => onOpenPdfPreview(material)}
                title={`查看素材原文 ${pageRange}`}
              />
            )}
            {children.length > 0 && (
              <Badge variant="secondary" className="text-xs">
                {usingWhole ? '整份使用中' : `已拆分 ${children.length} 个子素材`}
              </Badge>
            )}
            {material.kbSourceId && (
              <Badge variant="outline" className="text-xs border-blue-200 text-blue-700">
                {material.kbAtomId ? '知识库原子' : '知识库'} v{material.kbSourceVersion || '1'}
              </Badge>
            )}
            {material.splitReviewStatus === 'needs_review' && (
              <Badge variant="outline" className="text-xs border-amber-300 text-amber-700">
                待确认拆分
              </Badge>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {statusInfo && (
            <span
              className={`flex items-center gap-1 text-xs ${
                material.processingStatus === 'completed'
                  ? 'text-green-600'
                  : material.processingStatus === 'failed'
                    ? 'text-red-600'
                    : material.processingStatus === 'processing'
                      ? 'text-blue-600'
                      : 'text-yellow-600'
              }`}
            >
              {material.processingStatus === 'completed' && (
                <CheckCircle2 className="h-3.5 w-3.5" />
              )}
              {material.processingStatus === 'processing' && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              {material.processingStatus === 'failed' && (
                <AlertTriangle className="h-3.5 w-3.5" />
              )}
              {statusInfo.label}
            </span>
          )}
          {material.filePath?.toLowerCase().endsWith('.pdf') && (
            <Button variant="outline" size="sm" onClick={() => onOpenPdfPreview(material)}>
              查看原文
            </Button>
          )}
          {children.length > 0 &&
            (usingWhole ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onMaterialAction(material.id, 'use_split', '已恢复拆分素材使用')}
                disabled={isProcessing}
              >
                保留拆分
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onMaterialAction(material.id, 'use_whole', '已切换为整份使用')}
                disabled={isProcessing}
              >
                整份使用
              </Button>
            ))}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onDelete(material.id)}
            disabled={isProcessing}
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </div>

      {children.length > 0 && (
        <div className="mt-3 ml-4 border-l pl-4 space-y-3">
          {CHILD_GROUP_ORDER.map((groupKey) => {
            const groupChildren = childGroups?.[groupKey] || []
            if (groupChildren.length === 0) return null
            const groupMeta = CHILD_GROUP_META[groupKey]
            return (
              <details
                key={groupKey}
                className="rounded-lg border bg-muted/20"
                open={groupMeta.defaultOpen}
              >
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
                  <div className="flex items-center justify-between gap-3">
                    <span>{groupMeta.label}</span>
                    <Badge variant="outline" className="text-xs">
                      {groupChildren.length} 项
                    </Badge>
                  </div>
                </summary>
                <div className="px-3 pb-3 space-y-2">
                  {groupChildren.map((child) => {
                    const ignored = child.splitReviewStatus === 'ignored'
                    return (
                      <ChildMaterialItem
                        key={child.id}
                        child={child}
                        ignored={ignored}
                        editingMaterialId={editingMaterialId}
                        editingTitleValue={editingTitleValue}
                        pendingTitleEdits={pendingTitleEdits}
                        isProcessing={isProcessing}
                        savingPendingTitles={savingPendingTitles}
                        getDisplayedTitle={getDisplayedTitle}
                        onChangeTitleValue={onChangeTitleValue}
                        onCommitTitleEdit={onCommitTitleEdit}
                        onCancelTitleEdit={onCancelTitleEdit}
                        onStartEditTitle={onStartEditTitle}
                        onOpenPdfPreview={onOpenPdfPreview}
                        onIgnoreOrRestore={() =>
                          onMaterialAction(
                            child.id,
                            ignored ? 'restore_child' : 'ignore_child',
                            ignored ? '已恢复子素材' : '已忽略子素材',
                          )
                        }
                      />
                    )
                  })}
                </div>
              </details>
            )
          })}
        </div>
      )}
    </div>
  )
}
