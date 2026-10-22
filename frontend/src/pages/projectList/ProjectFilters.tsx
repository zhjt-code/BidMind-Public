import React from 'react'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Search, X, User, ListFilter, CalendarClock, ArrowDownAZ } from 'lucide-react'
import {
  SORT_OPTIONS,
  STATUS_FILTER_OPTIONS,
  UPDATED_WITHIN_OPTIONS,
  type SortKey,
  type StatusFilter,
  type UpdatedWithin,
} from './types'

interface ProjectFiltersProps {
  searchInput: string
  onSearchChange: (value: string) => void
  sortKey: SortKey
  onSortChange: (key: SortKey) => void
  statusFilter: StatusFilter
  onStatusFilterChange: (filter: StatusFilter) => void
  updatedWithin: UpdatedWithin
  onUpdatedWithinChange: (value: UpdatedWithin) => void
  isAdmin: boolean
  usernameSearch?: string
  onUsernameSearchChange?: (value: string) => void
}

const ProjectFilters: React.FC<ProjectFiltersProps> = ({
  searchInput,
  onSearchChange,
  sortKey,
  onSortChange,
  statusFilter,
  onStatusFilterChange,
  updatedWithin,
  onUpdatedWithinChange,
  isAdmin,
  usernameSearch,
  onUsernameSearchChange,
}) => {
  return (
    <div className="mb-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <Input
            value={searchInput}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="搜索项目名称..."
            className="pl-9 pr-9"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => onSearchChange('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {isAdmin && onUsernameSearchChange && (
          <div className="relative w-full flex-shrink-0 lg:w-[200px]">
            <User className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input
              value={usernameSearch ?? ''}
              onChange={(e) => onUsernameSearchChange(e.target.value)}
              placeholder="搜索用户名..."
              className="pl-9 pr-9"
            />
            {usernameSearch && (
              <button
                type="button"
                onClick={() => onUsernameSearchChange('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}

        <Select value={statusFilter} onValueChange={(v) => onStatusFilterChange(v as StatusFilter)}>
          <SelectTrigger className="w-full lg:w-[140px]">
            {/* div 避开 SelectTrigger [&>span]:line-clamp-1 把横向 flex 改成纵向堆叠 */}
            <div className="flex min-w-0 items-center gap-1.5">
              <ListFilter className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <SelectValue placeholder="全部状态" />
            </div>
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTER_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={updatedWithin} onValueChange={(v) => onUpdatedWithinChange(v as UpdatedWithin)}>
          <SelectTrigger className="w-full lg:w-[140px]">
            <div className="flex min-w-0 items-center gap-1.5">
              <CalendarClock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <SelectValue placeholder="更新时间" />
            </div>
          </SelectTrigger>
          <SelectContent>
            {UPDATED_WITHIN_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={sortKey} onValueChange={(v) => onSortChange(v as SortKey)}>
          <SelectTrigger className="w-full lg:w-[150px]">
            <div className="flex min-w-0 items-center gap-1.5">
              <ArrowDownAZ className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <SelectValue placeholder="排序方式" />
            </div>
          </SelectTrigger>
          <SelectContent>
            {SORT_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}

export default ProjectFilters
