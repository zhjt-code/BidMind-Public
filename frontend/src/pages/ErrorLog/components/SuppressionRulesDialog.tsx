import React from 'react'
import { Loader2, ToggleLeft, ToggleRight, Trash2 } from 'lucide-react'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import type { SuppressionRule } from '../types'

interface SuppressionRulesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  rules: SuppressionRule[]
  loading: boolean
  total: number
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  onToggleRule: (ruleId: number, enabled: boolean) => void
  onDeleteRule: (ruleId: number) => void
}

export const SuppressionRulesDialog: React.FC<SuppressionRulesDialogProps> = ({
  open, onOpenChange, rules, loading, total, page, totalPages,
  onPageChange, onToggleRule, onDeleteRule,
}) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>日志抑制规则管理</DialogTitle>
          <DialogDescription>
            共 {total} 条规则。匹配的日志不再写入数据库，但仍保留在服务器日志文件中。
          </DialogDescription>
        </DialogHeader>
        <div className="flex-1 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
            </div>
          ) : rules.length === 0 ? (
            <div className="text-center py-12 text-gray-400 text-sm">暂无抑制规则</div>
          ) : (
            <div className="space-y-2">
              {rules.map(rule => (
                <div key={rule.id} className={`border rounded-lg p-3 ${rule.enabled ? 'bg-white' : 'bg-gray-50 opacity-70'}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-[11px] px-1.5 py-0.5 rounded border ${rule.enabled ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'}`}>
                          {rule.enabled ? '启用中' : '已禁用'}
                        </span>
                        {rule.level && (
                          <span className="text-[11px] text-gray-500">{rule.level}</span>
                        )}
                        <span className="text-[11px] text-gray-400 font-mono">{rule.module}</span>
                      </div>
                      <p className="text-xs text-gray-700 break-all">{rule.message_prefix}</p>
                      <div className="flex items-center gap-4 text-[11px] text-gray-400">
                        <span>创建: {rule.created_by}</span>
                        <span>拦截: {rule.suppressed_count} 条</span>
                        {rule.last_suppressed_at && (
                          <span>最后拦截: {new Date(rule.last_suppressed_at).toLocaleString('zh-CN', { hour12: false })}</span>
                        )}
                        {rule.created_at && (
                          <span>{new Date(rule.created_at).toLocaleDateString('zh-CN')}</span>
                        )}
                      </div>
                      {rule.reason && (
                        <p className="text-[11px] text-gray-500">原因: {rule.reason}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2"
                        title={rule.enabled ? '禁用' : '启用'}
                        onClick={() => onToggleRule(rule.id, !rule.enabled)}
                      >
                        {rule.enabled
                          ? <ToggleRight className="h-4 w-4 text-green-600" />
                          : <ToggleLeft className="h-4 w-4 text-gray-400" />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-red-500 hover:text-red-700"
                        onClick={() => onDeleteRule(rule.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 pt-3 border-t">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
              上一页
            </Button>
            <span className="text-sm text-gray-500">{page} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
              下一页
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
