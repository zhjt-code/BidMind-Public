import React from 'react'
import { ChevronsLeft, ChevronsRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface ErrorLogPaginationProps {
  currentPage: number
  totalPages: number
  jumpPageInput: string
  onJumpPageInputChange: (v: string) => void
  onPageChange: (page: number) => void
  onJumpToPage: () => void
}

export const ErrorLogPagination: React.FC<ErrorLogPaginationProps> = ({
  currentPage, totalPages, jumpPageInput,
  onJumpPageInputChange, onPageChange, onJumpToPage,
}) => {
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
      <Button
        variant="outline" size="sm"
        disabled={currentPage <= 1}
        onClick={() => onPageChange(1)}
        title="首页"
      >
        <ChevronsLeft className="h-4 w-4" />
      </Button>
      <Button
        variant="outline" size="sm"
        disabled={currentPage <= 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        上一页
      </Button>
      <span className="text-sm text-gray-500">
        第 {currentPage} / {totalPages} 页
      </span>
      <div className="flex items-center gap-2">
        <Input
          value={jumpPageInput}
          inputMode="numeric"
          placeholder="页码"
          className="w-20 h-9"
          onChange={e => onJumpPageInputChange(e.target.value.replace(/\D/g, ''))}
          onKeyDown={e => e.key === 'Enter' && onJumpToPage()}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={!jumpPageInput || Number(jumpPageInput) === currentPage}
          onClick={onJumpToPage}
        >
          跳转
        </Button>
      </div>
      <Button
        variant="outline" size="sm"
        disabled={currentPage >= totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      >
        下一页
      </Button>
      <Button
        variant="outline" size="sm"
        disabled={currentPage >= totalPages}
        onClick={() => onPageChange(totalPages)}
        title="末页"
      >
        <ChevronsRight className="h-4 w-4" />
      </Button>
    </div>
  )
}
