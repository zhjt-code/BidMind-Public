import React from 'react'
import { Loader2, CheckCheck, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ErrorLogBatchBarProps {
  viewMode: 'grouped' | 'flat'
  logsLength: number
  groupsLength: number
  total: number
  allCurrentPageSelected: boolean
  currentPageSelectedCount: number
  allMatchingSelected: boolean
  excludedIdsSize: number
  selectedCount: number
  selectedFingerprintsSize: number
  batchDeleting: boolean
  batchResolving: boolean
  onToggleSelectAll: () => void
  onSelectAllMatching: () => void
  onClearSelection: () => void
  onBatchResolve: () => void
  onBatchDelete: () => void
}

export const ErrorLogBatchBar: React.FC<ErrorLogBatchBarProps> = ({
  viewMode, logsLength, groupsLength, total,
  allCurrentPageSelected, currentPageSelectedCount,
  allMatchingSelected, excludedIdsSize,
  selectedCount, selectedFingerprintsSize,
  batchDeleting, batchResolving,
  onToggleSelectAll, onSelectAllMatching, onClearSelection,
  onBatchResolve, onBatchDelete,
}) => {
  const visible = viewMode === 'flat' ? logsLength > 0 : groupsLength > 0
  if (!visible) return null

  return (
    <div className="px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-lg mb-3 flex items-center justify-between gap-3 flex-wrap">
      <div className="flex items-center gap-3 flex-wrap">
        {viewMode === 'flat' ? (
          <>
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                checked={allCurrentPageSelected}
                onChange={onToggleSelectAll}
              />
              全选当前页 ({currentPageSelectedCount}/{logsLength})
            </label>

            {!allMatchingSelected && total > logsLength && (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-primary hover:text-primary"
                onClick={onSelectAllMatching}
              >
                全选符合当前筛选的 {total} 条
              </Button>
            )}

            {allMatchingSelected && (
              <>
                <span className="text-sm text-primary">
                  已选中全部匹配日志 {selectedCount} 条
                  {excludedIdsSize > 0 ? `，已排除 ${excludedIdsSize} 条` : ''}
                </span>
                <Button variant="ghost" size="sm" className="h-8 px-2" onClick={onClearSelection}>
                  清空选择
                </Button>
              </>
            )}
          </>
        ) : (
          <span className="text-sm text-gray-600">
            已选中 {selectedFingerprintsSize} 组 ({selectedCount} 条)
            {selectedFingerprintsSize > 0 && (
              <Button variant="ghost" size="sm" className="h-8 px-2 ml-2" onClick={onClearSelection}>
                清空
              </Button>
            )}
          </span>
        )}
      </div>

      {selectedCount > 0 && (
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={batchResolving}
            onClick={onBatchResolve}
          >
            {batchResolving ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <CheckCheck className="h-4 w-4 mr-1" />
            )}
            批量标记已处理 ({selectedCount})
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={batchDeleting}
            onClick={onBatchDelete}
          >
            {batchDeleting ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4 mr-1" />
            )}
            批量删除 ({selectedCount})
          </Button>
        </div>
      )}
    </div>
  )
}
