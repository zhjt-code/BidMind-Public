/**
 * 职责: 结果页响应审查/文档质检工作区切换
 * 不做: 不读路由、不拉质检数据、不渲染三栏内容、不充当页头标题
 */
import React from 'react'

import FieldHintButton from '@/components/upload/FieldHintButton'
import SegmentedControl from '@/components/upload/SegmentedControl'
import {
  segmentedCountAnnouncement,
  segmentedOptionAriaLabel,
} from '@/components/upload/segmentedOptionAria'
import { parseResultView, type ResultView } from './resultViewParam'

export interface ResultViewSwitchProps {
  value: ResultView
  pendingCount: number
  onChange: (next: ResultView) => void
}

const RESULT_VIEW_LABEL = '结果视图'

const RESULT_VIEW_HINT =
  '响应审查对照招标要求判断符合度。文档质检检查结构、编号、版式等问题，不参与符合度结论。'

const ResultViewSwitch: React.FC<ResultViewSwitchProps> = ({
  value,
  pendingCount,
  onChange,
}) => {
  const qualityAnnouncement = segmentedCountAnnouncement(pendingCount, '项待处理')
  return (
    <div className="flex items-center gap-2 min-w-0">
      <SegmentedControl
        ariaLabel={RESULT_VIEW_LABEL}
        value={value}
        emphasized
        options={[
          { value: 'review', label: '响应审查' },
          {
            value: 'quality',
            label: '文档质检',
            ariaLabel: qualityAnnouncement
              ? segmentedOptionAriaLabel('文档质检', qualityAnnouncement)
              : undefined,
          },
        ]}
        onChange={(next) => onChange(parseResultView(next))}
      />
      <FieldHintButton label={RESULT_VIEW_HINT}>
        <p>{RESULT_VIEW_HINT}</p>
      </FieldHintButton>
    </div>
  )
}

export default ResultViewSwitch
