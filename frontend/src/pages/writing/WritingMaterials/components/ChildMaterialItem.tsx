import React from 'react'
import { AlertTriangle, CheckCircle2, Images, Loader2, Pencil } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import SourcePageBadge from '@/components/SourcePageBadge'
import type { WritingMaterial } from '@/services/writingApi'
import {
  formatMaterialTypeLabel,
  formatPageRange,
  getAssetSubtitle,
  getPrimaryReviewBadge,
} from '../materialGrouping.ts'
import { PROCESSING_STATUS_BADGE } from '../constants.ts'

interface ChildMaterialItemProps {
  child: WritingMaterial
  ignored: boolean
  editingMaterialId: number | null
  editingTitleValue: string
  pendingTitleEdits: Record<number, string>
  isProcessing: boolean
  savingPendingTitles: boolean
  getDisplayedTitle: (material: WritingMaterial) => string
  onChangeTitleValue: (value: string) => void
  onCommitTitleEdit: () => void
  onCancelTitleEdit: () => void
  onStartEditTitle: (material: WritingMaterial) => void
  onOpenPdfPreview: (material: WritingMaterial) => void
  onIgnoreOrRestore: () => void
}

export const ChildMaterialItem: React.FC<ChildMaterialItemProps> = ({
  child,
  ignored,
  editingMaterialId,
  editingTitleValue,
  pendingTitleEdits,
  isProcessing,
  savingPendingTitles,
  getDisplayedTitle,
  onChangeTitleValue,
  onCommitTitleEdit,
  onCancelTitleEdit,
  onStartEditTitle,
  onOpenPdfPreview,
  onIgnoreOrRestore,
}) => {
  const childStatusInfo = PROCESSING_STATUS_BADGE[child.processingStatus]
  const childPageRange = formatPageRange(child)
  const reviewBadge = getPrimaryReviewBadge(child)

  return (
    <div className="rounded-lg border bg-white px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            {editingMaterialId === child.id ? (
              <Input
                autoFocus
                value={editingTitleValue}
                onChange={(e) => onChangeTitleValue(e.target.value)}
                onBlur={onCommitTitleEdit}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    onCommitTitleEdit()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    onCancelTitleEdit()
                  }
                }}
                className="h-8 text-sm"
                disabled={savingPendingTitles || isProcessing}
              />
            ) : (
              <>
                <button
                  type="button"
                  className="min-w-0 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-muted/60 focus-visible:outline-hidden focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={() => onStartEditTitle(child)}
                  disabled={isProcessing || savingPendingTitles}
                  title="点击直接编辑标题"
                >
                  <p
                    className={`cursor-text text-sm truncate ${ignored ? 'text-muted-foreground line-through' : 'font-medium'}`}
                  >
                    {getDisplayedTitle(child)}
                  </p>
                </button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-muted-foreground hover:text-foreground"
                  onClick={() => onStartEditTitle(child)}
                  disabled={isProcessing || savingPendingTitles}
                  title="编辑标题"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
              </>
            )}
          </div>
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            {childPageRange && (
              <SourcePageBadge
                label={childPageRange}
                onClick={() => onOpenPdfPreview(child)}
                title={`查看子素材原文 ${childPageRange}`}
              />
            )}
            {child.splitConfidence != null && (
              <Badge variant="outline" className="text-xs">
                置信度 {Math.round(child.splitConfidence * 100)}%
              </Badge>
            )}
            {reviewBadge && (
              <Badge variant="outline" className={`text-xs ${reviewBadge.className}`}>
                {reviewBadge.label}
              </Badge>
            )}
            {pendingTitleEdits[child.id] && editingMaterialId !== child.id && (
              <Badge variant="outline" className="text-xs border-amber-300 text-amber-700">
                未保存
              </Badge>
            )}
          </div>
          <div className="mt-2 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            <div className="flex items-center gap-2 flex-wrap">
              <span>文本类型: {formatMaterialTypeLabel(child)}</span>
              {child.structureHintLabel && <span>结构提示: {child.structureHintLabel}</span>}
              {child.imageCount > 0 && (
                <span>
                  图片资产: {child.validatedImageCount} 已识别 / {child.reviewImageCount} 待复核
                </span>
              )}
            </div>
          </div>
          {child.imageCount > 0 && (
            <details className="mt-2 rounded-md border bg-muted/20">
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium">
                <span className="inline-flex items-center gap-1">
                  <Images className="h-3.5 w-3.5" />
                  图片资产 {child.imageCount} 张
                </span>
              </summary>
              <div className="px-3 pb-3 space-y-2">
                {child.imageAssets.map((asset) => (
                  <div key={asset.id} className="rounded-md border bg-white px-3 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-medium truncate">
                          {asset.caption || `图片${asset.id}`}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {getAssetSubtitle(asset)}
                        </p>
                      </div>
                      <Badge
                        variant="outline"
                        className={`text-xs ${asset.reviewStatus === 'needs_review' ? 'border-amber-300 text-amber-700' : 'border-green-300 text-green-700'}`}
                      >
                        {asset.reviewStatus === 'needs_review' ? '待复核' : '已识别'}
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {childStatusInfo && (
            <span
              className={`flex items-center gap-1 text-xs ${
                child.processingStatus === 'completed'
                  ? 'text-green-600'
                  : child.processingStatus === 'failed'
                    ? 'text-red-600'
                    : child.processingStatus === 'processing'
                      ? 'text-blue-600'
                      : 'text-yellow-600'
              }`}
            >
              {child.processingStatus === 'completed' && <CheckCircle2 className="h-3.5 w-3.5" />}
              {child.processingStatus === 'processing' && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              {child.processingStatus === 'failed' && <AlertTriangle className="h-3.5 w-3.5" />}
              {childStatusInfo.label}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={onIgnoreOrRestore}
            disabled={isProcessing}
          >
            {ignored ? '恢复子素材' : '忽略子素材'}
          </Button>
        </div>
      </div>
    </div>
  )
}
