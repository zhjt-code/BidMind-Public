import React from 'react'
import { ResizablePercentPane } from './ResizablePercentPane'
import {
  RESULT_LEFT_PANE_MAX_PERCENT,
  RESULT_LEFT_PANE_MIN_PERCENT,
} from './resultMiddlePaneWidth'

interface ResizableLeftPaneProps {
  defaultWidthPercent: number
  children: React.ReactNode
}

/**
 * 职责: 结果页左栏按右缘拖拽改宽，默认保持调用方给定的百分比
 * 不做: 不改中栏、不持久化宽度、不渲染导航内容
 */
export const ResizableLeftPane: React.FC<ResizableLeftPaneProps> = ({
  defaultWidthPercent,
  children,
}) => (
  <ResizablePercentPane
    defaultWidthPercent={defaultWidthPercent}
    minPercent={RESULT_LEFT_PANE_MIN_PERCENT}
    maxPercent={RESULT_LEFT_PANE_MAX_PERCENT}
    ariaLabel="调整导航宽度"
    testId="result-left-pane-resizer"
    extraClassName="border-r"
  >
    {children}
  </ResizablePercentPane>
)
