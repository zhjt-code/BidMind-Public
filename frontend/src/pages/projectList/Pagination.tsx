import React from 'react'
import { ChevronsLeft, ChevronsRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface PaginationProps {
  total: number
  pageSize: number
  currentPage: number
  onChange: (page: number) => void
}

const Pagination: React.FC<PaginationProps> = ({
  total,
  pageSize,
  currentPage,
  onChange,
}) => {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (total <= pageSize) return null

  const maxVisible = 5
  const pages: number[] = []
  if (totalPages <= maxVisible) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else if (currentPage <= 3) {
    for (let i = 1; i <= maxVisible; i++) pages.push(i)
  } else if (currentPage >= totalPages - 2) {
    for (let i = totalPages - maxVisible + 1; i <= totalPages; i++) pages.push(i)
  } else {
    for (let i = currentPage - 2; i <= currentPage + 2; i++) pages.push(i)
  }

  return (
    <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between">
      <p className="text-sm text-gray-600">
        共 <span className="font-medium">{total}</span> 个项目，
        第 <span className="font-medium">{currentPage}</span> / <span className="font-medium">{totalPages}</span> 页
      </p>
      <div className="flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="w-8 h-8 p-0"
          onClick={() => onChange(1)}
          disabled={currentPage <= 1}
          title="首页"
          aria-label="首页"
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange(Math.max(1, currentPage - 1))}
          disabled={currentPage <= 1}
        >
          上一页
        </Button>
        {pages.map((page) => (
          <Button
            key={page}
            variant={currentPage === page ? 'default' : 'outline'}
            size="sm"
            className="w-8 h-8 p-0"
            onClick={() => onChange(page)}
          >
            {page}
          </Button>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange(Math.min(totalPages, currentPage + 1))}
          disabled={currentPage >= totalPages}
        >
          下一页
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="w-8 h-8 p-0"
          onClick={() => onChange(totalPages)}
          disabled={currentPage >= totalPages}
          title="末页"
          aria-label="末页"
        >
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

export default Pagination
