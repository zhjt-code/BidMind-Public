import React from 'react'

export interface UserOnlineStats {
  onlineCount: number
  onlineWindowSeconds: number
  degraded: boolean
}

interface UserOnlineStatsCardProps {
  stats: UserOnlineStats | null
  loading: boolean
  className?: string
}

const UserOnlineStatsCard: React.FC<UserOnlineStatsCardProps> = ({
  stats,
  loading,
  className = '',
}) => {
  const windowMinutes = stats ? Math.ceil(stats.onlineWindowSeconds / 60) : 0
  const indicatorClass = stats?.degraded
    ? 'bg-amber-50 text-amber-700'
    : 'bg-green-50 text-green-700'
  const dotClass = stats?.degraded ? 'bg-amber-500' : 'bg-green-500'

  return (
    <div className={`bg-white rounded-lg shadow-sm p-4 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm text-gray-500">当前在线用户数</p>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-3xl font-bold text-gray-900">
              {loading && !stats ? '...' : stats?.onlineCount ?? 0}
            </span>
            <span className="text-sm text-gray-500">人</span>
          </div>
        </div>

        <div className="text-right">
          <div className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${indicatorClass}`}>
            <span className={`mr-2 h-2 w-2 rounded-full ${dotClass}`} />
            {stats?.degraded
              ? '在线统计降级'
              : stats
                ? `最近 ${windowMinutes} 分钟活跃`
                : '等待统计'}
          </div>
          {stats?.degraded && (
            <p className="mt-2 text-xs text-amber-600">
              Redis 在线状态暂不可用，统计已降级
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export default UserOnlineStatsCard
