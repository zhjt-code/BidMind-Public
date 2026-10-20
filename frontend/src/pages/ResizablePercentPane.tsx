import React, { useRef, useState } from 'react'
import { RESULT_PAGE_PANE_CLASS } from './resultPageLayout'
import { clampPanePercent, computePanePercent } from './resultMiddlePaneWidth'

interface ResizablePercentPaneProps {
  defaultWidthPercent: number
  minPercent: number
  maxPercent: number
  ariaLabel: string
  testId: string
  extraClassName?: string
  children: React.ReactNode
}

/**
 * 职责: 结果页栏按右缘拖拽改百分比宽度
 * 不做: 不持久化宽度、不渲染栏内业务内容、不协调左右栏互斥上限
 */
export const ResizablePercentPane: React.FC<ResizablePercentPaneProps> = ({
  defaultWidthPercent,
  minPercent,
  maxPercent,
  ariaLabel,
  testId,
  extraClassName,
  children,
}) => {
  const paneRef = useRef<HTMLDivElement>(null)
  const dragOriginRef = useRef<{ paneLeft: number; parentWidth: number } | null>(null)
  const [widthPercent, setWidthPercent] = useState(() =>
    clampPanePercent(defaultWidthPercent, minPercent, maxPercent),
  )

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const pane = paneRef.current
    const parent = pane?.parentElement
    if (!pane || !parent) return
    dragOriginRef.current = {
      paneLeft: pane.getBoundingClientRect().left,
      parentWidth: parent.getBoundingClientRect().width,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId) || !dragOriginRef.current) return
    const { paneLeft, parentWidth } = dragOriginRef.current
    setWidthPercent(
      computePanePercent(
        event.clientX,
        paneLeft,
        parentWidth,
        minPercent,
        maxPercent,
        defaultWidthPercent,
      ),
    )
  }

  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragOriginRef.current = null
  }

  return (
    <div
      ref={paneRef}
      className={`relative min-w-0 ${RESULT_PAGE_PANE_CLASS}${extraClassName ? ` ${extraClassName}` : ''}`}
      style={{ width: `${widthPercent}%` }}
    >
      {children}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={ariaLabel}
        aria-valuenow={Math.round(widthPercent)}
        aria-valuemin={minPercent}
        aria-valuemax={maxPercent}
        data-testid={testId}
        className="absolute top-0 right-0 z-10 h-full w-1 cursor-col-resize touch-none hover:bg-primary/30 active:bg-primary/50"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
    </div>
  )
}
