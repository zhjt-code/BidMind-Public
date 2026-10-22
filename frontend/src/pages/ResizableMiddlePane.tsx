import React from 'react'
import { ResizablePercentPane } from './ResizablePercentPane'
import {
  RESULT_MIDDLE_PANE_MAX_PERCENT,
  RESULT_MIDDLE_PANE_MIN_PERCENT,
} from './resultMiddlePaneWidth'

interface ResizableMiddlePaneProps {
  defaultWidthPercent: number
  children: React.ReactNode
}

/**
 * 职责: 结果页中栏按右缘拖拽改宽，默认保持调用方给定的百分比
 * 不做: 不改左栏、不持久化宽度、不渲染列表/详情内容
 */
export const ResizableMiddlePane: React.FC<ResizableMiddlePaneProps> = ({
  defaultWidthPercent,
  children,
}) => (
  <ResizablePercentPane
    defaultWidthPercent={defaultWidthPercent}
    minPercent={RESULT_MIDDLE_PANE_MIN_PERCENT}
    maxPercent={RESULT_MIDDLE_PANE_MAX_PERCENT}
    ariaLabel="调整列表宽度"
    testId="result-middle-pane-resizer"
  >
    {children}
  </ResizablePercentPane>
)
