/**
 * 职责: 卡片标题行旁的说明按钮(悬停/聚焦显示文案)
 * 不做: 不解释业务、不持久化、不替代控件标签
 */
import React from 'react'
import { HelpCircle } from 'lucide-react'

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

export interface FieldHintButtonProps {
  /** 无障碍标签，须含完整提示，避免只靠悬停 */
  label: string
  children: React.ReactNode
}

const FieldHintButton: React.FC<FieldHintButtonProps> = ({ label, children }) => (
  <Tooltip>
    <TooltipTrigger asChild>
      <button
        type="button"
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-[rgba(28,39,51,0.06)] hover:text-[#1c2733] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        aria-label={label}
      >
        <HelpCircle className="h-[18px] w-[18px]" strokeWidth={2} aria-hidden="true" />
      </button>
    </TooltipTrigger>
    <TooltipContent className="max-w-xs text-left leading-relaxed">
      {children}
    </TooltipContent>
  </Tooltip>
)

export default FieldHintButton
