/**
 * 职责: 截断展示标识性文本，悬停时用悬浮层显示全称
 * 不做: 不测量是否溢出、不改截断样式、不承担帮助说明
 */
import React from 'react'

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

export interface TruncatedHoverTextProps {
  text: string
  className?: string
}

const TruncatedHoverText: React.FC<TruncatedHoverTextProps> = ({ text, className }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <span className={className}>{text}</span>
    </TooltipTrigger>
    <TooltipContent side="top" className="z-80 max-w-lg break-all whitespace-normal">
      {text}
    </TooltipContent>
  </Tooltip>
)

export default TruncatedHoverText
