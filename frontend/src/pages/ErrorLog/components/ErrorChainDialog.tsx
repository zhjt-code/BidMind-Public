import React from 'react'
import { Loader2, Link2, Crosshair } from 'lucide-react'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import type { ErrorLogItem } from '../types'
import { LEVEL_CONFIG, TASK_LABELS } from '../constants'

interface ErrorChainDialogProps {
  incidentId: string | null
  items: ErrorLogItem[]
  loading: boolean
  onClose: () => void
}

export const ErrorChainDialog: React.FC<ErrorChainDialogProps> = ({
  incidentId, items, loading, onClose,
}) => {
  return (
    <Dialog open={!!incidentId} onOpenChange={open => { if (!open) onClose() }}>
      <DialogContent className="max-w-4xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-blue-500" />
            错误链路
            {incidentId && (
              <span className="text-xs text-gray-500 font-mono ml-2">{incidentId}</span>
            )}
          </DialogTitle>
          <DialogDescription>
            同一次失败触发的全部日志，按时间顺序排列，根因（最早一条）位于顶部。
            {items.length > 0 && (
              <span className="ml-2 text-blue-600">共 {items.length} 条</span>
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="flex-1 overflow-auto pr-1">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
            </div>
          ) : items.length === 0 ? (
            <div className="text-center py-12 text-gray-400 text-sm">
              未找到该链路的日志记录（可能已被清理或抑制）
            </div>
          ) : (
            <div className="space-y-2">
              {items.map((item, idx) => {
                const itemLevelCfg = LEVEL_CONFIG[item.level] || LEVEL_CONFIG.ERROR
                const ItemIcon = itemLevelCfg.icon
                return (
                  <div
                    key={item.id}
                    className={`border rounded-md px-3 py-2 ${
                      item.is_root_cause ? 'border-red-300 bg-red-50/30' : 'bg-white'
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-xs text-gray-400 font-mono w-8 shrink-0 pt-0.5">
                        #{item.chain_seq != null ? item.chain_seq : idx}
                      </span>
                      <ItemIcon className={`h-4 w-4 mt-0.5 shrink-0 ${
                        item.level === 'CRITICAL' ? 'text-red-600'
                          : item.level === 'ERROR' ? 'text-red-500' : 'text-amber-500'
                      }`} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium border ${itemLevelCfg.className}`}>
                            {itemLevelCfg.label}
                          </span>
                          {item.is_root_cause && (
                            <span className="text-[11px] text-red-700 bg-red-100 px-1.5 py-0.5 rounded inline-flex items-center gap-0.5">
                              <Crosshair className="h-3 w-3" />根因
                            </span>
                          )}
                          {item.exception_type && (
                            <span className="text-[11px] text-rose-600 bg-rose-50 px-1.5 py-0.5 rounded font-mono">
                              {item.exception_type}
                            </span>
                          )}
                          {item.created_at && (
                            <span className="text-[11px] text-gray-400">
                              {new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-gray-800 wrap-break-word">{item.message}</p>
                        <div className="flex items-center gap-3 mt-1 text-[11px] text-gray-500 flex-wrap">
                          {item.origin_module ? (
                            <span className="font-mono text-rose-500">
                              {item.origin_module}
                              {item.origin_function ? `:${item.origin_function}` : ''}
                              {item.origin_line ? `:L${item.origin_line}` : ''}
                            </span>
                          ) : (
                            item.module && <span className="font-mono">{item.module}</span>
                          )}
                          {item.task_type && (
                            <span>{TASK_LABELS[item.task_type] || item.task_type}</span>
                          )}
                          {item.username && <span>用户: {item.username}</span>}
                          {item.project_id && (
                            <span>项目: {item.project_name || `P-${item.project_id}`}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
