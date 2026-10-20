/**
 * 职责: 监听指定滚动容器回顶，并按反馈 FAB 占位调整底部偏移。
 * 不做: 不感知业务域、不处理路由、不处理多容器协调、不控制反馈面板开闭。
 */
import React, { useCallback, useEffect, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import {
  FEEDBACK_FAB_VISIBILITY_EVENT,
  isFeedbackFabVisible,
  resolveBackToTopBottomClass,
} from '@/lib/feedbackFabVisibility'

export interface BackToTopProps {
  /** 滚动容器 ref；为空或 .current 为 null 时降级监听 window */
  scrollContainerRef?: React.RefObject<HTMLElement | null>
  /** 显示按钮的滚动阈值（像素），默认 300 */
  threshold?: number
  /** 额外类名，便于业务侧微调位置 */
  className?: string
}

const getScrollTop = (target: HTMLElement | Window): number => {
  if (target === window) return window.scrollY
  return (target as HTMLElement).scrollTop
}

export const BackToTop: React.FC<BackToTopProps> = ({
  scrollContainerRef,
  threshold = 300,
  className,
}) => {
  const [visible, setVisible] = useState(false)
  const [feedbackFabVisible, setFeedbackFabVisible] = useState(() =>
    isFeedbackFabVisible(),
  )

  useEffect(() => {
    const target: HTMLElement | Window = scrollContainerRef?.current ?? window
    let rafId = 0
    let pending = false

    const onScroll = () => {
      if (pending) return
      pending = true
      rafId = requestAnimationFrame(() => {
        setVisible(getScrollTop(target) > threshold)
        pending = false
      })
    }

    setVisible(getScrollTop(target) > threshold)
    target.addEventListener('scroll', onScroll, { passive: true })

    return () => {
      target.removeEventListener('scroll', onScroll)
      if (rafId) cancelAnimationFrame(rafId)
    }
  }, [scrollContainerRef, threshold])

  useEffect(() => {
    const syncFabSlot = () => setFeedbackFabVisible(isFeedbackFabVisible())
    window.addEventListener(FEEDBACK_FAB_VISIBILITY_EVENT, syncFabSlot)
    return () => {
      window.removeEventListener(FEEDBACK_FAB_VISIBILITY_EVENT, syncFabSlot)
    }
  }, [])

  const handleClick = useCallback(() => {
    const target: HTMLElement | Window = scrollContainerRef?.current ?? window
    const prefersReduced =
      typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    target.scrollTo({ top: 0, behavior: prefersReduced ? 'auto' : 'smooth' })
  }, [scrollContainerRef])

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label="回到顶部"
            tabIndex={visible ? 0 : -1}
            aria-hidden={!visible}
            onClick={handleClick}
            className={cn(
              'group fixed right-6 z-40 h-12 w-12 rounded-full p-0',
              resolveBackToTopBottomClass(feedbackFabVisible),
              'bg-white text-muted-foreground border border-border',
              'shadow-lg shadow-gray-900/[0.06]',
              'hover:bg-white hover:text-primary hover:border-primary/40',
              'hover:shadow-xl hover:shadow-primary/15',
              'motion-safe:hover:scale-105 motion-safe:active:scale-95',
              'transition-all duration-200 ease-out',
              visible
                ? 'opacity-100 motion-safe:translate-y-0 motion-safe:scale-100 pointer-events-auto'
                : 'opacity-0 motion-safe:translate-y-2 motion-safe:scale-90 pointer-events-none',
              className,
            )}
          >
            <ArrowUp
              className="h-5 w-5 motion-safe:transition-transform motion-safe:duration-200 motion-safe:group-hover:-translate-y-0.5"
              strokeWidth={2.5}
            />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="left" sideOffset={8}>回到顶部</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export default BackToTop
