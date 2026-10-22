import React from 'react'
import {
  Layers, List, ArrowUpDown, RefreshCw, Settings2, ListChecks, X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ErrorLogHeaderToolbarProps {
  viewMode: 'grouped' | 'flat'
  onViewModeChange: (mode: 'grouped' | 'flat') => void
  groupSortBy: 'latest_at' | 'count'
  onGroupSortByChange: (sortBy: 'latest_at' | 'count') => void
  total: number
  groupTotal: number
  recomputingFp: boolean
  onRecomputeFingerprints: () => void
  onOpenRulesDialog: () => void
  selectionMode: boolean
  onEnterSelectionMode: () => void
  onExitSelectionMode: () => void
}

export const ErrorLogHeaderToolbar: React.FC<ErrorLogHeaderToolbarProps> = ({
  viewMode, onViewModeChange,
  groupSortBy, onGroupSortByChange,
  total, groupTotal,
  recomputingFp, onRecomputeFingerprints,
  onOpenRulesDialog,
  selectionMode, onEnterSelectionMode, onExitSelectionMode,
}) => {
  return (
    <div className="flex items-center justify-between mb-6">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">系统错误日志</h1>
          <span className="text-sm text-gray-500">
            {viewMode === 'grouped'
              ? `${groupTotal} 组 / 共 ${total} 条`
              : `共 ${total} 条记录`}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex items-center border rounded-md overflow-hidden">
          <button
            className={`px-2 py-1.5 text-xs flex items-center gap-1 transition-colors ${viewMode === 'grouped' ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            onClick={() => onViewModeChange('grouped')}
            title="分组视图"
          >
            <Layers className="h-3.5 w-3.5" />分组
          </button>
          <button
            className={`px-2 py-1.5 text-xs flex items-center gap-1 transition-colors ${viewMode === 'flat' ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
            onClick={() => onViewModeChange('flat')}
            title="列表视图"
          >
            <List className="h-3.5 w-3.5" />列表
          </button>
        </div>
        {viewMode === 'grouped' && (
          <div className="flex items-center border rounded-md overflow-hidden">
            <button
              className={`px-2 py-1.5 text-xs flex items-center gap-1 transition-colors ${groupSortBy === 'latest_at' ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => onGroupSortByChange('latest_at')}
              title="按最新时间排序"
            >
              <ArrowUpDown className="h-3.5 w-3.5" />按时间
            </button>
            <button
              className={`px-2 py-1.5 text-xs flex items-center gap-1 transition-colors ${groupSortBy === 'count' ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => onGroupSortByChange('count')}
              title="按出现次数排序"
            >
              <ArrowUpDown className="h-3.5 w-3.5" />按数量
            </button>
          </div>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={onRecomputeFingerprints}
          disabled={recomputingFp}
          title="重新计算指纹以改进分组效果"
        >
          <RefreshCw className={`h-4 w-4 mr-1 ${recomputingFp ? 'animate-spin' : ''}`} />
          刷新分组
        </Button>
        <Button variant="outline" size="sm" onClick={onOpenRulesDialog}>
          <Settings2 className="h-4 w-4 mr-1" />抑制规则
        </Button>
        <Button
          variant={selectionMode ? 'default' : 'outline'}
          size="sm"
          onClick={() => selectionMode ? onExitSelectionMode() : onEnterSelectionMode()}
        >
          {selectionMode ? (
            <><X className="h-4 w-4 mr-1" />退出批量管理</>
          ) : (
            <><ListChecks className="h-4 w-4 mr-1" />批量管理</>
          )}
        </Button>
      </div>
    </div>
  )
}
