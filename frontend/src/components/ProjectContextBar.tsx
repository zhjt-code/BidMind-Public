import React, { useEffect, useRef, useState } from 'react'
import { CornerUpLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

const INITIAL_HIDE_MS = 3000
const HIDE_DELAY_MS = 700

function getProjectContextSeenKey(projectId?: number): string | null {
  return Number.isFinite(projectId) ? `project-context-seen:${projectId}` : null
}

interface ProjectContextBarProps {
  projectId?: number
  projectName?: string
  loading?: boolean
  onBackToList: () => void
}

const ProjectContextBar: React.FC<ProjectContextBarProps> = ({
  projectId,
  projectName,
  loading = false,
  onBackToList,
}) => {
  const [expanded, setExpanded] = useState(true)
  const hideTimerRef = useRef<number | null>(null)
  const initialHideTimerRef = useRef<number | null>(null)
  const displayName = projectName?.trim() || (loading ? '项目加载中...' : '未知项目')

  const clearHideTimer = () => {
    if (hideTimerRef.current) {
      window.clearTimeout(hideTimerRef.current)
      hideTimerRef.current = null
    }
  }

  const clearInitialHideTimer = () => {
    if (initialHideTimerRef.current) {
      window.clearTimeout(initialHideTimerRef.current)
      initialHideTimerRef.current = null
    }
  }

  const clearTimers = () => {
    clearHideTimer()
    clearInitialHideTimer()
  }

  const showBar = () => {
    clearTimers()
    setExpanded(true)
  }

  const scheduleHideBar = () => {
    clearTimers()
    hideTimerRef.current = window.setTimeout(() => {
      setExpanded(false)
      hideTimerRef.current = null
    }, HIDE_DELAY_MS)
  }

  const handleBlur = (event: React.FocusEvent<HTMLElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      scheduleHideBar()
    }
  }

  useEffect(() => {
    clearTimers()
    const seenKey = getProjectContextSeenKey(projectId)

    if (!seenKey) {
      setExpanded(true)
      initialHideTimerRef.current = window.setTimeout(() => {
        setExpanded(false)
        initialHideTimerRef.current = null
      }, INITIAL_HIDE_MS)
      return clearTimers
    }

    let hasSeen = false
    try {
      hasSeen = window.sessionStorage.getItem(seenKey) === '1'
    } catch {
      hasSeen = false
    }

    if (hasSeen) {
      setExpanded(false)
      return clearTimers
    }

    setExpanded(true)
    try {
      window.sessionStorage.setItem(seenKey, '1')
    } catch {
      // sessionStorage 不可用时仍保留本次首次展示行为。
    }
    initialHideTimerRef.current = window.setTimeout(() => {
      setExpanded(false)
      initialHideTimerRef.current = null
    }, INITIAL_HIDE_MS)

    return clearTimers
  }, [projectId])

  return (
    <section
      className="mb-1"
      onMouseEnter={showBar}
      onMouseLeave={scheduleHideBar}
      onFocus={showBar}
      onBlur={handleBlur}
    >
      <div
        className="h-3"
        tabIndex={0}
        aria-label="显示项目上下文操作"
      />

      <div
        className={`overflow-hidden transition-all duration-150 ${
          expanded ? 'max-h-9 translate-y-0 opacity-100' : 'max-h-0 -translate-y-1 opacity-0'
        }`}
      >
        <div className="flex min-h-8 items-center border-b border-gray-200/70 pb-1.5 text-sm">
          <Button
            variant="ghost"
            size="sm"
            onClick={onBackToList}
            className="h-7 flex-shrink-0 gap-1.5 px-2 text-gray-600 hover:text-gray-900"
            title="返回列表"
          >
            <CornerUpLeft className="h-4 w-4" />
            <span>返回列表</span>
          </Button>

          <div className="ml-auto flex min-w-0 items-center gap-1.5 pl-4">
            <span className="flex-shrink-0 text-xs text-gray-500">项目名称</span>
            <span className="truncate font-semibold text-gray-900" title={displayName}>
              {displayName}
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}

export default ProjectContextBar
