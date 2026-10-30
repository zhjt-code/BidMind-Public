import React from 'react'

interface SkeletonLineProps {
  width?: string   // Tailwind 宽度类，如 'w-full' / 'w-3/4' / 'w-1/2'
  height?: string  // Tailwind 高度类，如 'h-4' / 'h-6'
  className?: string
}

// 单行骨架
export const SkeletonLine: React.FC<SkeletonLineProps> = ({
  width = 'w-full',
  height = 'h-4',
  className = '',
}) => (
  <div
    className={`${width} ${height} bg-gray-200 rounded animate-pulse ${className}`}
  />
)

interface SkeletonCardProps {
  lines?: number   // 正文行数
  hasTitle?: boolean
  hasAction?: boolean
  className?: string
}

// 卡片骨架（标题 + 若干文字行 + 可选操作）
export const SkeletonCard: React.FC<SkeletonCardProps> = ({
  lines = 2,
  hasTitle = true,
  hasAction = false,
  className = '',
}) => (
  <div className={`p-4 ${className}`}>
    {hasTitle && <SkeletonLine width="w-1/3" height="h-5" className="mb-3" />}
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonLine
          key={i}
          width={i === lines - 1 ? 'w-3/4' : 'w-full'}
          height="h-4"
        />
      ))}
    </div>
    {hasAction && (
      <div className="flex gap-2 mt-3">
        <SkeletonLine width="w-16" height="h-7" />
        <SkeletonLine width="w-16" height="h-7" />
      </div>
    )}
  </div>
)

interface SkeletonListProps {
  rows?: number    // 列表行数
  className?: string
}

// 列表骨架（多行带左侧圆形头像）
export const SkeletonList: React.FC<SkeletonListProps> = ({
  rows = 5,
  className = '',
}) => (
  <div className={`divide-y divide-gray-100 ${className}`}>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="flex items-start gap-3 p-4">
        <div className="w-10 h-10 rounded-full bg-gray-200 animate-pulse shrink-0" />
        <div className="flex-1 space-y-2 pt-1">
          <SkeletonLine width="w-1/3" height="h-4" />
          <SkeletonLine width="w-2/3" height="h-3" />
        </div>
        <SkeletonLine width="w-16" height="h-6" />
      </div>
    ))}
  </div>
)

interface SkeletonStatCardsProps {
  count?: number
  className?: string
}

// 统计卡片骨架（用于 Result 页面汇总卡片）
export const SkeletonStatCards: React.FC<SkeletonStatCardsProps> = ({
  count = 4,
  className = '',
}) => (
  <div className={`grid gap-4 ${count === 4 ? 'grid-cols-4' : 'grid-cols-3'} ${className}`}>
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="bg-white rounded-lg shadow-sm p-4">
        <SkeletonLine width="w-1/2" height="h-8" className="mb-2" />
        <SkeletonLine width="w-3/4" height="h-3" />
      </div>
    ))}
  </div>
)

// 默认导出（通用 Skeleton，外部只需设置 className）
interface SkeletonProps {
  className?: string
}

const Skeleton: React.FC<SkeletonProps> = ({ className = '' }) => (
  <div className={`bg-gray-200 rounded animate-pulse ${className}`} />
)

export default Skeleton
