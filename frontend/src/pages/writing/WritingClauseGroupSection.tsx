/**
 * 职责: 单个约束分组的组头(标签+计数+确认进度+折叠开关+组级全部确认)与折叠容器
 * 不做: 不渲染约束卡片本体(children 传入)、不发请求(onConfirmGroup 回调上抛)
 */
import React from 'react'
import { ChevronDown, Check, Loader2, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

interface WritingClauseGroupSectionProps {
  title: string
  total: number
  confirmedCount: number
  collapsed: boolean
  onToggleCollapsed: () => void
  onConfirmGroup: () => void
  confirming?: boolean
  substantive?: boolean
  /** 行业画像等单条展示场景隐藏组头, 直接渲染 children */
  hideHeader?: boolean
  children: React.ReactNode
}

export const WritingClauseGroupSection: React.FC<WritingClauseGroupSectionProps> = ({
  title,
  total,
  confirmedCount,
  collapsed,
  onToggleCollapsed,
  onConfirmGroup,
  confirming = false,
  substantive = false,
  hideHeader = false,
  children,
}) => {
  if (hideHeader) {
    return <div className="space-y-3">{children}</div>
  }

  const unconfirmed = total - confirmedCount
  const allConfirmed = total > 0 && unconfirmed === 0

  return (
    <div className={substantive ? 'rounded-lg border border-amber-300 bg-amber-50/40 p-3' : ''}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="flex min-w-0 items-center gap-1.5 text-left"
        >
          <ChevronDown
            className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${collapsed ? '-rotate-90' : ''}`}
          />
          {substantive && <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />}
          <h3 className={`truncate text-sm font-medium ${substantive ? 'text-amber-900' : 'text-muted-foreground'}`}>
            {title}
          </h3>
          <span className="shrink-0 text-xs text-muted-foreground">({total})</span>
          {allConfirmed ? (
            <Badge className="bg-green-100 text-green-800 text-xs shrink-0">已全部确认</Badge>
          ) : (
            <span className="shrink-0 text-xs text-muted-foreground">{confirmedCount}/{total} 已确认</span>
          )}
        </button>
        {unconfirmed > 0 && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 shrink-0 px-2 text-xs"
            onClick={onConfirmGroup}
            disabled={confirming}
            title={`确认本组 ${unconfirmed} 条`}
          >
            {confirming ? (
              <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
            ) : (
              <Check className="h-3.5 w-3.5 mr-1" />
            )}
            确认本组 ({unconfirmed})
          </Button>
        )}
      </div>
      {!collapsed && <div className="space-y-3">{children}</div>}
    </div>
  )
}

export default WritingClauseGroupSection
