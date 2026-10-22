import { useCallback, useEffect, useMemo, useState } from 'react'
import { TASK_LABELS, TASK_BY_CATEGORY } from '../constants'

const DEFAULT_TIME_RANGE = 24

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

  useEffect(() => { setTaskFilter('all') }, [categoryFilter])

  const currentTaskOptions = useMemo(() => {
    if (categoryFilter === 'all') return TASK_LABELS
    return TASK_BY_CATEGORY[categoryFilter] || {}
  }, [categoryFilter])

  const activeFilters = useMemo<Record<string, unknown>>(() => {
    const filters: Record<string, unknown> = {}
    if (categoryFilter !== 'all') filters.category = categoryFilter
    if (taskFilter !== 'all') filters.task_type = taskFilter
    if (levelFilter !== 'all') filters.level = levelFilter
    if (sourceFilter !== 'all') filters.source = sourceFilter
    if (resolvedFilter === 'unresolved') filters.resolved = false
    else if (resolvedFilter === 'resolved') filters.resolved = true
    if (timeRange > 0) {
      filters.start_date = new Date(Date.now() - timeRange * 3600 * 1000).toISOString()
    }
    if (keyword) filters.keyword = keyword
    return filters
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
