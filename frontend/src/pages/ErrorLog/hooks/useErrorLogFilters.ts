import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TASK_LABELS, TASK_BY_CATEGORY } from '../constants'

const DEFAULT_TIME_RANGE = 24

let sampledStartDate: string | undefined

/** 请求参数附上最近一次筛选变更时采样的 start_date。 */
export function withSampledStartDate(filters: Record<string, unknown>): Record<string, unknown> {
  if (sampledStartDate == null) return filters
  return { ...filters, start_date: sampledStartDate }
}

export interface UseErrorLogFiltersResult {
  categoryFilter: string
  taskFilter: string
  levelFilter: string
  sourceFilter: string
  resolvedFilter: string
  timeRange: number
  keyword: string
  searchInput: string
  setCategoryFilter: (v: string) => void
  setTaskFilter: (v: string) => void
  setLevelFilter: (v: string) => void
  setSourceFilter: (v: string) => void
  setResolvedFilter: (v: string) => void
  setTimeRange: (v: number) => void
  setSearchInput: (v: string) => void
  activeFilters: Record<string, unknown>
  currentTaskOptions: Record<string, string>
  hasFilters: boolean
  handleSearch: () => void
  resetFilters: () => void
}

export function useErrorLogFilters(): UseErrorLogFiltersResult {
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const [taskFilter, setTaskFilter] = useState<string>('all')
  const [levelFilter, setLevelFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<string>('all')
  const [resolvedFilter, setResolvedFilter] = useState<string>('all')
  const [timeRange, setTimeRange] = useState<number>(DEFAULT_TIME_RANGE)
  const [keyword, setKeyword] = useState('')
  const [searchInput, setSearchInput] = useState('')

  const [prevCategoryFilter, setPrevCategoryFilter] = useState(categoryFilter)
  if (categoryFilter !== prevCategoryFilter) {
    setPrevCategoryFilter(categoryFilter)
    setTaskFilter('all')
  }

  const currentTaskOptions = useMemo(() => {
    if (categoryFilter === 'all') return TASK_LABELS
    return TASK_BY_CATEGORY[categoryFilter] || {}
  }, [categoryFilter])

  // 时间窗锚点在筛选变更的 effect 里采样，避免渲染期调用 Date.now。
  const anchorMsRef = useRef(0)

  const activeFilters = useMemo<Record<string, unknown>>(() => {
    const filters: Record<string, unknown> = {}
    if (categoryFilter !== 'all') filters.category = categoryFilter
    if (taskFilter !== 'all') filters.task_type = taskFilter
    if (levelFilter !== 'all') filters.level = levelFilter
    if (sourceFilter !== 'all') filters.source = sourceFilter
    if (resolvedFilter === 'unresolved') filters.resolved = false
    else if (resolvedFilter === 'resolved') filters.resolved = true
    // timeRange 只参与查询身份（换新对象触发重新请求）。start_date 在 effect 中采样，
    // 由 withSampledStartDate 在发起请求时附上。
    if (timeRange < 0) filters.time_range_invalid = true
    if (keyword) filters.keyword = keyword
    return filters
  }, [categoryFilter, taskFilter, levelFilter, sourceFilter, resolvedFilter, timeRange, keyword])

  useEffect(() => {
    anchorMsRef.current = Date.now()
    sampledStartDate = timeRange > 0
      ? new Date(anchorMsRef.current - timeRange * 3600 * 1000).toISOString()
      : undefined
  }, [categoryFilter, taskFilter, levelFilter, sourceFilter, resolvedFilter, timeRange, keyword])

  const hasFilters =
    categoryFilter !== 'all' ||
    taskFilter !== 'all' ||
    levelFilter !== 'all' ||
    sourceFilter !== 'all' ||
    resolvedFilter !== 'all' ||
    timeRange !== DEFAULT_TIME_RANGE ||
    Boolean(keyword)

  const handleSearch = useCallback(() => {
    setKeyword(searchInput.trim())
  }, [searchInput])

  const resetFilters = useCallback(() => {
    setCategoryFilter('all')
    setTaskFilter('all')
    setLevelFilter('all')
    setSourceFilter('all')
    setResolvedFilter('all')
    setTimeRange(DEFAULT_TIME_RANGE)
    setKeyword('')
    setSearchInput('')
  }, [])

  return {
    categoryFilter, taskFilter, levelFilter, sourceFilter, resolvedFilter,
    timeRange, keyword, searchInput,
    setCategoryFilter, setTaskFilter, setLevelFilter, setSourceFilter, setResolvedFilter,
    setTimeRange, setSearchInput,
    activeFilters, currentTaskOptions, hasFilters,
    handleSearch, resetFilters,
  }
}
