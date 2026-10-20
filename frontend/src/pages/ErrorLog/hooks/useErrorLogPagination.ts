import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

interface UseErrorLogPaginationOptions {
  viewMode: 'grouped' | 'flat'
  activeFilters: Record<string, unknown>
  groupSortBy: string
  totalPages: number
  groupTotalPages: number
}

export interface UseErrorLogPaginationResult {
  page: number
  groupPage: number
  jumpPageInput: string
  setJumpPageInput: (v: string) => void
  effectiveTotalPages: number
  handlePageChange: (nextPage: number) => void
  handleJumpToPage: () => void
}

export function useErrorLogPagination(opts: UseErrorLogPaginationOptions): UseErrorLogPaginationResult {
  const { viewMode, activeFilters, groupSortBy, totalPages, groupTotalPages } = opts

  const [page, setPage] = useState(1)
  const [groupPage, setGroupPage] = useState(1)
  const [jumpPageInput, setJumpPageInput] = useState('1')

  const effectiveTotalPages = viewMode === 'grouped' ? groupTotalPages : totalPages

  const handlePageChange = useCallback((nextPage: number) => {
    const safePage = Math.min(Math.max(nextPage, 1), Math.max(effectiveTotalPages, 1))
    if (viewMode === 'grouped') setGroupPage(safePage)
    else setPage(safePage)
  }, [effectiveTotalPages, viewMode])

  const handleJumpToPage = useCallback(() => {
    const targetPage = Number(jumpPageInput)
    if (!Number.isInteger(targetPage)) {
      toast.error('请输入有效页码')
      return
    }
    if (targetPage < 1 || targetPage > effectiveTotalPages) {
      toast.error(`页码范围为 1 - ${effectiveTotalPages}`)
      return
    }
    handlePageChange(targetPage)
  }, [handlePageChange, jumpPageInput, effectiveTotalPages])

  useEffect(() => {
    setPage(1)
    setGroupPage(1)
  }, [activeFilters, groupSortBy])

  useEffect(() => {
    setJumpPageInput(String(viewMode === 'grouped' ? groupPage : page))
  }, [page, groupPage, viewMode])

  return {
    page, groupPage, jumpPageInput,
    setJumpPageInput,
    effectiveTotalPages,
    handlePageChange, handleJumpToPage,
  }
}
