import React from 'react'
import {
  Loader2, ChevronDown, ChevronRight, Check,
  BellOff, Link2, Crosshair, History,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ErrorLogItem } from '../types'
import { LEVEL_CONFIG, SOURCE_CONFIG, TASK_LABELS, STAGE_LABELS } from '../constants'

const formatTime = (iso: string | null) => {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

const Detail: React.FC<{ label: string; value?: string | null; mono?: boolean }> = ({ label, value, mono }) => {
  if (!value) return null
  return (
    <div className="flex items-center gap-2">
      <span className="text-gray-400 w-16 text-right shrink-0">{label}</span>
      <span className={`text-gray-700 truncate ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}

interface ErrorLogRowProps {
  log: ErrorLogItem
  detail: ErrorLogItem | null
  detailLoading: boolean
  expanded: boolean
  onToggle: () => void
  onResolve: (id: number) => void
  onSuppress: (log: ErrorLogItem) => void
  selectionMode: boolean
  selected: boolean
  onToggleSelect: (id: number) => void
  onOpenChain?: (incidentId: string) => void
  onOpenProjectTimeline?: (projectId: number) => void
}

export const ErrorLogRow: React.FC<ErrorLogRowProps> = ({
  log, detail, detailLoading, expanded, onToggle, onResolve, onSuppress,
  selectionMode, selected, onToggleSelect, onOpenChain, onOpenProjectTimeline,
}) => {
  const levelCfg = LEVEL_CONFIG[log.level] || LEVEL_CONFIG.ERROR
  const LevelIcon = levelCfg.icon
  const sourceCfg = SOURCE_CONFIG[log.source] || SOURCE_CONFIG.backend

  return (
    <div className={`border rounded-lg mb-2 transition-colors ${log.resolved ? 'bg-gray-50 opacity-70' : 'bg-white'} ${selected ? 'ring-2 ring-primary/30 border-primary/40' : ''}`}>
      <div
        className="flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50/50"
        onClick={selectionMode ? () => onToggleSelect(log.id) : onToggle}
      >
        {selectionMode && (
          <input
            type="checkbox"
            className="h-4 w-4 mt-0.5 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer shrink-0"
            checked={selected}
            onChange={() => onToggleSelect(log.id)}
            onClick={e => e.stopPropagation()}
          />
        )}

        {!selectionMode && (
          expanded
            ? <ChevronDown className="h-4 w-4 text-gray-400 mt-0.5 shrink-0" />
            : <ChevronRight className="h-4 w-4 text-gray-400 mt-0.5 shrink-0" />
        )}

        <LevelIcon className={`h-4 w-4 mt-0.5 shrink-0 ${
          log.level === 'CRITICAL' ? 'text-red-600' : log.level === 'ERROR' ? 'text-red-500' : 'text-amber-500'
        }`} />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium border ${levelCfg.className}`}>
              {levelCfg.label}
            </span>
            {log.is_root_cause && (
              <span
                className="text-[11px] text-red-700 bg-red-50 border border-red-200 px-1.5 py-0.5 rounded inline-flex items-center gap-0.5"
                title="同一错误链路的根因（最早入库的一条）"
              >
                <Crosshair className="h-3 w-3" />根因
              </span>
            )}
            {log.exception_type && (
              <span
                className="text-[11px] text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded font-mono"
                title="抛出的异常类型"
              >
                {log.exception_type}
              </span>
            )}
            <span className="text-[11px] text-gray-400 font-mono">{sourceCfg.label}</span>
            {log.task_type && (
              <span className="text-[11px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">
                {TASK_LABELS[log.task_type] || log.task_type}
              </span>
            )}
            {log.username && (
              <span className="text-[11px] text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded">
                {log.username}
              </span>
            )}
            {log.project_id && (
              <span className="text-[11px] text-gray-500">
                {log.project_name || `P-${log.project_id}`}
              </span>
            )}
            {!log.username && !log.project_id && !log.task_type && (
              <span
                className="text-[11px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded"
                title="该日志缺少用户/项目/任务上下文，可能源于未受 logger.contextualize 覆盖的代码路径"
              >
                无项目关联
              </span>
            )}
            {log.resolved && (
              <span className="text-[11px] text-green-600 bg-green-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                <Check className="h-3 w-3" />已处理
              </span>
            )}
          </div>
          <p className="text-sm text-gray-800 truncate">{log.message}</p>
          <div className="flex items-center gap-3 mt-1 text-[11px] text-gray-400 flex-wrap">
            <span>{formatTime(log.created_at)}</span>
            {log.origin_module ? (
              <span
                className="font-mono text-rose-500"
                title="根因位置（traceback 末端帧），区别于 logger 调用点"
              >
                {log.origin_module}
                {log.origin_function ? `:${log.origin_function}` : ''}
                {log.origin_line ? `:L${log.origin_line}` : ''}
              </span>
            ) : (
              log.module && <span className="font-mono">{log.module}</span>
            )}
            {log.request_id && <span className="font-mono">req:{log.request_id}</span>}
            {log.incident_id && onOpenChain && !selectionMode && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onOpenChain(log.incident_id as string)
                }}
                className="font-mono text-blue-500 hover:text-blue-700 inline-flex items-center gap-0.5"
                title="查看本次失败的完整错误链路"
              >
                <Link2 className="h-3 w-3" />链路 {log.incident_id.slice(0, 8)}
              </button>
            )}
          </div>
        </div>
      </div>

      {!selectionMode && expanded && (
        <div className="px-4 pb-4 pt-0">
          <div className="bg-gray-50 rounded-lg p-4 space-y-3">
            {(log.exception_type || log.origin_module || log.incident_id) && (
              <div className="border border-rose-200 bg-rose-50/50 rounded-md px-3 py-2 space-y-1 text-xs">
                <div className="flex items-center gap-2 flex-wrap">
                  <Crosshair className="h-3.5 w-3.5 text-rose-500" />
                  <span className="font-medium text-rose-700">根因定位</span>
                  {log.is_root_cause && (
                    <span className="text-[11px] text-red-700 bg-red-100 px-1.5 py-0.5 rounded">
                      本链路根因
                    </span>
                  )}
                </div>
                {log.exception_type && (
                  <div className="flex gap-2">
                    <span className="text-gray-500 w-16 text-right shrink-0">异常类型</span>
                    <span className="text-rose-600 font-mono">{log.exception_type}</span>
                  </div>
                )}
                {log.origin_module && (
                  <div className="flex gap-2">
                    <span className="text-gray-500 w-16 text-right shrink-0">异常位置</span>
                    <span className="text-gray-700 font-mono break-all">
                      {log.origin_module}
                      {log.origin_function ? `:${log.origin_function}` : ''}
                      {log.origin_line ? `:L${log.origin_line}` : ''}
                    </span>
                  </div>
                )}
                {log.incident_id && (
                  <div className="flex gap-2 items-center">
                    <span className="text-gray-500 w-16 text-right shrink-0">链路ID</span>
                    <span className="text-gray-700 font-mono">{log.incident_id}</span>
                    {onOpenChain && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onOpenChain(log.incident_id as string) }}
                        className="text-blue-500 hover:text-blue-700 inline-flex items-center gap-0.5 text-[11px]"
                      >
                        <Link2 className="h-3 w-3" />查看完整链路
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
              <Detail label="模块" value={log.module} />
              <Detail label="函数" value={log.function_name} />
              <Detail label="行号" value={log.line_number?.toString()} />
              <Detail label="来源" value={SOURCE_CONFIG[log.source]?.label || log.source} />
              <Detail label="请求ID" value={log.request_id} mono />
              <Detail label="用户" value={log.username || (log.user_id ? `ID: ${log.user_id}` : null)} />
              {log.project_id ? (
                <div className="flex items-center gap-2">
                  <span className="text-gray-400 w-16 text-right shrink-0">项目</span>
                  <span className="text-gray-700 truncate">{log.project_name || `ID: ${log.project_id}`}</span>
                  {onOpenProjectTimeline && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onOpenProjectTimeline(log.project_id as number) }}
                      className="text-blue-500 hover:text-blue-700 inline-flex items-center gap-0.5"
                      title="查看该项目的全部系统日志"
                    >
                      <History className="h-3 w-3" />日志
                    </button>
                  )}
                </div>
              ) : (
                <Detail label="项目" value={null} />
              )}
              <Detail label="任务类型" value={log.task_type ? (TASK_LABELS[log.task_type] || log.task_type) : null} />
              <Detail label="执行阶段" value={log.stage ? (STAGE_LABELS[log.stage] || log.stage) : null} />
            </div>

            <div>
              <p className="text-xs text-gray-500 mb-1">完整消息</p>
              <pre className="text-xs bg-gray-900 text-gray-100 rounded p-3 overflow-x-auto whitespace-pre-wrap wrap-break-word max-h-32">
                {log.message}
              </pre>
            </div>

            {detailLoading && (
              <div className="flex items-center gap-2 text-xs text-gray-400 py-2">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载堆栈信息...
              </div>
            )}

            {detail?.traceback && (
              <div>
                <p className="text-xs text-gray-500 mb-1">堆栈追踪</p>
                <pre className="text-xs bg-gray-900 text-gray-100 rounded p-3 overflow-x-auto whitespace-pre-wrap wrap-break-word max-h-64">
                  {detail.traceback}
                </pre>
              </div>
            )}

            <div className="flex justify-end pt-1 gap-2">
              <Button
                size="sm"
                variant="ghost"
                className="text-gray-500 hover:text-orange-600"
                onClick={(e) => { e.stopPropagation(); onSuppress(log) }}
              >
                <BellOff className="h-3.5 w-3.5 mr-1" />
                不再追踪此类
              </Button>
              {!log.resolved && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => { e.stopPropagation(); onResolve(log.id) }}
                >
                  <Check className="h-3.5 w-3.5 mr-1" />
                  标记已处理
                </Button>
              )}
            </div>
            {log.resolved && log.resolved_by && (
              <p className="text-[11px] text-gray-400 text-right">
                由 {log.resolved_by} 于 {formatTime(log.resolved_at)} 标记
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
