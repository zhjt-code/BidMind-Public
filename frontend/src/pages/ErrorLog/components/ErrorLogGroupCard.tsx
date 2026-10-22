import React from 'react'
import { Loader2, ChevronDown, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ErrorLogItem, ErrorLogGroup } from '../types'
import { LEVEL_CONFIG, SOURCE_CONFIG, CATEGORY_LABELS } from '../constants'
import { formatRelativeTime } from '../utils'
import { ErrorLogRow } from './ErrorLogRow'

interface ErrorLogGroupCardProps {
  group: ErrorLogGroup
  expanded: boolean
  onToggle: () => void
  innerLogs: ErrorLogItem[]
  innerTotal: number
  innerPage: number
  innerTotalPages: number
  innerLoading: boolean
  onInnerPageChange: (page: number) => void
  detailCache: Record<number, ErrorLogItem>
  detailLoadingId: number | null
  expandedLogId: number | null
  onLogToggle: (id: number) => void
  onResolve: (id: number) => void
  onSuppress: (log: ErrorLogItem) => void
  selectionMode: boolean
  groupSelected: boolean
  onToggleGroupSelect: (fingerprint: string) => void
  onOpenChain?: (incidentId: string) => void
  onOpenProjectTimeline?: (projectId: number) => void
}

export const ErrorLogGroupCard: React.FC<ErrorLogGroupCardProps> = ({
  group, expanded, onToggle,
  innerLogs, innerTotal, innerPage, innerTotalPages, innerLoading,
  onInnerPageChange,
  detailCache, detailLoadingId, expandedLogId,
  onLogToggle, onResolve, onSuppress,
  selectionMode, groupSelected, onToggleGroupSelect,
  onOpenChain, onOpenProjectTimeline,
}) => {
  const levelCfg = LEVEL_CONFIG[group.level] || LEVEL_CONFIG.ERROR
  const LevelIcon = levelCfg.icon
  const sourceCfg = SOURCE_CONFIG[group.source || 'backend'] || SOURCE_CONFIG.backend

  return (
    <div className={`border rounded-lg mb-2 transition-colors bg-white ${groupSelected ? 'ring-2 ring-primary/30 border-primary/40' : ''}`}>
      <div
        className="flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50/50"
        onClick={selectionMode ? () => onToggleGroupSelect(group.fingerprint) : onToggle}
      >
        {selectionMode && (
          <input
            type="checkbox"
            className="h-4 w-4 mt-0.5 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer flex-shrink-0"
            checked={groupSelected}
            onChange={() => onToggleGroupSelect(group.fingerprint)}
            onClick={e => e.stopPropagation()}
          />
        )}

        {!selectionMode && (
          expanded
            ? <ChevronDown className="h-4 w-4 text-gray-400 mt-0.5 flex-shrink-0" />
            : <ChevronRight className="h-4 w-4 text-gray-400 mt-0.5 flex-shrink-0" />
        )}

        <LevelIcon className={`h-4 w-4 mt-0.5 flex-shrink-0 ${
          group.level === 'CRITICAL' ? 'text-red-600' : group.level === 'ERROR' ? 'text-red-500' : 'text-amber-500'
        }`} />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium border ${levelCfg.className}`}>
              {levelCfg.label}
            </span>
            <span className="text-[11px] text-gray-400 font-mono">{sourceCfg.label}</span>
            {group.module && (
              <span className="text-[11px] text-gray-500 font-mono truncate max-w-[200px]" title={group.module}>
                {group.module}
              </span>
            )}
            {group.category && group.category !== 'system' && (
              <span className="text-[11px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">
                {CATEGORY_LABELS[group.category] || group.category}
              </span>
            )}
          </div>
          <p className="text-sm text-gray-800 truncate">{group.sample_message}</p>
          <div className="flex items-center gap-3 mt-1 text-[11px] text-gray-400 flex-wrap">
            {group.unresolved_count > 0 && (
              <span className="text-red-500">未处理 {group.unresolved_count}</span>
            )}
            {(group.user_count > 0 || group.project_count > 0) && (
              <span
                className="text-purple-500"
                title="该错误的影响面：涉及独立用户数 / 项目数（来自所有匹配筛选的日志）"
              >
                影响 {group.user_count} 用户 / {group.project_count} 项目
              </span>
            )}
            <span>{formatRelativeTime(group.latest_at)}</span>
            {group.earliest_at !== group.latest_at && (
              <span>~ {formatRelativeTime(group.earliest_at)}</span>
            )}
          </div>
        </div>

        <span className="flex-shrink-0 inline-flex items-center justify-center min-w-[36px] h-6 px-2 rounded-full bg-gray-100 text-xs font-semibold text-gray-600">
          x{group.count}
        </span>
      </div>

      {!selectionMode && expanded && (
        <div className="border-t px-4 py-3 bg-gray-50/50">
          {innerLoading && innerLogs.length === 0 ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
            </div>
          ) : innerLogs.length === 0 ? (
            <div className="text-center py-6 text-gray-400 text-sm">暂无明细</div>
          ) : (
            <>
              <div className="text-[11px] text-gray-400 mb-2">
                共 {innerTotal} 条, 第 {innerPage}/{innerTotalPages} 页
              </div>
              {innerLogs.map(log => (
                <ErrorLogRow
                  key={log.id}
                  log={log}
                  detail={detailCache[log.id] || null}
                  detailLoading={detailLoadingId === log.id}
                  expanded={expandedLogId === log.id}
                  onToggle={() => onLogToggle(log.id)}
                  onResolve={onResolve}
                  onSuppress={onSuppress}
                  selectionMode={false}
                  selected={false}
                  onToggleSelect={() => {}}
                  onOpenChain={onOpenChain}
                  onOpenProjectTimeline={onOpenProjectTimeline}
                />
              ))}
              {innerTotalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-2">
                  <Button variant="outline" size="sm" disabled={innerPage <= 1} onClick={() => onInnerPageChange(innerPage - 1)}>
                    上一页
                  </Button>
                  <span className="text-xs text-gray-500">{innerPage}/{innerTotalPages}</span>
                  <Button variant="outline" size="sm" disabled={innerPage >= innerTotalPages} onClick={() => onInnerPageChange(innerPage + 1)}>
                    下一页
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
