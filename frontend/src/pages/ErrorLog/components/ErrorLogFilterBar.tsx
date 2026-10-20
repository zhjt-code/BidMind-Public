import React from 'react'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { CATEGORY_LABELS, TIME_RANGE_OPTIONS } from '../constants'

interface ErrorLogFilterBarProps {
  categoryFilter: string
  taskFilter: string
  levelFilter: string
  sourceFilter: string
  resolvedFilter: string
  timeRange: number
  searchInput: string
  currentTaskOptions: Record<string, string>
  hasFilters: boolean
  onCategoryChange: (v: string) => void
  onTaskChange: (v: string) => void
  onLevelChange: (v: string) => void
  onSourceChange: (v: string) => void
  onResolvedChange: (v: string) => void
  onTimeRangeChange: (v: number) => void
  onSearchInputChange: (v: string) => void
  onSearch: () => void
  onReset: () => void
}

export const ErrorLogFilterBar: React.FC<ErrorLogFilterBarProps> = ({
  categoryFilter, taskFilter, levelFilter, sourceFilter, resolvedFilter,
  timeRange, searchInput, currentTaskOptions, hasFilters,
  onCategoryChange, onTaskChange, onLevelChange, onSourceChange, onResolvedChange,
  onTimeRangeChange, onSearchInputChange, onSearch, onReset,
}) => {
  return (
    <div className="flex items-center gap-3 mb-4 flex-wrap">
      <Select value={categoryFilter} onValueChange={onCategoryChange}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="业务类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部业务</SelectItem>
          {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
            <SelectItem key={key} value={key}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={taskFilter} onValueChange={onTaskChange}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="任务阶段" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部任务</SelectItem>
          {Object.entries(currentTaskOptions).map(([key, label]) => (
            <SelectItem key={key} value={key}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={levelFilter} onValueChange={onLevelChange}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="级别" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部级别</SelectItem>
          <SelectItem value="WARNING">警告</SelectItem>
          <SelectItem value="ERROR">错误</SelectItem>
          <SelectItem value="CRITICAL">严重</SelectItem>
        </SelectContent>
      </Select>

      <Select value={sourceFilter} onValueChange={onSourceChange}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="来源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部来源</SelectItem>
          <SelectItem value="backend">接口服务</SelectItem>
          <SelectItem value="worker">异步任务</SelectItem>
        </SelectContent>
      </Select>

      <Select value={resolvedFilter} onValueChange={onResolvedChange}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部状态</SelectItem>
          <SelectItem value="unresolved">未处理</SelectItem>
          <SelectItem value="resolved">已处理</SelectItem>
        </SelectContent>
      </Select>

      <Select value={String(timeRange)} onValueChange={v => onTimeRangeChange(Number(v))}>
        <SelectTrigger className="w-32">
          <SelectValue placeholder="时间范围" />
        </SelectTrigger>
        <SelectContent>
          {TIME_RANGE_OPTIONS.map(opt => (
            <SelectItem key={opt.hours} value={String(opt.hours)}>{opt.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="flex items-center gap-1">
        <Input
          placeholder="搜索消息关键词..."
          value={searchInput}
          onChange={e => onSearchInputChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onSearch()}
          className="w-52 h-9"
        />
        <Button size="sm" variant="outline" onClick={onSearch}>
          搜索
        </Button>
      </div>

      {hasFilters && (
        <Button variant="ghost" size="sm" onClick={onReset}>
          重置筛选
        </Button>
      )}
    </div>
  )
}
