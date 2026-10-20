/**
 * 职责: 评分核对条的常驻摘要、折叠开关和操作区。
 * 不做: 不计算通过或复核结论，不决定某个操作按钮是否出现。
 */
import React, { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  initialScoringDetailsCollapsed,
  shouldShowScoringDetails,
} from '../scoringBannerDetailsCollapse'

export interface ScoringBannerChromeProps {
  containerClass: string
  iconClass: string
  iconPath: string
  /** 展开后除主结论以外还有内容。 */
  hasDetails: boolean
  /** 存在必须先看到的差额或复核说明。 */
  attentionVisible: boolean
  headline: React.ReactNode
  details: React.ReactNode
  actions: React.ReactNode
  testId?: string
}

export const ScoringBannerChrome: React.FC<ScoringBannerChromeProps> = (props) => {
  const [collapsed, setCollapsed] = useState(() =>
    initialScoringDetailsCollapsed(props.attentionVisible),
  )

  useEffect(() => {
    if (props.attentionVisible) setCollapsed(false)
  }, [props.attentionVisible])

  const showDetails = shouldShowScoringDetails(collapsed, props.hasDetails)
  const toggleLabel = collapsed ? '展开明细' : '收起明细'

  return (
    <div className={props.containerClass} data-testid={props.testId}>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <svg
            className={props.iconClass}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d={props.iconPath}
            />
          </svg>
          <div className="min-w-0 flex-1">{props.headline}</div>
          {props.actions}
          {props.hasDetails && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 w-8 shrink-0 px-0"
              aria-expanded={showDetails}
              aria-label={toggleLabel}
              title={toggleLabel}
              onClick={() => setCollapsed((current) => !current)}
            >
              {collapsed ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronUp className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
        {showDetails ? props.details : null}
      </div>
    </div>
  )
}
