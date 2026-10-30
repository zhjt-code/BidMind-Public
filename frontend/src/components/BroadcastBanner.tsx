/**
 * 顶部广播横幅
 *
 * 设计要点：
 * - 默认仅渲染优先级最高的 1 条；critical 级强制叠加
 * - 颜色按 level 区分：info=蓝、success=绿、warning=琥珀、critical=红
 * - 用户点 X 关闭后写入服务端状态，session 内不再出现
 * - 整条点击跳转到 refUrl（X 按钮 stopPropagation）
 * - 多条同级时显示 "+N 条"，点击展开下拉
 */

import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Megaphone, AlertTriangle, CheckCircle2, X, ChevronDown } from 'lucide-react'
import type { BannerItem, BroadcastLevel } from '@/services/bannerApi'
import { useMessageStore } from '@/stores'

// ============ 样式映射 ============

interface LevelStyle {
  container: string
  iconColor: string
  Icon: React.ElementType
  priority: number
}

const LEVEL_STYLES: Record<BroadcastLevel, LevelStyle> = {
  critical: {
    container: 'bg-red-50 border-l-red-500 text-red-900',
    iconColor: 'text-red-600',
    Icon: AlertTriangle,
    priority: 0,
  },
  warning: {
    container: 'bg-amber-50 border-l-amber-500 text-amber-900',
    iconColor: 'text-amber-600',
    Icon: AlertTriangle,
    priority: 1,
  },
  info: {
    container: 'bg-blue-50 border-l-blue-500 text-blue-900',
    iconColor: 'text-blue-600',
    Icon: Megaphone,
    priority: 2,
  },
  success: {
    container: 'bg-emerald-50 border-l-emerald-500 text-emerald-900',
    iconColor: 'text-emerald-600',
    Icon: CheckCircle2,
    priority: 3,
  },
}

// ============ 单条 Banner Row ============

interface BannerRowProps {
  banner: BannerItem
  onDismiss: (id: number) => void
  onNavigate: (refUrl: string) => void
}

const BannerRow: React.FC<BannerRowProps> = ({ banner, onDismiss, onNavigate }) => {
  const style = LEVEL_STYLES[banner.level]
  const { Icon } = style
  const clickable = !!banner.refUrl

  const handleClick = () => {
    if (clickable) onNavigate(banner.refUrl as string)
  }

  const handleDismiss = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDismiss(banner.id)
  }

  return (
    <div
      className={`
        flex items-center gap-3 px-4 py-2.5 border-l-4 ${style.container}
        ${clickable ? 'cursor-pointer hover:brightness-95' : ''}
        transition-all
      `}
      onClick={handleClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
    >
      <Icon className={`h-4 w-4 shrink-0 ${style.iconColor}`} />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium truncate">{banner.title}</span>
          <span className="hidden sm:inline text-current/70 truncate">
            {banner.content.length > 80 ? `${banner.content.slice(0, 80)}...` : banner.content}
          </span>
        </div>
      </div>

      {clickable && (
        <span className="hidden md:inline text-xs underline decoration-dotted shrink-0">
          了解详情
        </span>
      )}

      {banner.dismissible && (
        <button
          onClick={handleDismiss}
          className="shrink-0 p-1 rounded hover:bg-black/5 transition-colors"
          title="关闭"
          aria-label="关闭横幅"
        >
          <X className="h-4 w-4 opacity-70" />
        </button>
      )}
    </div>
  )
}

// ============ 主组件 ============

const BroadcastBanner: React.FC = () => {
  const navigate = useNavigate()
  const { banners, dismissBanner } = useMessageStore()
  const [showAll, setShowAll] = useState(false)

  const handleDismiss = useCallback(
    async (id: number) => {
      await dismissBanner(id)
    },
    [dismissBanner],
  )

  const handleNavigate = useCallback(
    (refUrl: string) => {
      // 内部路径用 react-router；外链直接 window.open
      if (refUrl.startsWith('http://') || refUrl.startsWith('https://')) {
        window.open(refUrl, '_blank', 'noopener')
      } else {
        navigate(refUrl)
      }
    },
    [navigate],
  )

  // 关闭展开态：当 banners 全部消失时收起
  useEffect(() => {
    if (banners.length <= 1) setShowAll(false)
  }, [banners.length])

  if (banners.length === 0) return null

  // 排序：critical 永远叠加在最上方；其余按级别优先级
  const sorted = [...banners].sort(
    (a, b) => LEVEL_STYLES[a.level].priority - LEVEL_STYLES[b.level].priority,
  )

  // critical 级强制全部展示；其他默认仅展示最高优先级 1 条
  const criticalOnes = sorted.filter((b) => b.level === 'critical')
  const nonCritical = sorted.filter((b) => b.level !== 'critical')

  let visible: BannerItem[]
  if (showAll) {
    visible = sorted
  } else {
    visible = [...criticalOnes]
    if (nonCritical.length > 0) visible.push(nonCritical[0])
  }

  const hiddenCount = sorted.length - visible.length

  return (
    <div className="border-b border-black/5">
      {visible.map((banner) => (
        <BannerRow
          key={banner.id}
          banner={banner}
          onDismiss={handleDismiss}
          onNavigate={handleNavigate}
        />
      ))}

      {hiddenCount > 0 && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full flex items-center justify-center gap-1.5 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <ChevronDown className="h-3 w-3" />
          展开 {hiddenCount} 条更多通知
        </button>
      )}
    </div>
  )
}

export default BroadcastBanner
